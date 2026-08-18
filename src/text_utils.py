# -*- coding: utf-8 -*-
"""Isim normalizasyonu ve web sitesi/alan adi siniflandirmasi.

Tek sorumluluk: metin. Ag istegi yok, dosya yazmaz, ust seviye modul
import etmez — pipeline'in her katmani bunu kullanabilsin diye en altta durur.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import List

# ---------------------------------------------------------------------------
# Turkce duyarli normalizasyon
# ---------------------------------------------------------------------------

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u", "Â": "a", "Î": "i", "Û": "u",
})

# Isim karsilastirmasinda anlam tasimayan kelimeler
_STOPWORDS = {
    "ve", "the", "and", "de", "da", "ltd", "sti", "as", "a", "s",
    "sirketi", "san", "tic", "limited", "anonim", "ltdsti",
}

# Sektor jenerigi kelimeler: alan adi eslemesinde AYIRT EDICI SAYILMAZ.
# "kebapci.com" -> "Ali Usta Kebap" isletmesinin sitesi degildir.
GENERIC_BIZ_WORDS = {
    "kebap", "kebab", "kebapci", "restoran", "restaurant", "lokanta", "cafe",
    "kafe", "kahve", "pide", "pizza", "burger", "doner", "cig", "kofte",
    "mangal", "ocakbasi", "balik", "meyhane", "bufe", "sofrasi", "sofra",
    "mutfak", "yemek", "firin", "pastane", "tatli", "baklava",
    "kuafor", "berber", "guzellik", "salon", "salonu", "spa", "bakim",
    "oto", "otomotiv", "servis", "servisi", "tamir", "tamirci", "lastik",
    "yikama", "galeri", "motor", "arac", "parca",
    "market", "marketi", "bakkal", "sarkuteri", "manav", "kasap",
    "eczane", "eczanesi", "klinik", "klinigi", "dis", "poliklinik",
    "hastane", "tip", "saglik", "doktor", "veteriner",
    "emlak", "emlakci", "gayrimenkul", "insaat", "yapi", "muhendislik",
    "avukat", "hukuk", "buro", "burosu", "ofis", "danismanlik", "musavirlik",
    "sigorta", "acente", "acentesi", "turizm", "seyahat", "tur",
    "spor", "fitness", "gym", "kurs", "kursu", "egitim", "akademi",
    "otel", "oteli", "pansiyon", "konak", "butik", "magaza", "magazasi",
    "ticaret", "sanayi", "merkez", "merkezi", "grup", "group", "hizmet",
    "hizmetleri", "cicek", "cicekci", "kuyumcu", "mobilya", "hirdavat",
    "bilgisayar", "teknoloji", "teknik", "elektrik", "tesisat", "nakliyat",
    "usta", "atolye", "dukkan", "shop", "store", "online", "web", "site",
}

# Bu alan adlari "isletmenin kendi web sitesi" SAYILMAZ.
# Sadece Instagram sayfasi olan bir isletme bir web ajansi icin hala lead'dir.
NON_WEBSITE_DOMAINS = {
    # sosyal
    "facebook.com", "fb.com", "fb.me", "instagram.com", "instagr.am",
    "twitter.com", "x.com", "linkedin.com", "youtube.com", "youtu.be",
    "tiktok.com", "pinterest.com", "wa.me", "whatsapp.com", "t.me",
    "telegram.me", "snapchat.com", "threads.net", "vk.com",
    # harita / dizin
    "google.com", "goo.gl", "g.page", "openstreetmap.org", "tomtom.com",
    "here.com", "bing.com", "yandex.com", "yandex.com.tr", "waze.com",
    "apple.com", "foursquare.com", "swarmapp.com", "4sq.com",
    "yelp.com", "tripadvisor.com", "zomato.com",
    "wikipedia.org", "wikidata.org", "wikimapia.org",
    # TR pazaryeri / dizin / rezervasyon
    "yemeksepeti.com", "getir.com", "trendyol.com", "trendyolyemek.com",
    "migros.com.tr", "hepsiburada.com", "n11.com", "gittigidiyor.com",
    "sahibinden.com", "hurriyetemlak.com", "emlakjet.com", "zingat.com",
    "armut.com", "doktortakvimi.com", "eniyihekim.com", "vezeeta.com",
    "bulurum.com", "nerede.com", "firmarehberi.com.tr", "sirketrehberi.com",
    "kolayrandevu.com", "obilet.com", "biletall.com",
    "booking.com", "hotels.com", "airbnb.com", "etstur.com", "tatilsepeti.com",
    "otelz.com", "trivago.com.tr", "expedia.com", "agoda.com",
    "sikayetvar.com", "eksisozluk.com", "instela.com",
    "yellowpages.com", "yellowpages.com.tr",
    "opentable.com", "quandoo.com.tr", "treatwell.com.tr",
    "linktr.ee", "beacons.ai", "bio.link", "carrd.co",
    # ucretsiz barindirma / blog (ajans acisindan hala hedef)
    "blogspot.com", "wordpress.com", "wixsite.com", "weebly.com",
    "webnode.com.tr", "tr.gg", "blogcu.com", "sitey.me",
}


def norm_text(s: str) -> str:
    """Turkce duyarli normalizasyon: kucuk harf, aksansiz, sadece harf/rakam."""
    if not s:
        return ""
    s = s.translate(_TR_MAP)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_tokens(s: str) -> List[str]:
    """Isimdeki anlamli kelimeler (stopword'ler ayiklanmis)."""
    return [t for t in norm_text(s).split() if t and t not in _STOPWORDS]


def name_similarity(a: str, b: str) -> float:
    """0..1 arasi isim benzerligi (token Jaccard + dizi benzerligi karisimi)."""
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return 0.0
    jacc = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()
    # bir taraf digerinin alt kumesiyse ("Ali Usta" vs "Ali Usta Kebap") odullendir
    if ta <= tb or tb <= ta:
        jacc = max(jacc, 0.85)
    return max(jacc, seq * 0.95)


# ---------------------------------------------------------------------------
# URL / alan adi
# ---------------------------------------------------------------------------

def url_host(url: str) -> str:
    """URL'den host adi cikarir (sema olmadan da calisir)."""
    if not url:
        return ""
    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url.strip())
    u = u.split("/")[0].split("?")[0].split("#")[0]
    u = u.split("@")[-1].split(":")[0]
    u = u.lower().lstrip(".")
    return u[4:] if u.startswith("www.") else u


