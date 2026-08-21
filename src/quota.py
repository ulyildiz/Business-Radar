# -*- coding: utf-8 -*-
"""Day-scoped request counter persisted to disk (FR-28).

Single responsibility: remembering "how many requests have I made today".
Makes no network calls and does not know which API it is counting — the label
is supplied by the caller.

WHY THIS IS WRITTEN TO DISK
A daily quota (QPD) covers the DAY, not the process. Several runs on the same
day (a test first, then the real scan) draw from the same pool. A counter kept
only in memory cannot see that; the limit is silently exceeded and the server
starts returning 429. At that point the error says "rate limit" while the real
cause is "the daily allowance is gone" — which makes it undiagnosable.
"""

from __future__ import annotations

import datetime
import json
import os

from .console import log


def today_str() -> str:
    return datetime.date.today().isoformat()


class DailyQuota:
    """A disk-backed counter that resets itself when the date changes.

    Passing `limit <= 0` DISABLES the counter (`take()` always returns True).
    That is the escape hatch for paid tiers, or for anyone who does not want
    the tracking.
    """

    def __init__(self, limit: int, path: str, *, label: str = "API",
                 warn_ratio: float = 0.9):
        self.limit = int(limit)
        self.path = path
        self.label = label
        self.warn_ratio = warn_ratio
        self.date = today_str()
        self.count = 0
        self.started_at = 0
        self._warned = False
        self._exhausted_reported = False
        self._io_warned = False
        self._load()
        self.started_at = self.count

    # -----------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    @property
    def remaining(self) -> int:
        if not self.enabled:
            return -1
        return max(0, self.limit - self.count)

    @property
    def used_this_run(self) -> int:
        return self.count - self.started_at

    # -----------------------------------------------------------------------

    def _load(self) -> None:
        """Read the state file. A missing or corrupt file is NOT an error.

        The counter is a convenience; aborting the scan because it could not be
        read would cause more harm than the thing it protects against. Start
        from a clean slate instead.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("date") != self.date:
            return  # a new day -> the counter starts from zero
        try:
            self.count = max(0, int(data.get("count", 0)))
        except (TypeError, ValueError):
            self.count = 0

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"date": self.date, "count": self.count}, fh)
        except OSError as exc:
            if not self._io_warned:
                log(f"Could not write the quota state ({self.path}): {exc}. The "
                    f"counter will only apply to this run.", level="warn")
                self._io_warned = True

    # -----------------------------------------------------------------------

    def take(self) -> bool:
        """Reserve one request. Returns False once the allowance is gone."""
        if not self.enabled:
            return True
        if self.count >= self.limit:
            self._report_exhausted()
            return False
        self.count += 1
        self._save()
        self._maybe_warn()
        return True

    def charge(self, extra: int) -> None:
        """Add requests spent outside of `take()` to the counter.

        Why this is needed: a candidate takes a single `take()`, but a failed
        request is retried up to `max_retries` times. The server deducts ALL of
        them from the daily allowance. Counting only take() would mean we
        cannot tell when the quota is genuinely exhausted — the protection
        would go quiet exactly when it matters.
        """
        if not self.enabled or extra <= 0:
            return
        self.count += extra
        self._save()
        self._maybe_warn()

    def _report_exhausted(self) -> None:
        if self._exhausted_reported:
            return
        self._exhausted_reported = True
        log(f"{self.label} daily quota exhausted ({self.limit} requests/day). "
            f"Remaining records will be marked 'not_checked' without "
            f"verification. The counter resets tomorrow ({self.path}).",
            level="warn")

    def _maybe_warn(self) -> None:
        if self._warned or self.count < self.limit * self.warn_ratio:
            return
        self._warned = True
        log(f"{self.label} daily quota is {100 * self.warn_ratio:.0f}% used "
            f"({self.count}/{self.limit}).", level="warn")

    def status(self) -> str:
        if not self.enabled:
            return "quota tracking disabled"
        return f"{self.count}/{self.limit} today, {self.used_this_run} this run"


def default_quota_path(filename: str) -> str:
    """Anchor a relative state-file name to the current working directory."""
    return filename if os.path.isabs(filename) else os.path.join(os.getcwd(), filename)
