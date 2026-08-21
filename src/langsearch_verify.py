# -*- coding: utf-8 -*-
"""LAYER 3 — final cross-check via LangSearch.

Single responsibility: taking candidates that still look website-less and
checking them one last time with a general web search. This is NOT a
Maps/Places API — it cannot be used for discovery, only for verification.

RATE LIMIT NOTE
LangSearch free tier: QPS=1, QPM=60, QPD=1000. More than one request per
second returns 429 immediately, so the delay is applied PROACTIVELY rather
than reactively: we wait before each request (HttpClient._throttle), because
seeing a 429 and only then backing off is a reaction that comes too late.
The natural consequence is that a list of ~450 candidates takes 8-9 minutes.
That is not slowness; it is the price of the free tier.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import ENV_LANGSEARCH, LANGSEARCH_MAX_CONSECUTIVE_FAILURES, Config
from .console import log
from .http_client import HttpClient
from .models import FOUND_VIA_LANGSEARCH, Candidate
from .quota import DailyQuota, default_quota_path
from .text_utils import domain_matches_name, is_real_website

LANGSEARCH_URL = "https://api.langsearch.com/v1/web-search"
BUCKET = "langsearch"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _extract_pages(resp: requests.Response) -> List[Dict[str, Any]]:
    """Pull the result list out of the response, tolerating schema variations."""
    try:
        body = resp.json()
    except ValueError:
        return []
    if not isinstance(body, dict):
        return []

    data = body.get("data")
    if isinstance(data, dict):
        pages = data.get("webPages")
        if isinstance(pages, dict) and isinstance(pages.get("value"), list):
            return [p for p in pages["value"] if isinstance(p, dict)]
        if isinstance(data.get("results"), list):
            return [p for p in data["results"] if isinstance(p, dict)]

    pages = body.get("webPages")
    if isinstance(pages, dict) and isinstance(pages.get("value"), list):
        return [p for p in pages["value"] if isinstance(p, dict)]
    if isinstance(body.get("results"), list):
        return [p for p in body["results"] if isinstance(p, dict)]
    return []


def _find_own_domain(pages: List[Dict[str, Any]], name: str, min_token_len: int) -> str:
    """Do the results contain the business's OWN domain? Returns the URL if so."""
    for page in pages:
        url = page.get("url") or page.get("link") or ""
        if not url or not is_real_website(url):
            continue
        if domain_matches_name(url, name, min_token_len=min_token_len):
            return url
    return ""


def _build_query(name: str, city: str) -> str:
    return f'"{name}" "{city}"'.strip() if city else f'"{name}"'


def _query_and_charge(http: HttpClient, cfg: Config, name: str, city: str,
                      quota: DailyQuota) -> Optional[requests.Response]:
    """Send the request and bill any retries to the daily counter as well.

    The caller already reserved one unit; this adds the EXTRA attempts spent.
    """
    before = http.counts.get(BUCKET, 0)
    resp = _query(http, cfg, name, city)
    quota.charge(http.counts.get(BUCKET, 0) - before - 1)
    return resp


def _query(http: HttpClient, cfg: Config, name: str, city: str) -> Optional[requests.Response]:
    """A single search request. Delay and backoff are handled by HttpClient."""
    return http.request(
        "POST", LANGSEARCH_URL,
        bucket=BUCKET, delay=cfg.delay_langsearch,
        per_minute=cfg.langsearch_per_minute,
        headers={
            "Authorization": f"Bearer {cfg.langsearch_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "query": _build_query(name, city),
            "freshness": "noLimit",
            "summary": False,
            "count": cfg.langsearch_count,
        }),
    )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def human_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f} min"


def estimate_seconds(count: int, delay: float, per_minute: int = 0) -> float:
    """Estimated duration of Layer 3 (FR-30).

    Whichever of the two limits is slower decides: the per-request interval
    (QPS) or the per-minute ceiling (QPM). An estimate that looks only at the
    delay understates long runs, where the per-minute ceiling takes over.
    """
    count = max(0, count)
    by_delay = count * max(0.0, delay)
    by_window = (count * 60.0 / per_minute) if per_minute > 0 else 0.0
    return max(by_delay, by_window)


