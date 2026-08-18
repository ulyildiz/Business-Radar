# -*- coding: utf-8 -*-
"""KATMAN 3 — LangSearch ile son capraz kontrol.

Tek sorumluluk: hala "website yok" gorunen adaylari genel web aramasiyla
son bir kez kontrol etmek. Bu bir Maps/Places API'si DEGIL — kesif icin
kullanilamaz, sadece dogrulama icindir.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import requests

from .config import Config
from .console import log
from .http_client import HttpClient
from .models import FOUND_VIA_LANGSEARCH, Candidate
from .text_utils import domain_matches_name, is_real_website

LANGSEARCH_URL = "https://api.langsearch.com/v1/web-search"
BUCKET = "langsearch"


def _extract_pages(resp: requests.Response) -> List[Dict[str, Any]]:
    """Yanittan sonuc listesini cikarir (sema varyasyonlarina toleransli)."""
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
    """Sonuclarda isletmenin KENDI alan adi var mi? Varsa URL'yi doner."""
    for page in pages:
        url = page.get("url") or page.get("link") or ""
        if not url or not is_real_website(url):
            continue
        if domain_matches_name(url, name, min_token_len=min_token_len):
            return url
    return ""


def _build_query(name: str, city: str) -> str:
    return f'"{name}" "{city}"'.strip() if city else f'"{name}"'


def verify_with_langsearch(http: HttpClient, cfg: Config, candidates: List[Candidate],
                           city: str) -> Tuple[List[Candidate], List[Candidate]]:
    """Adaylari son kez kontrol eder -> (hala sitesiz, sitesi bulunanlar)."""
    still_no_website: List[Candidate] = []
    found_website: List[Candidate] = []

    log(f"KATMAN 3 — LangSearch capraz kontrolu ({len(candidates)} aday)")

    for index, candidate in enumerate(candidates, 1):
        if not candidate.name:
            candidate.add_note("LangSearch atlandi (isimsiz)")
            still_no_website.append(candidate)
            continue

        resp = http.request(
            "POST", LANGSEARCH_URL,
            bucket=BUCKET, delay=cfg.delay_langsearch,
            headers={
                "Authorization": f"Bearer {cfg.langsearch_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps({
                "query": _build_query(candidate.name, city),
                "freshness": "noLimit",
                "summary": False,
                "count": cfg.langsearch_count,
            }),
        )
        if resp is None:
            candidate.add_note("LangSearch yanit vermedi")
            still_no_website.append(candidate)
            continue

        candidate.langsearch_checked = True
        hit = _find_own_domain(_extract_pages(resp), candidate.name, cfg.min_token_len)
        if hit:
            candidate.mark_has_website(hit, FOUND_VIA_LANGSEARCH,
                                       "LangSearch'te kendi alan adi bulundu")
            found_website.append(candidate)
        else:
            still_no_website.append(candidate)

        if index % 25 == 0:
            log(f"  ... {index}/{len(candidates)} islendi", level="dbg")

    log(f"KATMAN 3 sonucu: {len(still_no_website)} nihai lead, "
        f"{len(found_website)} kayitta site bulundu.", level="ok")
    return still_no_website, found_website
