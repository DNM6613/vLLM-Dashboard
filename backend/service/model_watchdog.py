"""Model lock watchdog: auto-restart a locked model after abnormal exit.

Semantics (2026-10-16, user-confirmed):
- Single lock at a time — enforced by ``state_machine.set_model_locked``
- Crash repair only: a manual stop of a locked model is NOT re-started;
  crash events are recorded solely by the status-sync transitions
  (RUNNING→STOPPED on positive evidence, LOADING→STOPPED with no vLLM
  process). The LOADING hard-cap reset (process present, port conflict)
  deliberately records no crash — restarting would fight an external vLLM.
- Covers AI-server restart and dashboard restart: a dashboard restart
  replays pre-restart RUNNING locked models as crash events
  (``state_machine.take_pending_locked_restarts``); an AI-server restart
  is detected by the next sync once the server is back.
- Breaker: 3 crashes within 10 minutes suspends auto-restart and marks the
  model FAILED; observing the model LOADING/RUNNING again (e.g. manual
  start) or re-locking resets it.
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

_crash_events: dict[str, list[datetime]] = {}
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


def on_model_crash(model_id: str) -> None:
    """Record a crash of a locked model (status-sync transition hook)."""
    now = datetime.now()
    recent = [t for t in _crash_events.get(model_id, [])
              if now - t < timedelta(seconds=BREAKER_WINDOW_SECONDS)]
    recent.append(now)
    _crash_events[model_id] = recent

    if len(recent) >= BREAKER_MAX_CRASHES and model_id not in _suspended:
        _suspended.add(model_id)
        state_machine.update_model_status(
            model_id, ModelStatus.FAILED,
            error_message=SUSPEND_MESSAGE.format(n=len(recent)),
        )
        logger.error(
            f"Model lock watchdog: {len(recent)} crashes for {model_id} within "
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
    _inflight.add(model_id)
    try:
        await _ms.start_model(model_id)
        logger.info(f"Model lock watchdog: auto-restarted locked model {model_id}")
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
            on_model_crash(model_id)
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
