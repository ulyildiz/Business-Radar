# -*- coding: utf-8 -*-
"""Komut satiri arayuzu ve giris noktasi.

Tek sorumluluk: argumanlari tanimlamak, Config'i kurmak, pipeline'i cagirmak.
Is mantigi burada degil.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from . import config as config_module
from . import osm_source, output, tomtom_source
from .config import (
    DELAY_LANGSEARCH,
    DELAY_TOMTOM,
    ENV_CONTACT,
    ENV_LANGSEARCH,
    ENV_TOMTOM,
    TOMTOM_DAILY_FREE_LIMIT,
    TOMTOM_DEFAULT_CELL_M,
    TOMTOM_MAX_LIMIT,
    VERSION,
    Config,
    ConfigError,
)
from .console import enable_utf8, log, set_verbose
from .geo_utils import build_grid
from .http_client import HttpClient
from .models import Center
from .osm_source import BUSINESS_TYPES, TYPE_ALIASES, UnknownBusinessType, resolve_types
from .pipeline import resolve_center, run_and_report


# ---------------------------------------------------------------------------
# Arguman ayristirma yardimcilari
# ---------------------------------------------------------------------------

def parse_latlng(value: str) -> Tuple[float, float]:
    parts = re.split(r"[,\s]+", value.strip())
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--latlng formati: 'enlem,boylam' (or. 40.9903,29.0270)")
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError("--latlng sayisal olmali")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise argparse.ArgumentTypeError("--latlng gecersiz aralikta")
    return lat, lon


def parse_category_overrides(raw: Optional[Sequence[str]]) -> Dict[str, str]:
    """--tomtom-category TIP=SORGU ciftlerini sozluge cevirir."""
    overrides: Dict[str, str] = {}
    for item in raw or []:
        if "=" not in item:
            log(f"--tomtom-category yok sayildi (TIP=SORGU bekleniyordu): {item}", level="warn")
            continue
        key, _, value = item.partition("=")
        canonical = key.strip().lower().replace(" ", "_")
        canonical = TYPE_ALIASES.get(canonical, canonical)
        overrides[canonical] = value.strip()
    return overrides


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m leadgen.cli",
        description="GPS yaricapinda web sitesi olan/olmayan yerel isletmeleri bulur "
                    "(3 katmanli, sadece ucretsiz API'ler).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ilk kurulum:\n"
            "  cp .env.example .env      # sonra .env'i acip anahtarlari doldurun\n\n"
            "Ornek:\n"
            '  python -m leadgen.cli --address "Kadikoy, Istanbul" --radius 5000 \\\n'
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
    """Nerede, ne kadar alanda, hangi is tipleri, nereye yazilacak."""
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--address", help="Merkez adres (Nominatim ile koordinata cevrilir)")
    where.add_argument("--latlng", type=parse_latlng, help="Merkez koordinat: 'enlem,boylam'")

    parser.add_argument("--radius", type=int, default=2000,
                        help="Arama yaricapi, metre (varsayilan: 2000)")
    parser.add_argument("--types", nargs="+", default=["restaurant"],
                        help="Bir veya daha fazla is tipi (veya 'all'). --list-types ile bakin.")
    parser.add_argument("--output", "-o", default="leads",
                        help="Cikti taban adi. Uretilenler: <taban>_no_website.csv, "
                             "<taban>_has_website.csv, <taban>_NOTLAR.txt (varsayilan: leads)")


def _add_key_arguments(parser: argparse.ArgumentParser) -> None:
    """Anahtarlar: verilmezse .env'den okunur, verilirse .env'i ezer (FR-24)."""
    keys = parser.add_argument_group("anahtarlar (verilmezse .env'den okunur)")
    keys.add_argument("--contact", help=f".env: {ENV_CONTACT} — User-Agent'a konacak iletisim bilgisi")
    keys.add_argument("--tomtom-key", help=f".env: {ENV_TOMTOM}")
    keys.add_argument("--langsearch-key", help=f".env: {ENV_LANGSEARCH}")
    keys.add_argument("--env-file", help="Alternatif .env yolu")


def _add_layer_arguments(parser: argparse.ArgumentParser) -> None:
    """Katman acma/kapama ve katmana ozgu ayarlar."""
    layers = parser.add_argument_group("katmanlar")
    layers.add_argument("--skip-tomtom", action="store_true", help="TomTom katmanini tamamen atla")
    layers.add_argument("--skip-langsearch", action="store_true", help="LangSearch katmanini atla")
    layers.add_argument("--tomtom-mode", choices=("discover", "verify"), default="discover",
                        help="discover: TomTom bagimsiz alan taramasi yapar ve OSM'de olmayan "
                             "isletmeleri de EKLER (varsayilan). "
                             "verify: sadece adaylari tek tek dogrular (kota tasarrufu).")
    layers.add_argument("--tomtom-cell-radius", type=int, default=TOMTOM_DEFAULT_CELL_M,
                        help=f"discover grid hucre yaricapi, metre (varsayilan: {TOMTOM_DEFAULT_CELL_M})")
    layers.add_argument("--tomtom-limit", type=int, default=TOMTOM_MAX_LIMIT,
                        help=f"TomTom tek cagri sonuc siniri (maks {TOMTOM_MAX_LIMIT})")
    layers.add_argument("--tomtom-max-pages", type=int, default=3,
                        help="Hucre basina maksimum sayfa (varsayilan: 3, ~300 sonuc)")
    layers.add_argument("--tomtom-daily-limit", type=int, default=TOMTOM_DAILY_FREE_LIMIT,
                        help=f"TomTom gunluk istek tavani (varsayilan: {TOMTOM_DAILY_FREE_LIMIT})")
    layers.add_argument("--tomtom-match-radius", type=int, default=250,
                        help="verify modunda aday cevresi yaricapi, metre (varsayilan: 250)")
    layers.add_argument("--tomtom-category", action="append", metavar="TIP=SORGU",
                        help='TomTom kategori metnini tip bazinda ez '
                             '(or. --tomtom-category car_repair="oto sanayi")')
    layers.add_argument("--langsearch-count", type=int, default=10,
                        help="LangSearch sonuc sayisi (varsayilan: 10)")
    layers.add_argument("--langsearch-city", help="Katman 3 sorgusundaki semt (varsayilan: otomatik)")


def _add_tuning_arguments(parser: argparse.ArgumentParser) -> None:
    """Eslestirme esikleri ve cikti bicimi."""
    tuning = parser.add_argument_group("eslestirme")
    tuning.add_argument("--merge-distance", type=float, default=75.0,
                        help="OSM/TomTom kayitlarini ayni sayma mesafesi, metre (varsayilan: 75)")
    tuning.add_argument("--name-threshold", type=float, default=0.72,
                        help="Isim eslesme esigi 0-1 (varsayilan: 0.72)")
    tuning.add_argument("--min-token-len", type=int, default=3,
                        help="Alan adi eslemesinde ayirt edici en kisa parca (varsayilan: 3)")
    tuning.add_argument("--include-unnamed", action="store_true",
                        help="Isimsiz OSM kayitlarini da dahil et")

    out = parser.add_argument_group("cikti")
    out.add_argument("--full-columns", action="store_true",
                     help="CSV'lere teshis kolonlarini da ekle (notes, lat/lon, ...)")
    out.add_argument("--no-has-website", action="store_true",
                     help="Website'i olan isletmeler dosyasini yazma")


def _add_network_arguments(parser: argparse.ArgumentParser) -> None:
    """Rate limit, zaman asimi, mirror ayarlari."""
    net = parser.add_argument_group("ag")
    net.add_argument("--overpass-timeout", type=int, default=180,
                     help="Overpass sorgu zaman asimi, sn (varsayilan: 180)")
    net.add_argument("--overpass-url", action="append", help="Ozel Overpass mirror (tekrarlanabilir)")
    net.add_argument("--delay-tomtom", type=float, default=DELAY_TOMTOM,
                     help=f"TomTom istekleri arasi gecikme (varsayilan: {DELAY_TOMTOM})")
    net.add_argument("--delay-langsearch", type=float, default=DELAY_LANGSEARCH,
                     help=f"LangSearch istekleri arasi gecikme (varsayilan: {DELAY_LANGSEARCH})")
    net.add_argument("--max-retries", type=int, default=3,
                     help="Basarisiz isteklerde tekrar deneme sayisi (varsayilan: 3)")


def _add_helper_mode_arguments(parser: argparse.ArgumentParser) -> None:
    """Tarama yapmayan yardimci modlar."""
    modes = parser.add_argument_group("yardimci modlar")
    modes.add_argument("--dry-run", action="store_true", help="Hicbir istek atmadan plani goster")
    modes.add_argument("--list-types", action="store_true", help="Desteklenen is tiplerini listele ve cik")
    modes.add_argument("--tomtom-probe", action="store_true",
                       help="Her is tipi icin TomTom'un ne dondurdugunu goster ve cik "
                            "(tip basina 1 istek; kategori kalitesini dogrulamak icin)")

    parser.add_argument("-v", "--verbose", action="store_true", help="Ayrintili cikti")
    parser.add_argument("--version", action="version", version=f"leadgen {VERSION}")


# ---------------------------------------------------------------------------
# Config kurulumu
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> Config:
    """CLI + .env birlesimi -> tek Config nesnesi."""
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
        langsearch_count=args.langsearch_count,
        langsearch_city=args.langsearch_city,
        merge_distance_m=args.merge_distance,
        name_threshold=args.name_threshold,
        min_token_len=args.min_token_len,
        delay_tomtom=args.delay_tomtom,
        delay_langsearch=args.delay_langsearch,
        max_retries=args.max_retries,
        output_base=args.output,
        write_has_website=not args.no_has_website,
        full_columns=args.full_columns,
    )


# ---------------------------------------------------------------------------
# Yardimci modlar
# ---------------------------------------------------------------------------

def print_types() -> None:
    print("Desteklenen is tipleri (kanonik ad -> OSM tag'leri):\n")
    for btype in sorted(BUSINESS_TYPES):
        tags = ", ".join(f"{k}={v}" for k, v in BUSINESS_TYPES[btype])
        aliases = sorted(a for a, c in TYPE_ALIASES.items() if c == btype)
        alias_text = f"   [takma ad: {', '.join(aliases)}]" if aliases else ""
        print(f"  {btype:18s} {tags}{alias_text}")
    print("\n  all                tum tipler")


def print_dry_run(cfg: Config, args: argparse.Namespace, types: Sequence[str]) -> int:
    """Hicbir istek atmadan plani ve tahmini kota maliyetini gosterir (FR-16)."""
    has_center = args.latlng is not None
    lat, lon = args.latlng if has_center else (0.0, 0.0)

    print("=" * 70)
    print("DRY RUN — hicbir HTTP istegi atilmadi")
    print("=" * 70)
    if has_center:
        print(f"Merkez        : {lat:.6f}, {lon:.6f} (geocoding gerekmiyor)")
    else:
        print(f'Merkez        : "{args.address}" (Nominatim ile cozulecek)')
    print(f"Yaricap       : {cfg.radius_m} m")
    print(f"Is tipleri    : {', '.join(types)}  ({len(types)} tip)")

    query = osm_source.build_overpass_query(types, lat, lon, cfg.radius_m, cfg.overpass_timeout)
    if not has_center:
        query = query.replace("0.000000,0.000000", "<enlem>,<boylam>")
    print("\nOverpass sorgusu:")
    print("-" * 70)
    print(query)
    print("-" * 70)

    print("\nTahmini istek sayilari:")
    print(f"  Nominatim (geocode)   : {0 if has_center else 1}")
    print(f"  Nominatim (reverse)   : {0 if (cfg.skip_langsearch or cfg.langsearch_city) else 1}")
    print(f"  Overpass              : 1  (tek sorgu tum yaricapi kapsar)")
    _print_tomtom_estimate(cfg, types, lat, lon)
    print(f"  LangSearch (Katman 3) : "
          f"{'atlandi' if cfg.skip_langsearch else 'website filtresinden gecen aday sayisi kadar'}")

    print("\nKatman durumu:")
    print(f"  Katman 1a (OSM)       : AKTIF")
    print(f"  Katman 1b/2 (TomTom)  : {_layer_state(cfg.skip_tomtom, cfg.tomtom_key, cfg.tomtom_mode)}")
    print(f"  Katman 3 (LangSearch) : {_layer_state(cfg.skip_langsearch, cfg.langsearch_key)}")

    no_web, has_web, notes = output.output_paths(cfg.output_base)
    print(f"\nCikti (lead)  : {no_web}")
    print(f"Cikti (siteli): {has_web if cfg.write_has_website else '(yazilmayacak)'}")
    print(f"Kapsam notu   : {notes}")
    print(f"User-Agent    : {cfg.user_agent}")
    _print_missing_key_warnings(cfg)
    return 0


def _layer_state(skipped: bool, key: str, mode: str = "") -> str:
    if skipped:
        return "ATLANIYOR"
    if not key:
        return "ANAHTAR YOK -> atlanacak"
    return f"AKTIF ({mode})" if mode else "AKTIF"


def _print_tomtom_estimate(cfg: Config, types: Sequence[str], lat: float, lon: float) -> None:
    if not cfg.tomtom_enabled:
        print(f"  TomTom                : 0  (atlandi/anahtar yok)")
        return
    if cfg.tomtom_mode == "verify":
        print(f"  TomTom (verify)       : aday sayisi kadar")
        return

    cells = build_grid(lat, lon, cfg.radius_m, cfg.tomtom_cell_radius_m)
    low = len(cells) * len(types)
    high = low * max(1, cfg.tomtom_max_pages)
    print(f"  TomTom (discover)     : {low} - {high}")
    print(f"      {len(cells)} grid hucresi x {len(types)} kategori "
          f"x 1-{max(1, cfg.tomtom_max_pages)} sayfa")
    print(f"      hucre yaricapi: {cfg.tomtom_cell_radius_m} m")
    if high > cfg.tomtom_daily_limit:
        print(f"      !! Ust sinir ({high}) gunluk kotayi ({cfg.tomtom_daily_limit}) asiyor.")
        print(f"         --tomtom-cell-radius buyutun, --radius kucultun,")
        print(f"         ya da --tomtom-mode verify kullanin.")


def _print_missing_key_warnings(cfg: Config) -> None:
    """dry-run istek atmadigi icin durdurmaz, ama eksikleri soyler."""
    missing = []
    if not cfg.contact:
        missing.append(ENV_CONTACT)
    if not cfg.skip_tomtom and not cfg.tomtom_key:
        missing.append(ENV_TOMTOM)
    if not cfg.skip_langsearch and not cfg.langsearch_key:
        missing.append(ENV_LANGSEARCH)
    if missing:
        print()
        print("UYARI: su ayarlar eksik -> " + ", ".join(missing))
        print("       Gercek calistirmada hata verir. Once:  cp .env.example .env")


def run_probe(cfg: Config, args: argparse.Namespace, types: Sequence[str]) -> int:
    """--tomtom-probe: kategori kalite kontrolu."""
    if not cfg.tomtom_key:
        log(f"--tomtom-probe icin TomTom anahtari gerekli ({ENV_TOMTOM}).", level="err")
        return 2
    http = HttpClient(cfg)
    center = resolve_center(http, cfg, address=args.address, latlng=args.latlng)
    if center is None:
        log("--tomtom-probe icin --address veya --latlng gerekli.", level="err")
        return 2
    return tomtom_source.probe_categories(http, cfg, types, center)


# ---------------------------------------------------------------------------
# Giris noktasi
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
        parser.error("--address veya --latlng vermelisiniz (ya da --list-types kullanin)")
    if args.radius <= 0:
        parser.error("--radius pozitif olmali")
    if args.radius > 50000:
        log("Yaricap 50 km'den buyuk — Overpass zaman asimina ugrayabilir.", level="warn")

    try:
        types = resolve_types(args.types)
    except UnknownBusinessType as exc:
        log(f"Bilinmeyen is tipi: {', '.join(exc.unknown)}  (--list-types ile listeye bakin)",
            level="err")
        return 2

    cfg = build_config(args)

    # dry-run istek atmaz -> eksik anahtar durdurmaz, sadece uyarir
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
        log("Kullanici tarafindan iptal edildi.", level="err")
        raise SystemExit(130)
