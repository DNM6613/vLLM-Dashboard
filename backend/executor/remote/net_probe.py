"""Unprivileged ICMP echo probe + per-host 3-strike state for header lights.

The SSH/BMC header lights report the *network layer*, probed fresh on every
request (never from cache). A single dropped echo must not flip the light
red — the management link loses individual packets — so a host is only
declared ``down`` after :data:`DOWN_STRIKES` consecutive failed pings, and
any successful ping clears the streak immediately.

The DGRAM ICMP socket (``SOCK_DGRAM`` + ``IPPROTO_ICMP``) needs neither
``CAP_NET_RAW`` nor a ``ping`` binary: on Linux the kernel allocates the
echo id and demultiplexes replies to the sending socket. Environments that
forbid unprivileged ICMP sockets (e.g. Windows without admin rights)
surface an ``OSError`` — the probe treats that as one failed strike.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

DOWN_STRIKES = 3
PING_TIMEOUT = 1.0

_seq_lock = threading.Lock()
_seq = 0


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def icmp_ping(host: str, timeout: float = PING_TIMEOUT) -> float | None:
    """Send one ICMP echo to *host*; return the RTT in seconds, or ``None``.

    ``None`` = no reply before *timeout*, or the environment forbids
    unprivileged ICMP sockets. Callers record the outcome in a
    :class:`PingTracker` — one failed ping alone never means "down".
    """
    global _seq
    with _seq_lock:
        _seq = (_seq + 1) & 0xFFFF
        seq = _seq
    # ident MUST be 0: the kernel assigns a per-socket ident to the echo
    # request and only delivers replies carrying THAT ident to this socket
    # (verified live in production: a sent ident of 0x1234 came back as
    # 0x8fb0). Matching on the sent ident would never succeed — the
    # kernel's per-socket demux plus the seq check is the correct reply
    # discriminator.
    # Millisecond timestamp, masked to 32 bits (payload-only identifier —
    # the 2026+ epoch ms exceeds the uint32 range).
    payload = struct.pack("!II", int(time.time() * 1000) & 0xFFFFFFFF, 0)
    header = struct.pack("!BBHHH", 8, 0, 0, 0, seq)
    packet = header[:2] + struct.pack("!H", _checksum(header + payload)) + header[4:] + payload

    t0 = time.monotonic()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (host, 0))
            while True:
                # recv() (unlike recvfrom()) returns the raw datagram
                # bytes — verified live in the production container.
                data = sock.recv(1024)
                if len(data) < 8:
                    continue
                rtype, _rcode, _cks, _rid, rseq = struct.unpack("!BBHHH", data[:8])
                if rtype == 0 and rseq == seq:
                    return time.monotonic() - t0
    except OSError:
        # socket.timeout on expiry; PermissionError where the environment
        # forbids unprivileged ICMP; or a dropped host route.
        return None


class PingTracker:
    """Per-host consecutive-failure state feeding a light's network layer.

    ``up`` — last ping ok (streak 0); ``degraded`` — 1–2 consecutive
    failures (yellow); ``down`` — >= *down_strikes* consecutive failures
    (red). A success resets the streak to zero immediately.
    """

    def __init__(self, down_strikes: int = DOWN_STRIKES):
        self._down_strikes = down_strikes
        self._lock = threading.Lock()
        self._streak: dict[str, int] = {}
        self._rtt: dict[str, float] = {}

    def record(self, host: str, rtt: float | None) -> tuple[str, float | None]:
        """Feed one ping outcome; return ``(state, last_good_rtt)``."""
        with self._lock:
            streak = 0 if rtt is not None else self._streak.get(host, 0) + 1
            self._streak[host] = streak
            if rtt is not None:
                self._rtt[host] = rtt
            if streak == 0:
                state = "up"
            elif streak >= self._down_strikes:
                state = "down"
            else:
                state = "degraded"
            return state, self._rtt.get(host)

    def reset(self, host: str) -> None:
        with self._lock:
            self._streak.pop(host, None)
            self._rtt.pop(host, None)


ssh_ping = PingTracker()
bmc_ping = PingTracker()
