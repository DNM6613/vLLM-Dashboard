"""Model lock watchdog: auto-restart a locked model after abnormal exit.

Semantics (2026-10-16, user-confirmed):
- Single lock at a time — enforced by ``state_machine.set_model_locked``
- Crash repair only: a manual stop of a locked model is NOT re-started;
  crash events are recorded solely by the status-sync transitions
  (RUNNING→STOPPED on positive evidence, LOADING→STOPPED with no vLLM
  process). The LOADING hard-cap reset (process present, port conflict)
  deliberately records no crash — restarting would fight an external vLLM.
- Covers AI-server restart and dashboard restart: a dashboard restart
  replays pre-restart RUNNING locked models as *synthetic* crash events
  (``state_machine.take_pending_locked_restarts``) — they schedule the
  restart attempt but do not count against the breaker (restarting the
  dashboard is not a model crash); an AI-server restart is detected by the
  next sync once the server is back.
- Never re-delivers the start command while a vLLM process is still alive
  on the remote: ``_attempt_restart`` probes remote presence first
  (``_check_vllm_present_strict``) because after a dashboard restart the
  in-memory status is the coerced STOPPED and the start_model 409 guard
  cannot see a live remote (audit 2026-10-17 P2-1).
- Breaker: 3 *real* crashes within 10 minutes suspends auto-restart and
  marks the model FAILED; observing the model LOADING/RUNNING again (e.g.
  manual start) or re-locking resets it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from fastapi import HTTPException

from ..config.model_launch_config import launch_config_manager
from ..controller.state_machine import state_machine
from ..schemas.model import ModelStatus
from . import model_service as _ms

logger = logging.getLogger(__name__)

RESTART_DELAY_SECONDS = 10.0
TICK_SECONDS = 30.0
BREAKER_WINDOW_SECONDS = 600.0
BREAKER_MAX_CRASHES = 3

SUSPEND_MESSAGE = (
    "自动启动已暂停：10 分钟内崩溃 {n} 次。手动启动或解除/重新锁定后恢复"
)

NO_START_COMMAND_MESSAGE = "自动启动失败：未配置启动命令（在启动配置中设置后手动启动）"

# model_id -> list of (timestamp, synthetic). Synthetic (replayed) events
# still schedule restart attempts but are excluded from the breaker count.
_crash_events: dict[str, list[tuple[datetime, bool]]] = {}
_inflight: set[str] = set()
_suspended: set[str] = set()
_restart_tasks: set[asyncio.Task] = set()
_task: asyncio.Task | None = None
_stopping = False


def _clear(model_id: str) -> None:
    _crash_events.pop(model_id, None)
    _suspended.discard(model_id)


def is_suspended(model_id: str) -> bool:
    return model_id in _suspended


def on_model_crash(model_id: str, synthetic: bool = False) -> None:
    """Record a crash of a locked model (status-sync transition hook).

    ``synthetic`` marks replayed pre-restart events: the event is still
    recorded (the tick needs it to retry a deferred start) but it does not
    count against the breaker — rapid dashboard restarts must not suspend
    auto-restart of a healthy model.
    """
    now = datetime.now()
    events = [(t, s) for t, s in _crash_events.get(model_id, [])
              if now - t < timedelta(seconds=BREAKER_WINDOW_SECONDS)]
    events.append((now, synthetic))
    _crash_events[model_id] = events

    real = [e for e in events if not e[1]]
    if len(real) >= BREAKER_MAX_CRASHES and model_id not in _suspended:
        _suspended.add(model_id)
        state_machine.update_model_status(
            model_id, ModelStatus.FAILED,
            error_message=SUSPEND_MESSAGE.format(n=len(real)),
        )
        logger.error(
            f"Model lock watchdog: {len(real)} crashes for {model_id} within "
            f"{BREAKER_WINDOW_SECONDS // 60} min — auto-restart suspended, "
            f"model marked FAILED")
        return

    try:
        task = asyncio.get_running_loop().create_task(_restart_after_delay(model_id))
    except RuntimeError:
        return  # no running loop (tests); the tick will pick the event up
    _restart_tasks.add(task)
    task.add_done_callback(_restart_tasks.discard)


async def _restart_after_delay(model_id: str) -> None:
    await asyncio.sleep(RESTART_DELAY_SECONDS)
    await _attempt_restart(model_id)


async def _deliver_start_command(command: str) -> None:
    """Send the start command to the persistent console PTY.

    The manual (browser) flow delivers the command itself — the frontend
    opens a console WebSocket in ``mode=start`` and sends it (see
    useConsoleWebSocket.startConsoleForModel). The watchdog has no client,
    so the backend must send it here: without this, every auto-start sets
    LOADING, the 10 s probe finds no vLLM process and rolls back, and the
    model flaps forever (incident 2026-10-17 10:15: ~65 undelivered
    attempts over 20 minutes until a manual start).
    """
    config = _ms.config_manager.get_config()
    session, _created = await asyncio.to_thread(
        _ms.console_session_manager.get_or_create, config)
    if session is None:
        logger.warning(
            "Model lock watchdog: no console session — start command "
            "not delivered (the probe rollback will retry)")
        return
    if not command.endswith("\n"):
        command += "\n"
    if session.send_command(command):
        logger.info("Model lock watchdog: start command delivered to console PTY")
    else:
        logger.warning(
            "Model lock watchdog: start command not delivered "
            "(PTY send window exhausted — the probe rollback will retry)")


async def _attempt_restart(model_id: str) -> None:
    model = state_machine.get_model(model_id)
    if model is None or not model.locked:
        _clear(model_id)
        return
    if model.status in (ModelStatus.RUNNING, ModelStatus.LOADING):
        _clear(model_id)
        return
    if model_id in _suspended or model_id in _inflight:
        return
    # P2-1 guard (audit 2026-10-17): after a dashboard restart the in-memory
    # status is the coerced STOPPED, so start_model's 409 check cannot see a
    # live remote. Probe first — under the single-lock design any vLLM
    # process on the remote is ours; if it is still alive, keep the event
    # (the tick's sync reconciles the status and clears it) and do not
    # re-deliver the start command (which may carry cleanup/kill logic).
    try:
        config = _ms.config_manager.get_config()
        target = _ms.resolve_api_target(config)
        present = await _ms._check_vllm_present_strict(config.host, target.port)
    except Exception as e:
        logger.warning(f"Model lock watchdog: presence probe failed for {model_id}: {e}")
        present = None
    if present is True:
        logger.info(
            f"Model lock watchdog: {model_id} still has a vLLM process on the "
            f"remote — skipping auto-start until sync reconciles the status")
        return
    _inflight.add(model_id)
    try:
        result = await _ms.start_model(model_id)
        logger.info(f"Model lock watchdog: auto-restarted locked model {model_id}")
        try:
            await _deliver_start_command(result.get("command", ""))
        except Exception as e:
            # Delivery failure must not kill the watchdog loop; the 10 s
            # probe rollback + re-arm retries the start on the next attempt.
            logger.warning(
                f"Model lock watchdog: start command delivery failed for "
                f"{model_id}: {e}")
    except HTTPException as e:
        if e.status_code == 409:
            _clear(model_id)
        elif e.status_code == 400 and "Start command" in str(e.detail):
            state_machine.update_model_status(
                model_id, ModelStatus.FAILED, error_message=NO_START_COMMAND_MESSAGE)
            _clear(model_id)
            logger.error(
                f"Model lock watchdog: {model_id} has no start command — "
                f"auto-restart cleared")
        else:
            logger.warning(
                f"Model lock watchdog: auto-restart of {model_id} deferred "
                f"({e.status_code}: {e.detail}) — will retry on next tick")
    finally:
        _inflight.discard(model_id)


async def watchdog_tick() -> None:
    # A-1 (audit 2026-10-17): only reconcile remote state when there is
    # something to repair — a locked model or a pending crash event — so an
    # idle dashboard (no lock) adds no 30 s remote load / log churn.
    if not _crash_events and not any(m.locked for m in state_machine.get_all_models()):
        return
    try:
        await _ms._sync_status_once(timeout=3.0)
    except Exception as e:
        logger.warning(f"Model lock watchdog: status sync failed: {e}")
    for model in state_machine.get_all_models():
        if model.locked and model.status in (ModelStatus.RUNNING, ModelStatus.LOADING):
            _clear(model.id)
    for model_id in list(_crash_events):
        model = state_machine.get_model(model_id)
        if model is None or not model.locked:
            _clear(model_id)
            continue
        await _attempt_restart(model_id)


async def _watchdog_loop() -> None:
    while not _stopping:
        await watchdog_tick()
        await asyncio.sleep(TICK_SECONDS)


async def start_watchdog() -> None:
    global _task, _stopping
    if _task is not None and not _task.done():
        return
    for model_id in state_machine.take_pending_locked_restarts():
        model = state_machine.get_model(model_id)
        if model is not None and model.locked:
            logger.info(
                f"Model lock watchdog: replaying pre-restart RUNNING for "
                f"locked model {model_id}")
            on_model_crash(model_id, synthetic=True)
    _stopping = False
    _task = asyncio.create_task(_watchdog_loop())
    logger.info(f"Model lock watchdog started (tick {TICK_SECONDS:.0f}s)")


async def stop_watchdog() -> None:
    global _task, _stopping
    _stopping = True
    for task in list(_restart_tasks):
        task.cancel()
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _task
        _task = None


def apply_model_lock(model_id: str, locked: bool) -> dict:
    model = state_machine.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    if locked:
        config = launch_config_manager.load_config(model_id)
        if not config or not config.get("start_command"):
            raise HTTPException(status_code=400, detail="Start command not configured")
    unlocked = state_machine.set_model_locked(model_id, locked)
    if not locked:
        _clear(model_id)
    return {"model_id": model_id, "locked": locked, "unlocked": unlocked}
