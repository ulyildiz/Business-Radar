# -*- coding: utf-8 -*-
"""Command-line interface and entry point.

Single responsibility: declaring arguments, building the Config, and invoking
the pipeline. No business logic lives here.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, Optional, Sequence, Tuple

from . import config as config_module
from . import osm_source, output, tomtom_source
from .config import (
    APP_NAME,
    DELAY_LANGSEARCH,
    DELAY_TOMTOM,
    ENV_CONTACT,
    ENV_LANGSEARCH,
    ENV_TOMTOM,
    LANGSEARCH_FREE_QPD,
    LANGSEARCH_FREE_QPM,
    LANGSEARCH_PER_MINUTE,
    LANGSEARCH_QUOTA_FILE,
    TOMTOM_DAILY_FREE_LIMIT,
    TOMTOM_DEFAULT_CELL_M,
    TOMTOM_DEFAULT_LANGUAGE,
    TOMTOM_MAX_LIMIT,
    VERSION,
    Config,
    ConfigError,
)
from .console import enable_utf8, log, set_verbose
from .geo_utils import build_grid
from .http_client import HttpClient
from .langsearch_verify import estimate_seconds, human_duration
from .osm_source import BUSINESS_TYPES, TYPE_ALIASES, UnknownBusinessType, resolve_types
from .pipeline import resolve_center, run_and_report
from .quota import DailyQuota, default_quota_path

PROG = "python -m src.cli"


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def parse_latlng(value: str) -> Tuple[float, float]:
    parts = re.split(r"[,\s]+", value.strip())
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            "--latlng format: 'lat,lon' (e.g. 40.9903,29.0270)")
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError("--latlng must be numeric")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise argparse.ArgumentTypeError("--latlng is out of range")
    return lat, lon


def parse_category_overrides(raw: Optional[Sequence[str]]) -> Dict[str, str]:
    """Turn --tomtom-category TYPE=QUERY pairs into a dictionary."""
    overrides: Dict[str, str] = {}
    for item in raw or []:
        if "=" not in item:
            log(f"--tomtom-category ignored (expected TYPE=QUERY): {item}", level="warn")
            continue
        key, _, value = item.partition("=")
        canonical = key.strip().lower().replace(" ", "_")
        canonical = TYPE_ALIASES.get(canonical, canonical)
        overrides[canonical] = value.strip()
    return overrides


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Finds local businesses with and without websites within a "
                    "GPS radius (3 layers, free APIs only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "First-time setup:\n"
            "  cp .env.example .env      # then open .env and fill in your keys\n\n"
            "Example:\n"
            f'  {PROG} --address "Kadikoy, Istanbul" --radius 5000 \\\n'
            "      --types restaurant hair_salon car_repair \\\n"
            '      --tomtom-category car_repair="oto sanayi" \\\n'
            "      --output kadikoy_leads\n"
        ),
    )

    _add_target_arguments(parser)
    _add_key_arguments(parser)
    _add_layer_arguments(parser)
    _add_tuning_arguments(parser)
    _add_network_arguments(parser)
    _add_helper_mode_arguments(parser)
    return parser


def _add_target_arguments(parser: argparse.ArgumentParser) -> None:
    """Where to scan, how wide, which business types, and where to write."""
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--address", help="Center address (geocoded via Nominatim)")
    where.add_argument("--latlng", type=parse_latlng, help="Center coordinates: 'lat,lon'")

    parser.add_argument("--radius", type=int, default=2000,
                        help="Search radius in meters (default: 2000)")
    parser.add_argument("--types", nargs="+", default=["restaurant"],
                        help="One or more business types (or 'all'). See --list-types.")
    parser.add_argument("--output", "-o", default="leads",
                        help="Output base name. Produces <base>_no_website.csv, "
                             "<base>_has_website.csv, <base>_NOTES.txt (default: leads)")


def _add_key_arguments(parser: argparse.ArgumentParser) -> None:
    """Keys: read from .env when omitted, override .env when given (FR-24)."""
    keys = parser.add_argument_group("credentials (read from .env when omitted)")
    keys.add_argument("--contact",
                      help=f".env: {ENV_CONTACT} — contact details placed in the User-Agent")
    keys.add_argument("--tomtom-key", help=f".env: {ENV_TOMTOM}")
    keys.add_argument("--langsearch-key", help=f".env: {ENV_LANGSEARCH}")
    keys.add_argument("--env-file", help="Alternative path to the .env file")


def _add_layer_arguments(parser: argparse.ArgumentParser) -> None:
    """Layer toggles and layer-specific settings."""
    layers = parser.add_argument_group("layers")
    layers.add_argument("--skip-tomtom", action="store_true",
                        help="Skip the TomTom layer entirely")
    layers.add_argument("--skip-langsearch", action="store_true",
                        help="Skip the LangSearch layer")
    layers.add_argument("--tomtom-mode", choices=("discover", "verify"), default="discover",
                        help="discover: TomTom sweeps the area independently and ADDS "
                             "businesses missing from OSM (default). "
                             "verify: only checks existing candidates (saves quota).")
    layers.add_argument("--tomtom-cell-radius", type=int, default=TOMTOM_DEFAULT_CELL_M,
                        help=f"Grid cell radius in discover mode, meters "
                             f"(default: {TOMTOM_DEFAULT_CELL_M})")
    layers.add_argument("--tomtom-limit", type=int, default=TOMTOM_MAX_LIMIT,
                        help=f"Results per TomTom call (max {TOMTOM_MAX_LIMIT})")
    layers.add_argument("--tomtom-max-pages", type=int, default=3,
                        help="Maximum pages per cell (default: 3, about 300 results)")
    layers.add_argument("--tomtom-daily-limit", type=int, default=TOMTOM_DAILY_FREE_LIMIT,
                        help=f"TomTom daily request ceiling (default: {TOMTOM_DAILY_FREE_LIMIT})")
    layers.add_argument("--tomtom-match-radius", type=int, default=250,
                        help="Search radius around a candidate in verify mode, meters "
                             "(default: 250)")
    layers.add_argument("--tomtom-category", action="append", metavar="TYPE=QUERY",
                        help='Override the TomTom category text per type '
                             '(e.g. --tomtom-category car_repair="oto sanayi")')
    layers.add_argument("--tomtom-language", default=TOMTOM_DEFAULT_LANGUAGE,
                        help=f"Locale of the business data TomTom returns, not the UI "
                             f"language (default: {TOMTOM_DEFAULT_LANGUAGE})")
    layers.add_argument("--langsearch-count", type=int, default=10,
                        help="Number of LangSearch results per query (default: 10)")
    layers.add_argument("--langsearch-city",
                        help="Locality appended to the Layer 3 query (default: automatic)")
    layers.add_argument("--langsearch-daily-limit", type=int, default=LANGSEARCH_FREE_QPD,
                        help=f"LangSearch daily request ceiling (free tier QPD="
                             f"{LANGSEARCH_FREE_QPD}). 0 disables tracking.")
    layers.add_argument("--langsearch-per-minute", type=int, default=LANGSEARCH_PER_MINUTE,
                        help=f"LangSearch per-minute ceiling (free tier QPM="
                             f"{LANGSEARCH_FREE_QPM}; the default of "
                             f"{LANGSEARCH_PER_MINUTE} leaves margin). Lower this if "
                             f"429s persist.")
    layers.add_argument("--langsearch-quota-file", default=LANGSEARCH_QUOTA_FILE,
                        help=f"State file holding the daily counter "
                             f"(default: {LANGSEARCH_QUOTA_FILE})")


def _add_tuning_arguments(parser: argparse.ArgumentParser) -> None:
    """Matching thresholds and output format."""
    tuning = parser.add_argument_group("matching")
    tuning.add_argument("--merge-distance", type=float, default=75.0,
                        help="Distance within which OSM/TomTom records are treated as "
                             "the same business, meters (default: 75)")
    tuning.add_argument("--name-threshold", type=float, default=0.72,
                        help="Name similarity threshold, 0-1 (default: 0.72)")
    tuning.add_argument("--min-token-len", type=int, default=3,
                        help="Shortest distinctive token used in domain matching "
                             "(default: 3)")
    tuning.add_argument("--include-unnamed", action="store_true",
                        help="Include unnamed OSM records as well")

    out = parser.add_argument_group("output")
    out.add_argument("--full-columns", action="store_true",
                     help="Add diagnostic columns to the CSVs (notes, lat/lon, ...)")
    out.add_argument("--no-has-website", action="store_true",
                     help="Do not write the file listing businesses that have a site")


def _add_network_arguments(parser: argparse.ArgumentParser) -> None:
    """Rate limits, timeouts, and mirror settings."""
    net = parser.add_argument_group("network")
    net.add_argument("--overpass-timeout", type=int, default=180,
                     help="Overpass query timeout in seconds (default: 180)")
    net.add_argument("--overpass-url", action="append",
                     help="Custom Overpass mirror (repeatable)")
    net.add_argument("--delay-tomtom", type=float, default=DELAY_TOMTOM,
                     help=f"Delay between TomTom requests (default: {DELAY_TOMTOM})")
    net.add_argument("--langsearch-delay", "--delay-langsearch", type=float,
                     dest="langsearch_delay", default=DELAY_LANGSEARCH,
                     help=f"PROACTIVE delay between LangSearch requests, seconds "
                          f"(default: {DELAY_LANGSEARCH}). The free tier allows QPS=1, "
                          f"so going below this produces 429s; 0.25 is enough on "
                          f"Tier 1 (QPS=5).")
    net.add_argument("--max-retries", type=int, default=3,
                     help="Retry attempts for failed requests (default: 3)")


def _add_helper_mode_arguments(parser: argparse.ArgumentParser) -> None:
    """Helper modes that do not run a scan."""
    modes = parser.add_argument_group("helper modes")
    modes.add_argument("--dry-run", action="store_true",
                       help="Show the plan without issuing any request")
    modes.add_argument("--list-types", action="store_true",
                       help="List the supported business types and exit")
    modes.add_argument("--tomtom-probe", action="store_true",
                       help="Show what TomTom returns for each business type and exit "
                            "(one request per type; verifies category quality)")

    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")


# ---------------------------------------------------------------------------
# Config assembly
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> Config:
    """Merge CLI arguments and .env into a single Config object."""
    env_path = config_module.find_env_file(args.env_file)
    env = config_module.load_env(args.env_file)
    resolve = config_module.resolve_secret

    return Config(
        contact=resolve(args.contact, env, ENV_CONTACT),
        tomtom_key=resolve(args.tomtom_key, env, ENV_TOMTOM),
        langsearch_key=resolve(args.langsearch_key, env, ENV_LANGSEARCH),
        env_path=env_path,
        radius_m=args.radius,
        include_unnamed=args.include_unnamed,
        skip_tomtom=args.skip_tomtom,
        skip_langsearch=args.skip_langsearch,
        tomtom_mode=args.tomtom_mode,
        overpass_mirrors=args.overpass_url or list(config_module.DEFAULT_OVERPASS_MIRRORS),
        overpass_timeout=args.overpass_timeout,
        tomtom_cell_radius_m=args.tomtom_cell_radius,
        tomtom_limit=args.tomtom_limit,
        tomtom_max_pages=args.tomtom_max_pages,
        tomtom_daily_limit=args.tomtom_daily_limit,
        tomtom_match_radius_m=args.tomtom_match_radius,
        tomtom_categories=parse_category_overrides(args.tomtom_category),
        tomtom_language=args.tomtom_language,
        langsearch_count=args.langsearch_count,
        langsearch_city=args.langsearch_city,
        merge_distance_m=args.merge_distance,
        name_threshold=args.name_threshold,
        min_token_len=args.min_token_len,
        delay_tomtom=args.delay_tomtom,
        delay_langsearch=args.langsearch_delay,
        langsearch_daily_limit=args.langsearch_daily_limit,
        langsearch_per_minute=args.langsearch_per_minute,
        langsearch_quota_file=args.langsearch_quota_file,
        max_retries=args.max_retries,
        output_base=args.output,
        write_has_website=not args.no_has_website,
        full_columns=args.full_columns,
    )


# ---------------------------------------------------------------------------
# Helper modes
# ---------------------------------------------------------------------------

def print_types() -> None:
    print("Supported business types (canonical name -> OSM tags):\n")
    for btype in sorted(BUSINESS_TYPES):
        tags = ", ".join(f"{k}={v}" for k, v in BUSINESS_TYPES[btype])
        aliases = sorted(a for a, c in TYPE_ALIASES.items() if c == btype)
        alias_text = f"   [aliases: {', '.join(aliases)}]" if aliases else ""
        print(f"  {btype:18s} {tags}{alias_text}")
    print("\n  all                every type")


def print_dry_run(cfg: Config, args: argparse.Namespace, types: Sequence[str]) -> int:
    """Show the plan and the estimated quota cost without issuing requests (FR-16)."""
    has_center = args.latlng is not None
    lat, lon = args.latlng if has_center else (0.0, 0.0)

    print("=" * 70)
    print("DRY RUN — no HTTP request was made")
    print("=" * 70)
    if has_center:
        print(f"Center        : {lat:.6f}, {lon:.6f} (no geocoding needed)")
    else:
        print(f'Center        : "{args.address}" (will be resolved via Nominatim)')
    print(f"Radius        : {cfg.radius_m} m")
    print(f"Business types: {', '.join(types)}  ({len(types)} types)")

    query = osm_source.build_overpass_query(types, lat, lon, cfg.radius_m, cfg.overpass_timeout)
    if not has_center:
        query = query.replace("0.000000,0.000000", "<lat>,<lon>")
    print("\nOverpass query:")
    print("-" * 70)
    print(query)
    print("-" * 70)

    print("\nEstimated request counts:")
    print(f"  Nominatim (geocode)   : {0 if has_center else 1}")
    print(f"  Nominatim (reverse)   : {0 if (cfg.skip_langsearch or cfg.langsearch_city) else 1}")
    print(f"  Overpass              : 1  (one query covers the whole radius)")
    _print_tomtom_estimate(cfg, types, lat, lon)
    _print_langsearch_estimate(cfg)

    print("\nLayer status:")
    print(f"  Layer 1a (OSM)        : ACTIVE")
    print(f"  Layer 1b/2 (TomTom)   : {_layer_state(cfg.skip_tomtom, cfg.tomtom_key, cfg.tomtom_mode)}")
    print(f"  Layer 3 (LangSearch)  : {_layer_state(cfg.skip_langsearch, cfg.langsearch_key)}")

    no_web, has_web, notes = output.output_paths(cfg.output_base)
    print(f"\nOutput (leads): {no_web}")
    print(f"Output (sites): {has_web if cfg.write_has_website else '(will not be written)'}")
    print(f"Coverage note : {notes}")
    print(f"User-Agent    : {cfg.user_agent}")
    _print_missing_key_warnings(cfg)
    return 0


def _layer_state(skipped: bool, key: str, mode: str = "") -> str:
    if skipped:
        return "SKIPPED"
    if not key:
        return "NO KEY -> will be skipped"
    return f"ACTIVE ({mode})" if mode else "ACTIVE"


def _print_tomtom_estimate(cfg: Config, types: Sequence[str], lat: float, lon: float) -> None:
    if not cfg.tomtom_enabled:
        print(f"  TomTom                : 0  (skipped / no key)")
        return
    if cfg.tomtom_mode == "verify":
        print(f"  TomTom (verify)       : one per candidate")
        return

    cells = build_grid(lat, lon, cfg.radius_m, cfg.tomtom_cell_radius_m)
    low = len(cells) * len(types)
    high = low * max(1, cfg.tomtom_max_pages)
    print(f"  TomTom (discover)     : {low} - {high}")
    print(f"      {len(cells)} grid cells x {len(types)} categories "
          f"x 1-{max(1, cfg.tomtom_max_pages)} pages")
    print(f"      cell radius: {cfg.tomtom_cell_radius_m} m")
    if high > cfg.tomtom_daily_limit:
        print(f"      !! The upper bound ({high}) exceeds the daily quota "
              f"({cfg.tomtom_daily_limit}).")
        print(f"         Raise --tomtom-cell-radius, lower --radius,")
        print(f"         or use --tomtom-mode verify.")


def _print_langsearch_estimate(cfg: Config) -> None:
    """How many requests and HOW LONG Layer 3 costs — shown before spending (FR-30)."""
    if cfg.skip_langsearch:
        print(f"  LangSearch (Layer 3)  : skipped")
        return
    quota = DailyQuota(cfg.langsearch_daily_limit,
                       default_quota_path(cfg.langsearch_quota_file), label="LangSearch")
    qpm = cfg.langsearch_per_minute
    print(f"  LangSearch (Layer 3)  : one per candidate passing the website filter")
    print(f"      {cfg.delay_langsearch:.2f}s between requests, per-minute cap {qpm} -> "
          f"100 candidates ~{human_duration(estimate_seconds(100, cfg.delay_langsearch, qpm))}, "
          f"500 ~{human_duration(estimate_seconds(500, cfg.delay_langsearch, qpm))}")
    print(f"      daily quota: {quota.status()}")


def _print_missing_key_warnings(cfg: Config) -> None:
    """A dry run issues no requests, so it warns instead of stopping."""
    missing = []
    if not cfg.contact:
        missing.append(ENV_CONTACT)
    if not cfg.skip_tomtom and not cfg.tomtom_key:
        missing.append(ENV_TOMTOM)
    if not cfg.skip_langsearch and not cfg.langsearch_key:
        missing.append(ENV_LANGSEARCH)
    if missing:
        print()
        print("WARNING: these settings are missing -> " + ", ".join(missing))
        print("         A real run would fail. Start with:  cp .env.example .env")


def run_probe(cfg: Config, args: argparse.Namespace, types: Sequence[str]) -> int:
    """--tomtom-probe: category sanity check."""
    if not cfg.tomtom_key:
        log(f"--tomtom-probe requires a TomTom key ({ENV_TOMTOM}).", level="err")
        return 2
    http = HttpClient(cfg)
    center = resolve_center(http, cfg, address=args.address, latlng=args.latlng)
    if center is None:
        log("--tomtom-probe requires --address or --latlng.", level="err")
        return 2
    return tomtom_source.probe_categories(http, cfg, types, center)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    enable_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    set_verbose(args.verbose)

    if args.list_types:
        print_types()
        return 0

    if not args.address and not args.latlng:
        parser.error("provide --address or --latlng (or use --list-types)")
    if args.radius <= 0:
        parser.error("--radius must be positive")
    if args.radius > 50000:
        log("Radius above 50 km — Overpass may time out.", level="warn")

    try:
        types = resolve_types(args.types)
    except UnknownBusinessType as exc:
        log(f"Unknown business type: {', '.join(exc.unknown)}  "
            f"(see --list-types for the full list)", level="err")
        return 2

    cfg = build_config(args)

    # A dry run issues no requests, so missing keys warn rather than stop.
    if args.dry_run:
        return print_dry_run(cfg, args, types)

    try:
        cfg.require_keys()
    except ConfigError as exc:
        sys.stderr.write("\n" + str(exc) + "\n\n")
        return 2

    if args.tomtom_probe:
        return run_probe(cfg, args, types)

    return run_and_report(cfg, types, address=args.address, latlng=args.latlng)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("Cancelled by user.", level="err")
        raise SystemExit(130)
