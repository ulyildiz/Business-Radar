# -*- coding: utf-8 -*-
"""Terminal cikti yardimcilari.

Tek sorumluluk: kullaniciya terminalde mesaj gostermek.
Hicbir seyi disari yazmaz, hicbir API cagirmaz.
"""

from __future__ import annotations

import sys
from typing import List

_VERBOSE = False

_PREFIX = {
    "info": "[*]",
    "ok": "[+]",
    "warn": "[!]",
    "err": "[x]",
    "dbg": "[.]",
}


def enable_utf8() -> None:
    """Windows konsolunda Turkce karakterler patlamasin."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def set_verbose(value: bool) -> None:
    global _VERBOSE
    _VERBOSE = value


def is_verbose() -> bool:
    return _VERBOSE


def log(msg: str, *, level: str = "info") -> None:
    """Tek satirlik durum mesaji. 'dbg' seviyesi sadece --verbose ile gorunur."""
    if level == "dbg" and not _VERBOSE:
        return
    stream = sys.stderr if level in ("warn", "err", "dbg") else sys.stdout
    stream.write(f"{_PREFIX.get(level, '[*]')} {msg}\n")
    stream.flush()


def wrap(text: str, width: int) -> List[str]:
    """Metni verilen genislige gore satirlara boler (textwrap'e ince alternatif)."""
    lines: List[str] = []
    current = ""
    for word in text.split():
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines
