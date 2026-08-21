# -*- coding: utf-8 -*-
"""Name normalization and website/domain classification.

Single responsibility: text. No network calls, no file writes, no imports of
higher-level modules — it sits at the bottom so every pipeline layer can use it.

NOTE ON LANGUAGE
The user interface is English, but the word lists below are DATA, not UI text.
They encode how business names and domains look in the target market (Turkish),
and translating them would break matching. Keep them in the source language.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import List

# ---------------------------------------------------------------------------
# Locale-aware normalization
# ---------------------------------------------------------------------------

# Turkish letters have no ASCII equivalent under NFKD (dotless i in particular),
# so they are folded explicitly before the generic accent stripping below.
_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u", "Â": "a", "Î": "i", "Û": "u",
})

# Words that carry no meaning when comparing business names.
_STOPWORDS = {
    "ve", "the", "and", "de", "da", "ltd", "sti", "as", "a", "s",
    "sirketi", "san", "tic", "limited", "anonim", "ltdsti",
}

# Generic trade words: NOT treated as distinctive when matching a domain.
# "kebapci.com" is not the website of a business called "Ali Usta Kebap".
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

# These domains do NOT count as "the business has its own website".
# A business with only an Instagram page is still a lead for a web agency.
NON_WEBSITE_DOMAINS = {
    # social
    "facebook.com", "fb.com", "fb.me", "instagram.com", "instagr.am",
    "twitter.com", "x.com", "linkedin.com", "youtube.com", "youtu.be",
    "tiktok.com", "pinterest.com", "wa.me", "whatsapp.com", "t.me",
    "telegram.me", "snapchat.com", "threads.net", "vk.com",
    # maps / directories
    "google.com", "goo.gl", "g.page", "openstreetmap.org", "tomtom.com",
    "here.com", "bing.com", "yandex.com", "yandex.com.tr", "waze.com",
    "apple.com", "foursquare.com", "swarmapp.com", "4sq.com",
    "yelp.com", "tripadvisor.com", "zomato.com",
    "wikipedia.org", "wikidata.org", "wikimapia.org",
    # marketplaces / directories / booking
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
    # free hosting / blogs (still a prospect from an agency's point of view)
    "blogspot.com", "wordpress.com", "wixsite.com", "weebly.com",
    "webnode.com.tr", "tr.gg", "blogcu.com", "sitey.me",
}


def norm_text(s: str) -> str:
    """Locale-aware normalization: lowercase, accent-free, alphanumeric only."""
    if not s:
        return ""
    s = s.translate(_TR_MAP)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_tokens(s: str) -> List[str]:
    """Meaningful words in a business name, with stopwords removed."""
    return [t for t in norm_text(s).split() if t and t not in _STOPWORDS]


def name_similarity(a: str, b: str) -> float:
    """Name similarity in 0..1 (token Jaccard blended with sequence ratio)."""
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return 0.0
    jacc = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()
    # Reward one name being a subset of the other ("Ali Usta" vs "Ali Usta Kebap").
    if ta <= tb or tb <= ta:
        jacc = max(jacc, 0.85)
    return max(jacc, seq * 0.95)


# ---------------------------------------------------------------------------
# URL / domain
# ---------------------------------------------------------------------------

def url_host(url: str) -> str:
    """Extract the host from a URL (works without a scheme too)."""
    if not url:
        return ""
    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url.strip())
    u = u.split("/")[0].split("?")[0].split("#")[0]
    u = u.split("@")[-1].split(":")[0]
    u = u.lower().lstrip(".")
    return u[4:] if u.startswith("www.") else u


def registrable_domain(host: str) -> str:
    """Approximate eTLD+1, handling two-part suffixes such as co.uk / com.tr."""
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
    """Is this the business's OWN site, or just social media / a directory?"""
    host = url_host(url)
    if not host or "." not in host:
        return False
    if registrable_domain(host) in NON_WEBSITE_DOMAINS or host in NON_WEBSITE_DOMAINS:
        return False
    # Catch subdomain escapes such as business.facebook.com.
    return not any(host == d or host.endswith("." + d) for d in NON_WEBSITE_DOMAINS)


def domain_matches_name(url: str, business_name: str, min_token_len: int = 3) -> bool:
    """Does this domain really belong to this business? (Layer 3 exclusion proof)

    Deliberately CONSERVATIVE: a false exclusion is a lost lead, and it happens
    silently. A single generic word ("kebap", "kuafor") appearing in the domain
    is not proof — otherwise "kebapci.com" would knock "Ali Usta Kebap" off the
    list. A false keep only costs one wasted outreach call.
    """
    dom_core = norm_text(registrable_domain(url_host(url)).split(".")[0]).replace(" ", "")
    all_toks = name_tokens(business_name)
    joined = "".join(all_toks)
    if not dom_core or not joined:
        return False

    # 1) Exact match: "Ali Usta Kebap" -> aliustakebap.com
    if joined == dom_core:
        return True

    # 2) Very short domains are not proof (ada.com, abc.com, ...).
    if len(dom_core) < 5:
        return False

    # 3) The DISTINCTIVE part of the name must appear in the domain.
    #    For "Ozkan Oto Servis" that is "ozkan"; otoservis.com is not its site.
    distinctive = [t for t in all_toks if len(t) >= min_token_len and t not in GENERIC_BIZ_WORDS]
    if not distinctive:
        return False  # fully generic name -> only an exact match counts (rule 1)
    if not any(t in dom_core for t in distinctive):
        return False

    # 4) Most of the domain must come from the business name.
    #    "ali" covers only 43% of alibaba.com, which is not proof.
    covered = sum(len(t) for t in all_toks if t in dom_core)
    return (covered / len(dom_core)) >= 0.6
