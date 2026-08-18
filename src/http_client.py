# -*- coding: utf-8 -*-
"""Rate-limit'e saygili, tekrar denemeli HTTP istemcisi.

Tek sorumluluk: ag katmani. Hangi endpoint'in cagrildigini bilmez —
onu her kaynak modulu kendi bilir. Istekler tek sirada gider (paralel yok).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from .config import Config
from .console import log


class HttpClient:
    """Host/bucket bazli gecikmeli, tekrar denemeli, tek is parcacikli istemci."""

    def __init__(self, cfg: Config):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.user_agent,
            "Accept": "application/json",
        })
        self.max_retries = cfg.max_retries
        self.timeout = cfg.timeout_s
        self._last_call: Dict[str, float] = {}
        self.counts: Dict[str, int] = {}

    # -----------------------------------------------------------------------

    def _throttle(self, bucket: str, delay: float) -> None:
        """Ayni bucket'a arka arkaya istekte minimum bekleme suresini uygular."""
        last = self._last_call.get(bucket)
        if last is not None:
            wait = delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_call[bucket] = time.monotonic()

    def _backoff_seconds(self, resp: requests.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return float(retry_after)
        return min(2 ** attempt * 2, 60)

    def request(
        self,
        method: str,
        url: str,
        *,
        bucket: str,
        delay: float,
        timeout: Optional[int] = None,
        **kwargs: Any,
    ) -> Optional[requests.Response]:
        """Basarili yaniti dondurur; tum denemeler basarisizsa None."""
        for attempt in range(1, self.max_retries + 1):
            self._throttle(bucket, delay)
            self.counts[bucket] = self.counts.get(bucket, 0) + 1

            try:
                resp = self.session.request(method, url, timeout=timeout or self.timeout, **kwargs)
            except requests.RequestException as exc:
                log(f"{bucket}: istek hatasi ({attempt}/{self.max_retries}): {exc}", level="warn")
                time.sleep(min(2 ** attempt, 20))
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code in (429, 502, 503, 504):
                wait = self._backoff_seconds(resp, attempt)
                log(f"{bucket}: HTTP {resp.status_code}, {wait:.0f}sn bekleniyor "
                    f"({attempt}/{self.max_retries})", level="warn")
                time.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                log(f"{bucket}: HTTP {resp.status_code} — API anahtari gecersiz veya yetkisiz. "
                    f"Yanit: {resp.text[:200]}", level="err")
                return None

            log(f"{bucket}: HTTP {resp.status_code} — {resp.text[:200]}", level="warn")
            return None
        return None


class Budget:
    """TomTom gunluk istek tavanini takip eder; sessiz kota asimini engeller."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._reported = False

    def take(self) -> bool:
        """Bir istek hakki ayirir. Kota bittiyse False (ve bir kez uyarir)."""
        if self.used >= self.limit:
            if not self._reported:
                log(f"TomTom kotasi doldu ({self.limit} istek). Kalan kayitlar "
                    f"dogrulanmadan devam edecek.", level="warn")
                self._reported = True
            return False
        self.used += 1
        return True

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)
