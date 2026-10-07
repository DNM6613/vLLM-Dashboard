"""Wall-clock bound for blocking remote (SSH) operations.

The executor's per-command timeout only covers opening the SSH channel —
reading the output is unbounded, so a stalled remote (e.g. the AI server's
sshd intermittently not sending its protocol banner) can hold a read
endpoint, and its worker thread, indefinitely. :func:`probe_in_thread`
runs the blocking call in a worker thread and caps the await: on timeout
the awaiter is released (the worker thread is abandoned) so the API can
answer 504 / a degraded payload instead of hanging forever and leaking
one pool worker per request.
"""
import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class RemoteProbeTimeout(Exception):
    """A remote probe exceeded its wall-clock bound."""


async def probe_in_thread(fn: Callable[..., Any], timeout: float,
                          op: str = "remote probe") -> Any:
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
    except TimeoutError as e:
        logger.warning(
            "Remote operation %r exceeded %.0fs wall-clock; "
            "abandoning its worker thread", op, timeout)
        raise RemoteProbeTimeout(f"{op} exceeded {timeout:.0f}s wall-clock") from e
