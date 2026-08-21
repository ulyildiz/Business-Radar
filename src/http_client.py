# -*- coding: utf-8 -*-
"""Rate-limit-aware HTTP client with retries.

Single responsibility: the network layer. It does not know which endpoint is
being called — each source module knows that for itself. Requests are issued
strictly one at a time (no parallelism).
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests

from .config import Config
from .console import log

MAX_BACKOFF_S = 60.0
RETRYABLE = (429, 502, 503, 504)
AUTH_FAILURE = (401, 403)

# Length of the rolling per-minute (QPM) window.
WINDOW_S = 60.0

# On a 429 the bucket's base delay grows by this much, and creeps back down
# after this many successful requests. A fixed delay alone is not enough: the
# real limit lives on the server and shifts over time, so the only correct
# response is to adapt to the measured behaviour.
RATE_LIMIT_PENALTY_S = 0.3
MAX_EXTRA_DELAY_S = 3.0
RECOVER_AFTER_OK = 20


def retry_after_seconds(value: str) -> Optional[float]:
    """Convert a `Retry-After` header to seconds; None if unparseable.

    The header comes in two forms: a number of seconds ("5", "1.5") or an
    HTTP-date ("Wed, 21 Oct 2026 07:28:00 GMT"). Checking only `str.isdigit()`
    silently drops both the date form and fractional values — meaning we would
    guess instead of using the delay the server actually asked for.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class HttpClient:
    """Single-threaded client with per-bucket throttling and retries."""

    def __init__(self, cfg: Config):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.user_agent,
            "Accept": "application/json",
        })
        self.max_retries = cfg.max_retries
        self.timeout = cfg.timeout_s
        self._last_call: Dict[str, float] = {}
        self._window: Dict[str, List[float]] = {}
        self._extra_delay: Dict[str, float] = {}
        self._ok_streak: Dict[str, int] = {}
        self.counts: Dict[str, int] = {}
        # Last HTTP status seen per bucket, so callers can answer "why did this
        # fail" (was it a 429 or a 500?) rather than just seeing None.
        self.last_status: Dict[str, int] = {}

    # -----------------------------------------------------------------------

    def _throttle(self, bucket: str, delay: float, per_minute: int) -> None:
        """Wait BEFORE sending. Two separate limits are enforced together.

        1) Minimum interval between requests (QPS).
        2) Total requests within the trailing 60 seconds (QPM).

        The second is essential: looking only at the interval cannot see the
        extra requests produced by retries. A 1.1s interval means one request
        per second, but it also means ~55 requests per minute; add a few
        retries and the per-minute ceiling is silently exceeded, at which point
        429s start to cluster.
        """
        wait = max(self._spacing_wait(bucket, delay), self._window_wait(bucket, per_minute))
        if wait > 0:
            time.sleep(wait)
        now = time.monotonic()
        self._last_call[bucket] = now
        if per_minute > 0:
            self._window.setdefault(bucket, []).append(now)

    def _spacing_wait(self, bucket: str, delay: float) -> float:
        last = self._last_call.get(bucket)
        if last is None:
            return 0.0
        target = delay + self._extra_delay.get(bucket, 0.0)
        return target - (time.monotonic() - last)

    def _window_wait(self, bucket: str, per_minute: int) -> float:
        """If the rolling 60s window is full, wait until a slot frees up."""
        if per_minute <= 0:
            return 0.0
        now = time.monotonic()
        stamps = self._window.setdefault(bucket, [])
        stamps[:] = [t for t in stamps if now - t < WINDOW_S]
        if len(stamps) < per_minute:
            return 0.0
        return WINDOW_S - (now - stamps[0]) + 0.05

    def _slow_down(self, bucket: str) -> None:
        """After a 429, slow this bucket down persistently — not just this call."""
        current = self._extra_delay.get(bucket, 0.0)
        updated = min(current + RATE_LIMIT_PENALTY_S, MAX_EXTRA_DELAY_S)
        self._extra_delay[bucket] = updated
        self._ok_streak[bucket] = 0
        if updated > current:
            log(f"{bucket}: rate limit hit, request interval raised by "
                f"+{updated:.1f}s", level="dbg")

    def _speed_up(self, bucket: str) -> None:
        """Roll the penalty back gradually once things run cleanly again."""
        current = self._extra_delay.get(bucket, 0.0)
        if current <= 0.0:
            return
        streak = self._ok_streak.get(bucket, 0) + 1
        if streak < RECOVER_AFTER_OK:
            self._ok_streak[bucket] = streak
            return
        self._extra_delay[bucket] = max(0.0, current - RATE_LIMIT_PENALTY_S / 2)
        self._ok_streak[bucket] = 0

    def extra_delay(self, bucket: str) -> float:
        """The adaptively added delay — exposed for reporting."""
        return self._extra_delay.get(bucket, 0.0)

    def _backoff_seconds(self, resp: requests.Response, attempt: int) -> float:
        """How long to wait: the server's answer first, else exponential+jitter.

        Jitter is REQUIRED (FR-27): requests that wait a fixed delay all come
        back at the same instant and trip the limit again. A random margin
        spreads the retries out and breaks that loop.
        """
        told = retry_after_seconds(resp.headers.get("Retry-After", ""))
        if told is not None:
            return min(told, MAX_BACKOFF_S)
        return min(2 ** attempt + random.uniform(0.0, 1.0), MAX_BACKOFF_S)

    def request(
        self,
        method: str,
        url: str,
        *,
        bucket: str,
        delay: float,
        per_minute: int = 0,
        timeout: Optional[int] = None,
        **kwargs: Any,
    ) -> Optional[requests.Response]:
        """Return the successful response, or None if every attempt failed."""
        for attempt in range(1, self.max_retries + 1):
            self._throttle(bucket, delay, per_minute)
            self.counts[bucket] = self.counts.get(bucket, 0) + 1

            try:
                resp = self.session.request(method, url, timeout=timeout or self.timeout, **kwargs)
            except requests.RequestException as exc:
                self.last_status[bucket] = 0  # network error: no HTTP status
                log(f"{bucket}: request failed ({attempt}/{self.max_retries}): {exc}",
                    level="warn")
                time.sleep(min(2 ** attempt + random.uniform(0.0, 1.0), 20))
                continue

            self.last_status[bucket] = resp.status_code
            if resp.status_code == 200:
                self._speed_up(bucket)
                return resp

            if resp.status_code in RETRYABLE:
                if resp.status_code == 429:
                    self._slow_down(bucket)
                wait = self._backoff_seconds(resp, attempt)
                source = "Retry-After" if resp.headers.get("Retry-After") else "backoff"
                log(f"{bucket}: HTTP {resp.status_code}, waiting {wait:.1f}s "
                    f"[{source}] ({attempt}/{self.max_retries})", level="warn")
                time.sleep(wait)
                continue

            if resp.status_code in AUTH_FAILURE:
                log(f"{bucket}: HTTP {resp.status_code} — API key invalid or "
                    f"unauthorized. Response: {resp.text[:200]}", level="err")
                return None

            log(f"{bucket}: HTTP {resp.status_code} — {resp.text[:200]}", level="warn")
            return None
        return None


class Budget:
    """Tracks the TomTom daily request ceiling to prevent silent overruns."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._reported = False

    def take(self) -> bool:
        """Reserve one request. Returns False once the quota is gone (warns once)."""
        if self.used >= self.limit:
            if not self._reported:
                log(f"TomTom quota exhausted ({self.limit} requests). Remaining "
                    f"records will continue without verification.", level="warn")
                self._reported = True
            return False
        self.used += 1
        return True

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)
