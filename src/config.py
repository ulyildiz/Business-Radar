# -*- coding: utf-8 -*-
"""Single source of settings: .env loading merged with CLI overrides.

Single responsibility: configuration. This module makes no API calls and
WRITES no files — it only reads and validates.

Precedence:  CLI argument  >  .env file  >  environment variable  >  default
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

APP_NAME = "businessfind"
VERSION = "2.1.0"

# Variable names expected in .env (must match .env.example exactly).
ENV_CONTACT = "CONTACT_EMAIL"
ENV_TOMTOM = "TOMTOM_API_KEY"
ENV_LANGSEARCH = "LANGSEARCH_API_KEY"

USER_AGENT_TMPL = APP_NAME + "/{ver} (local business lead finder; contact: {contact})"

DEFAULT_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]

# Rate-limit defaults, in seconds.
DELAY_NOMINATIM = 1.1   # Nominatim policy: at most 1 request per second
DELAY_OVERPASS = 2.0
DELAY_TOMTOM = 0.25     # freemium, roughly 5 QPS

# LangSearch free tier: QPS=1, QPM=60, QPD=1000.
# 1.0 sits exactly on the boundary; any clock skew or latency jitter trips the
# limit immediately. 1.1 leaves a small but decisive margin. On a paid tier
# (Tier 1: QPS=5) this can be lowered with --langsearch-delay 0.25.
DELAY_LANGSEARCH = 1.1
LANGSEARCH_FREE_QPD = 1000

# THIS IS THE ACTUAL BINDING LIMIT.
# A 1.1s interval satisfies QPS=1 but works out to ~55 requests per minute;
# once retries are added it pins against the QPM=60 ceiling and 429s cluster.
# Measured behaviour: 28 requests / 29s = 58 per minute -> at the ceiling.
# We use 55 rather than 60 because the server's minute window does not start
# at the same instant as ours; running exactly at the ceiling means constantly
# scraping against it.
LANGSEARCH_FREE_QPM = 60
LANGSEARCH_PER_MINUTE = 55

LANGSEARCH_QUOTA_FILE = ".langsearch_quota_state.json"
# Layer 3 stops after this many consecutive CANDIDATE failures (FR-29).
# A 429 is no longer treated as a permanent fault: adaptive slowdown is in
# place, so the layer recovers on its own. Hence the high threshold — stopping
# early used to cut a healthy scan short and leave leads as 'not_checked'.
LANGSEARCH_MAX_CONSECUTIVE_FAILURES = 10

TOMTOM_DAILY_FREE_LIMIT = 2500
TOMTOM_MAX_LIMIT = 100          # maximum results per call (API limit)
TOMTOM_DEFAULT_CELL_M = 600     # grid cell radius in discover mode
TOMTOM_DEFAULT_LANGUAGE = "tr-TR"   # locale of returned business data


class ConfigError(Exception):
    """A setting is missing or invalid — fail loudly instead of continuing."""


# ---------------------------------------------------------------------------
# .env loading (stdlib only; no extra dependency such as python-dotenv)
# ---------------------------------------------------------------------------

def parse_env_text(text: str) -> Dict[str, str]:
    """Turn `KEY=value` lines into a dictionary.

    - lines starting with `#` are comments
    - blank lines are ignored
    - `export KEY=value` is accepted
    - surrounding single or double quotes are stripped from the value
    """
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def find_env_file(explicit: Optional[str] = None) -> Optional[str]:
    """Locate .env: --env-file first, then the cwd, then the project root."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(here), ".env"),  # project root, next to src/
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_env(explicit: Optional[str] = None) -> Dict[str, str]:
    """Read .env; return an empty dict if absent (this does NOT raise).

    Turning a missing file into an error happens later, in
    `Config.require_keys()`, once we know which keys are actually REQUIRED for
    this particular run.
    """
    path = find_env_file(explicit)
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_env_text(fh.read())
    except OSError as exc:
        raise ConfigError(f"Could not read .env ({path}): {exc}") from exc


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Every runtime setting in a single object."""

    # --- identity / keys ---
    contact: str = ""
    tomtom_key: str = ""
    langsearch_key: str = ""
    env_path: Optional[str] = None

    # --- search area ---
    radius_m: int = 2000
    include_unnamed: bool = False

    # --- layer switches ---
    skip_tomtom: bool = False
    skip_langsearch: bool = False
    tomtom_mode: str = "discover"

    # --- Overpass ---
    overpass_mirrors: List[str] = field(default_factory=lambda: list(DEFAULT_OVERPASS_MIRRORS))
    overpass_timeout: int = 180

    # --- TomTom ---
    tomtom_cell_radius_m: int = TOMTOM_DEFAULT_CELL_M
    tomtom_limit: int = TOMTOM_MAX_LIMIT
    tomtom_max_pages: int = 3
    tomtom_daily_limit: int = TOMTOM_DAILY_FREE_LIMIT
    tomtom_match_radius_m: int = 250
    tomtom_categories: Dict[str, str] = field(default_factory=dict)
    # Locale of the DATA TomTom returns (business names, addresses) — not the
    # UI language. Match it to the region being scanned so names come back the
    # way they are actually written on the shopfront.
    tomtom_language: str = TOMTOM_DEFAULT_LANGUAGE

    # --- LangSearch ---
    langsearch_count: int = 10
    langsearch_city: Optional[str] = None
    langsearch_daily_limit: int = LANGSEARCH_FREE_QPD
    langsearch_per_minute: int = LANGSEARCH_PER_MINUTE
    langsearch_quota_file: str = LANGSEARCH_QUOTA_FILE

    # --- matching ---
    merge_distance_m: float = 75.0
    name_threshold: float = 0.72
    min_token_len: int = 3

    # --- network ---
    delay_nominatim: float = DELAY_NOMINATIM
    delay_overpass: float = DELAY_OVERPASS
    delay_tomtom: float = DELAY_TOMTOM
    delay_langsearch: float = DELAY_LANGSEARCH
    max_retries: int = 3
    timeout_s: int = 60

    # --- output ---
    output_base: str = "leads"
    write_has_website: bool = True
    full_columns: bool = False

    @property
    def user_agent(self) -> str:
        return USER_AGENT_TMPL.format(ver=VERSION, contact=self.contact or "unset")

    @property
    def tomtom_enabled(self) -> bool:
        return bool(self.tomtom_key) and not self.skip_tomtom

    @property
    def langsearch_enabled(self) -> bool:
        return bool(self.langsearch_key) and not self.skip_langsearch

    # -----------------------------------------------------------------------

    def require_keys(self) -> None:
        """Are the required keys present? If not, stop with an EXPLICIT error.

        What counts as "required" depends on which layers are enabled for this
        run: with --skip-tomtom, no TomTom key is expected.
        """
        missing: List[str] = []
        if not self.contact:
            missing.append(ENV_CONTACT)
        if not self.skip_tomtom and not self.tomtom_key:
            missing.append(ENV_TOMTOM)
        if not self.skip_langsearch and not self.langsearch_key:
            missing.append(ENV_LANGSEARCH)
        if not missing:
            return

        raise ConfigError(_missing_keys_message(missing, self.env_path))


def _missing_keys_message(missing: Sequence[str], env_path: Optional[str]) -> str:
    """An error message that says what to DO — "missing" alone is not enough."""
    lines = ["Missing configuration: " + ", ".join(missing), ""]
    if env_path:
        lines.append(f".env was found ({env_path}) but the value(s) above are empty.")
        lines.append("Open the file and fill them in, for example:")
    else:
        lines.append("No .env file was found. Copy the example first:")
        lines.append("")
        lines.append("    cp .env.example .env")
        lines.append("")
        lines.append("Then open it and fill in the values, for example:")
    lines.append("")
    for key in missing:
        if key == ENV_CONTACT:
            lines.append(f"    {key}=you@your-agency.com")
        else:
            lines.append(f"    {key}=<key from the provider dashboard>")
    lines.append("")
    if ENV_CONTACT in missing:
        lines.append(f"{ENV_CONTACT} is mandatory: the Nominatim and Overpass usage")
        lines.append("policies require a descriptive User-Agent with contact details.")
    if ENV_TOMTOM in missing:
        lines.append("To run without the TomTom layer: --skip-tomtom")
    if ENV_LANGSEARCH in missing:
        lines.append("To run without the LangSearch layer: --skip-langsearch")
    lines.append("")
    lines.append("Keys can also be passed on the command line: "
                 "--tomtom-key / --langsearch-key / --contact")
    return "\n".join(lines)


def resolve_secret(cli_value: Optional[str], env_file: Dict[str, str], key: str) -> str:
    """Resolve one setting in order: CLI > .env file > environment variable."""
    if cli_value:
        return cli_value.strip()
    if env_file.get(key):
        return env_file[key].strip()
    return os.environ.get(key, "").strip()
