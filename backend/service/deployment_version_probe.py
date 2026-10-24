"""Live version discovery for the deployment tabs.

CUDA toolkit: read-only SSH probe of the NVIDIA apt repo index (which
cuda-toolkit-X.Y the repo actually offers). vLLM: the PyPI release list,
fetched from the dashboard backend (pypi.org first, the configured
mirror's JSON endpoint as fallback).

Both replace hard-coded knowledge: the CUDA tab used to offer a fixed
3-version list and the vLLM tab only "latest" or a hand-typed version, so
neither could see a new NVIDIA toolkit or a vLLM release without a
dashboard release.

Degradation is graceful and visible: a failed probe serves the static
fallback list (CUDA, source "fallback") or an empty list (vLLM — the
manual version input stays), never a broken tab.

Poll-safety: /cuda and /vllm are polled every 5 s while the modal is
open, so each result is TTL-cached and the producer holds its lock across
the fetch — a lost claim (a fetch already in flight) blocks in get() for
at most one fetch duration and then reads the fresh value, instead of
fanning the poll out into a fetch storm.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import httpx

from ..executor.remote.deployment_commands import (
    CUDA_REPO_VERSIONS_CMD,
    parse_cuda_repo_versions,
)
from .deployment_common import _CUDA_VERSIONS, CUDA_MIN_DRIVER

logger = logging.getLogger(__name__)

# Poll-driven endpoints: TTLs bound how often the upstream is really hit.
_CUDA_TTL = 15 * 60
_CUDA_NEGATIVE_TTL = 5 * 60
_VLLM_TTL = 60 * 60
_VLLM_NEGATIVE_TTL = 10 * 60
VLLM_MAX_LISTED = 20
PYPI_JSON_URL = "https://pypi.org/pypi/vllm/json"

_STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class _TtlCache:
    """Value + monotonic expiry. The producer claims the lock and holds it
    across the (slow) fetch; a lost claim means a fetch is already in
    flight, so the caller blocks in get() for at most one fetch duration
    and then reads the fresh value — no fetch storm from 5 s polls."""

    def __init__(self, ttl: float, negative_ttl: float):
        self._ttl = ttl
        self._negative_ttl = negative_ttl
        self._lock = threading.Lock()
        self._value: Any = None
        self._expires = 0.0

    def get(self) -> Any:
        with self._lock:
            return self._value if time.monotonic() < self._expires else None

    def claim(self) -> bool:
        return self._lock.acquire(blocking=False)

    def commit(self, value: Any, negative: bool = False) -> None:
        # Called while holding the lock (claim).
        self._value = value
        self._expires = time.monotonic() + (self._negative_ttl if negative else self._ttl)
        self._lock.release()


_cuda_cache = _TtlCache(_CUDA_TTL, _CUDA_NEGATIVE_TTL)
_vllm_cache = _TtlCache(_VLLM_TTL, _VLLM_NEGATIVE_TTL)


def reset_caches() -> None:
    """Test hook: drop both caches."""
    for cache in (_cuda_cache, _vllm_cache):
        with cache._lock:
            cache._value = None
            cache._expires = 0.0


def probe_cuda_repo_versions(executor) -> tuple[list[str], str]:
    """CUDA toolkits the NVIDIA apt repo offers (or the static list).

    Only versions with a CUDA_MIN_DRIVER entry are offered — toolkits
    without a documented minimum driver (a future version not yet
    indexed) would render as "—" rows with no compat check (user ruling
    2026-10-24). Order: newest first (the parse is ascending — reversed
    here).

    Returns (versions, source) with source "repo" (live index) or
    "fallback" (static list — probe failed, negative window active, or
    serving the cached value while another probe is in flight).
    """
    cached = _cuda_cache.get()
    if cached is not None:
        return cached
    if not _cuda_cache.claim():
        return _cuda_cache.get() or (list(_CUDA_VERSIONS), "fallback")
    try:
        result = executor.execute(CUDA_REPO_VERSIONS_CMD, timeout=60)
        versions = (parse_cuda_repo_versions(result.get("stdout") or "")
                    if result.get("success") else [])
    except Exception:
        logger.exception("CUDA repo version probe failed")
        versions = []
    versions = [v for v in versions if v in CUDA_MIN_DRIVER]
    versions.reverse()
    if versions:
        _cuda_cache.commit((versions, "repo"))
        return (versions, "repo")
    _cuda_cache.commit((list(_CUDA_VERSIONS), "fallback"), negative=True)
    return (list(_CUDA_VERSIONS), "fallback")


def cached_cuda_versions_or_fallback() -> list[str]:
    """/vllm's cuda_versions field: the cached live list when fresh, else
    the static list. Serves the fresh value after a bounded wait when a
    probe is in flight; never forces a probe itself."""
    cached = _cuda_cache.get()
    return list(cached[0]) if cached else list(_CUDA_VERSIONS)


def parse_vllm_releases(releases: dict[str, Any] | None) -> list[str]:
    """PyPI `releases` mapping -> stable versions, newest first, capped.

    Only clean X.Y.Z survives: pre-releases (rc/dev suffixes) and
    non-version keys would pollute the picker.
    """
    if not releases:
        return []
    stable = [v for v in releases if _STABLE_VERSION_RE.fullmatch(v)]
    stable.sort(key=lambda v: tuple(int(p) for p in v.split(".")), reverse=True)
    return stable[:VLLM_MAX_LISTED]


def _pypi_json_url(mirror: str) -> str | None:
    """Derive the JSON API endpoint from a configured simple-index mirror
    (full mirrors like tuna expose it); None for unrecognized shapes."""
    m = (mirror or "").strip().rstrip("/")
    if m.endswith("/simple"):
        return m[: -len("/simple")] + "/pypi/vllm/json"
    return None


def get_vllm_available_versions(mirror: str = "") -> list[str]:
    """Stable vLLM versions on PyPI (newest first, capped), or [] offline.

    pypi.org is authoritative; the configured mirror's JSON endpoint is
    the fallback (the install path uses that mirror, so a pypi.org outage
    must not blank the picker while the mirror still works).
    """
    cached = _vllm_cache.get()
    if cached is not None:
        return cached
    if not _vllm_cache.claim():
        return _vllm_cache.get() or []
    urls = [PYPI_JSON_URL]
    derived = _pypi_json_url(mirror)
    if derived and derived not in urls:
        urls.append(derived)
    versions: list[str] = []
    for url in urls:
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            versions = parse_vllm_releases((resp.json() or {}).get("releases"))
            if versions:
                break
        except Exception:
            logger.info("vLLM version probe failed: %s", url)
    if versions:
        _vllm_cache.commit(versions)
    else:
        _vllm_cache.commit([], negative=True)
    return versions
