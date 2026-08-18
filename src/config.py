# -*- coding: utf-8 -*-
"""Ayarlarin tek kaynagi: .env okuma + CLI override birlestirme.

Tek sorumluluk: konfigurasyon. Bu modul API cagirmaz, dosya YAZMAZ,
sadece okur ve dogrular.

Oncelik sirasi:  CLI argumani  >  .env dosyasi  >  ortam degiskeni  >  varsayilan
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

VERSION = "2.0.0"

# .env icinde beklenen degisken adlari (.env.example ile birebir ayni olmali)
ENV_CONTACT = "CONTACT_EMAIL"
ENV_TOMTOM = "TOMTOM_API_KEY"
ENV_LANGSEARCH = "LANGSEARCH_API_KEY"

USER_AGENT_TMPL = "leadgen/{ver} (local business lead finder; contact: {contact})"

DEFAULT_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]

# Rate limit varsayilanlari (saniye)
DELAY_NOMINATIM = 1.1   # Nominatim kurali: en fazla 1 istek/saniye
DELAY_OVERPASS = 2.0
DELAY_TOMTOM = 0.25     # freemium ~5 QPS
DELAY_LANGSEARCH = 1.0

TOMTOM_DAILY_FREE_LIMIT = 2500
TOMTOM_MAX_LIMIT = 100          # tek cagrida maksimum sonuc (API siniri)
TOMTOM_DEFAULT_CELL_M = 600     # discover modunda grid hucre yaricapi


class ConfigError(Exception):
    """Ayar eksik/hatali — sessizce devam etmek yerine acik hata."""


# ---------------------------------------------------------------------------
# .env okuma (stdlib; python-dotenv gibi ek bagimlilik EKLENMEZ)
# ---------------------------------------------------------------------------

def parse_env_text(text: str) -> Dict[str, str]:
    """`KEY=value` satirlarini sozluge cevirir.

    - `#` ile baslayan satirlar yorum
    - bos satirlar yok sayilir
    - `export KEY=value` kabul edilir
    - deger cift/tek tirnakla sarilmissa tirnaklar soyulur
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
    """.env dosyasini bulur: once --env-file, sonra cwd, sonra paket koku."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(here), ".env"),  # lead-gen-tool/.env
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_env(explicit: Optional[str] = None) -> Dict[str, str]:
    """.env dosyasini okur; yoksa bos sozluk doner (hata FIRLATMAZ).

    Eksik dosyanin hataya donusmesi, hangi anahtarlarin GEREKLI oldugu
    bilindikten sonra `Config.require_keys()` icinde yapilir.
    """
    path = find_env_file(explicit)
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_env_text(fh.read())
    except OSError as exc:
        raise ConfigError(f".env okunamadi ({path}): {exc}") from exc


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Tum calisma ayarlari tek nesnede."""

    # --- kimlik / anahtarlar ---
    contact: str = ""
    tomtom_key: str = ""
    langsearch_key: str = ""
    env_path: Optional[str] = None

    # --- arama alani ---
    radius_m: int = 2000
    include_unnamed: bool = False

    # --- katman anahtarlari ---
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

    # --- LangSearch ---
    langsearch_count: int = 10
    langsearch_city: Optional[str] = None

    # --- eslestirme ---
    merge_distance_m: float = 75.0
    name_threshold: float = 0.72
    min_token_len: int = 3

    # --- ag ---
    delay_nominatim: float = DELAY_NOMINATIM
    delay_overpass: float = DELAY_OVERPASS
    delay_tomtom: float = DELAY_TOMTOM
    delay_langsearch: float = DELAY_LANGSEARCH
    max_retries: int = 3
    timeout_s: int = 60

    # --- cikti ---
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
        """Gerekli anahtarlar var mi? Yoksa ACIK hatayla dur (FR-22).

        "Gerekli", o calistirmada hangi katmanlarin acik oldugua gore degisir:
        --skip-tomtom verilmisse TomTom anahtari aranmaz.
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
    """Ne yapilacagini SOYLEYEN hata metni — sadece 'eksik' demek yetmez."""
    lines = ["Eksik ayar: " + ", ".join(missing), ""]
    if env_path:
        lines.append(f".env bulundu ({env_path}) ama yukaridaki deger(ler) bos.")
        lines.append("Dosyayi acip doldurun, ornegin:")
    else:
        lines.append(".env dosyasi bulunamadi. Once ornegi kopyalayin:")
        lines.append("")
        lines.append("    cp .env.example .env")
        lines.append("")
        lines.append("Sonra dosyayi acip doldurun, ornegin:")
    lines.append("")
    for key in missing:
        if key == ENV_CONTACT:
            lines.append(f"    {key}=siz@ajansiniz.com")
        else:
            lines.append(f"    {key}=<panelden aldiginiz anahtar>")
    lines.append("")
    if ENV_CONTACT in missing:
        lines.append(f"{ENV_CONTACT} zorunlu: Nominatim ve Overpass kullanim politikasi")
        lines.append("iletisim bilgisi iceren aciklayici bir User-Agent sart kosuyor.")
    if ENV_TOMTOM in missing:
        lines.append("TomTom katmanini kullanmayacaksaniz: --skip-tomtom")
    if ENV_LANGSEARCH in missing:
        lines.append("LangSearch katmanini kullanmayacaksaniz: --skip-langsearch")
    lines.append("")
    lines.append("Anahtarlar CLI ile de verilebilir: --tomtom-key / --langsearch-key / --contact")
    return "\n".join(lines)


def resolve_secret(cli_value: Optional[str], env_file: Dict[str, str], key: str) -> str:
    """CLI > .env dosyasi > ortam degiskeni sirasiyla bir ayari cozer."""
    if cli_value:
        return cli_value.strip()
    if env_file.get(key):
        return env_file[key].strip()
    return os.environ.get(key, "").strip()