def _log_plan(count: int, cfg: Config, quota: DailyQuota) -> None:
    seconds = estimate_seconds(count, cfg.delay_langsearch, cfg.langsearch_per_minute)
    log(f"LAYER 3 — LangSearch cross-check ({count} candidates)")
    log(f"  Estimated time ~{human_duration(seconds)} — {cfg.delay_langsearch:.2f}s "
        f"between requests, per-minute cap {cfg.langsearch_per_minute}. "
        f"Daily quota: {quota.status()}")
    if quota.enabled and count > quota.remaining:
        log(f"  Today's remaining allowance ({quota.remaining}) is below the "
            f"candidate count. The last {count - quota.remaining} records will "
            f"be written as 'not_checked'.", level="warn")


def _fatal_reason(http: HttpClient) -> str:
    """Conditions where retrying is POINTLESS — stop without waiting.

    An invalid key gives the same answer for every candidate; spending ten
    candidates to reach the same conclusion only wastes time and daily quota.
    """
    status = http.last_status.get(BUCKET)
    if status in (401, 403):
        return f"API key rejected (HTTP {status})"
    return ""


def _breaker_reason(http: HttpClient) -> str:
    status = http.last_status.get(BUCKET)
    if status == 429:
        return "repeated rate limiting (429)"
    if status:
        return f"repeated HTTP {status}"
    return "repeated failure to get a response"


def _report_breaker(reason: str, cfg: Config) -> None:
    log(f"Layer 3 stopped: {reason}. Remaining candidates will be written as "
        f"'not_checked' without verification.", level="warn")
    if "API key" in reason:
        log(f"  Check {ENV_LANGSEARCH} in your .env. To disable this layer: "
            f"--skip-langsearch", level="warn")
    elif "429" in reason:
        log(f"  Adaptive slowdown was active and did not resolve it. Lower the "
            f"per-minute cap: --langsearch-per-minute "
            f"{max(10, cfg.langsearch_per_minute - 15)} "
            f"(currently {cfg.langsearch_per_minute}).", level="warn")


def _log_result(still: List[Candidate], found: List[Candidate], quota: DailyQuota,
                http: HttpClient) -> None:
    unverified = sum(1 for c in still if not c.langsearch_checked)
    extra = http.extra_delay(BUCKET)
    tail = (f" Finished with +{extra:.1f}s of added delay due to rate limiting."
            if extra else "")
    log(f"LAYER 3 result: {len(still)} final leads ({unverified} unverified), "
        f"{len(found)} records turned out to have a site. "
        f"Quota: {quota.status()}.{tail}", level="ok")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _handle_response(resp: requests.Response, candidate: Candidate,
                     cfg: Config) -> bool:
    """Evaluate the response; True if the business's own domain was found."""
    candidate.langsearch_checked = True
    hit = _find_own_domain(_extract_pages(resp), candidate.name, cfg.min_token_len)
    if not hit:
        return False
    candidate.mark_has_website(hit, FOUND_VIA_LANGSEARCH,
                               "own domain found via LangSearch")
    return True


def verify_with_langsearch(http: HttpClient, cfg: Config, candidates: List[Candidate],
                           city: str) -> Tuple[List[Candidate], List[Candidate]]:
    """Check candidates one last time -> (still site-less, found to have a site).

    On quota exhaustion, repeated errors, or an unnamed record, the candidate
    is NOT dropped; it stays in the lead list with an "unverified" note (FR-29).
    """
    still_no_website: List[Candidate] = []
    found_website: List[Candidate] = []
    quota = DailyQuota(cfg.langsearch_daily_limit,
                       default_quota_path(cfg.langsearch_quota_file),
                       label="LangSearch")
    _log_plan(len(candidates), cfg, quota)

    stop_reason = ""
    failures = 0

    for index, candidate in enumerate(candidates, 1):
        reason = stop_reason or ("unnamed record" if not candidate.name else "")
        if not reason and not quota.take():
            stop_reason = reason = "daily quota exhausted"
        if reason:
            candidate.mark_unverified(reason)
            still_no_website.append(candidate)
            continue

        resp = _query_and_charge(http, cfg, candidate.name, city, quota)
        if resp is None:
            failures += 1
            candidate.mark_unverified("request failed")
            still_no_website.append(candidate)
            stop_reason = _fatal_reason(http)
            if not stop_reason and failures >= LANGSEARCH_MAX_CONSECUTIVE_FAILURES:
                stop_reason = _breaker_reason(http)
            if stop_reason:
                _report_breaker(stop_reason, cfg)
            continue

        failures = 0
        (found_website if _handle_response(resp, candidate, cfg)
         else still_no_website).append(candidate)
        if index % 25 == 0:
            log(f"  ... {index}/{len(candidates)} processed "
                f"(quota {quota.status()})", level="dbg")

    _log_result(still_no_website, found_website, quota, http)
    return still_no_website, found_website
