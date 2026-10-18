"""Background timer for post-reboot driver task verification.

The task center used to live on the home page, and its 3 s poll of
``GET /deployment/tasks`` was — outside the deploy window — the only thing
that ran :func:`reconcile_driver_task`. With the task center removed from
the home page, this background loop keeps the post-reboot verification
running on its own: every ``TICK_SECONDS`` it verifies a pending
``awaiting_reboot`` driver task and flips it to ``success`` once the new
driver is active.

``TICK_SECONDS`` matches the 30 s cooldown the old poll-driven hook used,
so the SSH probe load on an (usually down) host is unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from . import deployment_service as ds

logger = logging.getLogger(__name__)

TICK_SECONDS = 30.0

_task: asyncio.Task | None = None
_stopping = False


async def _tick() -> None:
    # ``reconcile_driver_task`` is a no-op (returns None) when there is no
    # pending driver task, so an idle dashboard adds no remote load.
    try:
        await asyncio.to_thread(ds.reconcile_driver_task)
    except Exception:
        logger.exception("Driver reconcile timer: reconcile_driver_task failed")


async def _loop() -> None:
    while not _stopping:
        await _tick()
        await asyncio.sleep(TICK_SECONDS)


async def start_driver_reconcile_timer() -> None:
    global _task, _stopping
    if _task is not None and not _task.done():
        return
    _stopping = False
    _task = asyncio.create_task(_loop())
    logger.info(f"Driver reconcile timer started (tick {TICK_SECONDS:.0f}s)")


async def stop_driver_reconcile_timer() -> None:
    global _task, _stopping
    _stopping = True
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _task
        _task = None