def registrable_domain(host: str) -> str:
    """Kaba eTLD+1. co.uk / com.tr gibi ikili uzantilari kollar."""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    second_level = {
        "com", "net", "org", "edu", "gov", "co", "gen", "biz", "info",
        "web", "tv", "name", "av", "bel", "k12",
    }
    if parts[-2] in second_level and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_real_website(url: str) -> bool:
    """URL isletmenin KENDI sitesi mi, yoksa sosyal medya/dizin mi?"""
    host = url_host(url)
    if not host or "." not in host:
        return False
    if registrable_domain(host) in NON_WEBSITE_DOMAINS or host in NON_WEBSITE_DOMAINS:
        return False
    # alt alan adiyla kacamak: business.facebook.com gibi
    return not any(host == d or host.endswith("." + d) for d in NON_WEBSITE_DOMAINS)


def domain_matches_name(url: str, business_name: str, min_token_len: int = 3) -> bool:
    """Alan adi gercekten bu isletmeye mi ait? (Katman 3'un eleme kaniti)

    Bilerek TUTUCU: yanlis eleme = kaybedilmis lead, ve bu sessizce olur.
    Tek bir jenerik kelimenin ("kebap", "kuafor") alan adinda gecmesi kanit
    sayilmaz — yoksa "kebapci.com" yuzunden "Ali Usta Kebap" listeden duserdi.
    """
    dom_core = norm_text(registrable_domain(url_host(url)).split(".")[0]).replace(" ", "")
    all_toks = name_tokens(business_name)
    joined = "".join(all_toks)
    if not dom_core or not joined:
        return False

    # 1) Tam eslesme: "Ali Usta Kebap" -> aliustakebap.com
    if joined == dom_core:
        return True

    # 2) Cok kisa alan adlari kanit sayilmaz (ada.com, abc.com ...)
    if len(dom_core) < 5:
        return False

    # 3) Isletmenin AYIRT EDICI kismi alan adinda gecmeli.
    #    "Ozkan Oto Servis" icin bu "ozkan"dir; otoservis.com onun sitesi degil.
    distinctive = [t for t in all_toks if len(t) >= min_token_len and t not in GENERIC_BIZ_WORDS]
    if not distinctive:
        return False  # tamamen jenerik isim -> sadece tam eslesme kabul (madde 1)
    if not any(t in dom_core for t in distinctive):
        return False

    # 4) Alan adinin buyuk kismi isletme adindan gelmeli.
    #    "ali" -> alibaba.com sadece %43 kapsar, kanit sayilmaz.
    covered = sum(len(t) for t in all_toks if t in dom_core)
    return (covered / len(dom_core)) >= 0.6


def tr_locative(n: int) -> str:
    """Sayiya Turkce bulunma hali eki ekler: 3 -> 3'unde, 6 -> 6'sinda.

    Ek, sayinin okunusundaki son kelimeye gore degisir ("dort" -> 4'unde,
    "alti" -> 6'sinda). Duz bir "'sinde" eki cogu sayida yanlis olurdu.
    """
    if n == 0:
        return "0'ında"
    table = {
        1: "inde", 2: "sinde", 3: "ünde", 4: "ünde", 5: "inde",
        6: "sında", 7: "sinde", 8: "inde", 9: "unda",
        10: "unda", 20: "sinde", 30: "unda", 40: "ında", 50: "sinde",
        60: "ında", 70: "inde", 80: "inde", 90: "ında",
        100: "ünde", 1000: "inde",
    }
    if n % 10:
        key = n % 10
    elif n % 100:
        key = n % 100
    elif n % 1000:
        key = 100
    else:
        key = 1000
    return f"{n}'{table[key]}"
