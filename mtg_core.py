"""
Shared logic for comparing a Moxfield decklist against a ManaBox collection
export: Moxfield/Scryfall fetching, collection loading, price comparison, and
HTML report rendering. No terminal/UI dependencies (no `rich`, no `input()`)
so this module can be imported by both the CLI (moxfield_vs_collection.py)
and the Flask app (app.py).
"""

from __future__ import annotations

import csv
import datetime
import glob
import html
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

__all__ = [
    "CardEntry", "CardResult", "OwnedPrinting", "OwnedCard",
    "parse_deck_id", "fetch_deck", "extract_entries",
    "normalize_name", "find_collection_candidates", "load_collection", "load_overrides",
    "fetch_scryfall_prices_by_id", "fetch_cheapest_printings", "price_for_printing", "select_used_printings",
    "categorize", "shopping_group", "shopping_group_rank",
    "best_prices", "priced_for_finish", "build_comparison",
    "render_markdown", "write_missing_csv", "render_html",
    "scryfall_image_url",
]

MOXFIELD_API = "https://api2.moxfield.com/v2/decks/all/{deck_id}"

# Card type buckets, in the order we check them against a card's type line.
# A card is placed in the first bucket whose keyword appears in its type line.
TYPE_PRECEDENCE = [
    "Planeswalker",
    "Battle",
    "Creature",
    "Sorcery",
    "Instant",
    "Artifact",
    "Enchantment",
    "Land",
]
PLURALS = {"Sorcery": "Sorceries"}
BUCKET_ORDER = ["Planeswalkers", "Battles", "Creatures", "Sorceries",
                "Instants", "Artifacts", "Enchantments", "Lands", "Basic Lands", "Other"]

# (store label, nonfoil price key, foil price key, nonfoil url key, foil url key)
STORES = [
    ("TCGP", "usd", "usd_foil", "tcgPlayerUrl", "tcgPlayerUrl"),
    ("CK", "ck", "ck_foil", "cardKingdomUrl", "cardKingdomFoilUrl"),
    ("MP", "mp", "mp_foil", "manapool_url", "manapool_url"),
]

CARD_KINGDOM_BASE = "https://www.cardkingdom.com/"

SCRYFALL_IMAGE_BASE = "https://cards.scryfall.io"


def scryfall_image_url(scryfall_id: str | None, size: str = "normal") -> str | None:
    """Direct Scryfall CDN hotlink -- no API call needed. size: small/normal/large."""
    if not scryfall_id:
        return None
    return f"{SCRYFALL_IMAGE_BASE}/{size}/front/{scryfall_id[0]}/{scryfall_id[1]}/{scryfall_id}.jpg"


SCRYFALL_SYMBOL_BASE = "https://svgs.scryfall.io/card-symbols"


def mana_symbol_url(color: str) -> str:
    """Direct Scryfall CDN hotlink to the official mana symbol SVG for a single
    WUBRG letter (or 'C' for colorless) -- same no-API-call approach as card art."""
    return f"{SCRYFALL_SYMBOL_BASE}/{color}.svg"


@dataclass
class CardEntry:
    name: str
    quantity: int
    type_line: str
    is_foil: bool
    section: str  # mainboard / commander / companion / sideboard / maybeboard
    prices: dict = field(default_factory=dict)
    urls: dict = field(default_factory=dict)
    scryfall_id: str | None = None
    color_identity: list = field(default_factory=list)
    set_name: str = ""
    set_code: str = ""
    collector_number: str = ""
    # Cheapest (price, url) found across every paper printing of this card via
    # Scryfall search (see fetch_cheapest_printings) -- filled in by
    # build_comparison before pricing happens, None until then / on lookup failure.
    cheapest_nonfoil: tuple | None = None
    cheapest_foil: tuple | None = None


@dataclass
class CardResult:
    entry: CardEntry
    have: int
    shortfall: int
    best: list[tuple[str, float, str]]  # (store, price, url), cheapest first
    owned_scryfall_id: str | None = None  # the exact printing you own, if known
    owned_value: float = 0.0  # today's market value of the copies you're using here
    reserved: int = 0  # copies subtracted from `have` because a saved override reserves them elsewhere


# --------------------------------------------------------------------------
# Moxfield fetching
# --------------------------------------------------------------------------

def parse_deck_id(url_or_id: str) -> str:
    url_or_id = url_or_id.strip()
    match = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)
    # Assume the user passed a bare deck id already.
    return url_or_id.rstrip("/").split("/")[-1]


def fetch_deck(deck_id: str) -> dict:
    url = MOXFIELD_API.format(deck_id=deck_id)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://moxfield.com/decks/{deck_id}",
            "Origin": "https://moxfield.com",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Moxfield deck '{deck_id}' not found (404). Check the URL.")
        raise ValueError(f"Moxfield API returned HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach Moxfield API: {e.reason}")


def extract_entries(deck: dict, include_sideboard: bool, include_maybeboard: bool) -> list[CardEntry]:
    sections = [
        ("mainboard", deck.get("mainboard") or {}),
        ("commander", deck.get("commanders") or {}),
        ("companion", deck.get("companions") or {}),
        ("signature spell", deck.get("signatureSpells") or {}),
    ]
    if include_sideboard:
        sections.append(("sideboard", deck.get("sideboard") or {}))
    if include_maybeboard:
        sections.append(("maybeboard", deck.get("maybeboard") or {}))

    entries: list[CardEntry] = []
    for section_name, board in sections:
        for card_name, info in board.items():
            card = info.get("card") or {}
            if card.get("isToken"):
                continue
            entries.append(
                CardEntry(
                    name=card.get("name", card_name),
                    quantity=info.get("quantity", 1),
                    type_line=card.get("type_line", ""),
                    is_foil=bool(info.get("isFoil")),
                    section=section_name,
                    prices=card.get("prices") or {},
                    urls={
                        "cardKingdomUrl": card.get("cardKingdomUrl"),
                        "cardKingdomFoilUrl": card.get("cardKingdomFoilUrl"),
                        "tcgPlayerUrl": card.get("tcgPlayerUrl"),
                        "cardMarketUrl": card.get("cardMarketUrl"),
                        "coolStuffIncUrl": card.get("coolStuffIncUrl"),
                        "coolStuffIncFoilUrl": card.get("coolStuffIncFoilUrl"),
                        "starcitygames_url": card.get("starcitygames_url"),
                        "cardTraderUrl": card.get("cardTraderUrl"),
                        "cardTraderFoilUrl": card.get("cardTraderFoilUrl"),
                        "manapool_url": card.get("manapool_url"),
                    },
                    scryfall_id=card.get("scryfall_id"),
                    color_identity=card.get("color_identity") or [],
                    set_name=card.get("set_name") or "",
                    set_code=(card.get("set") or "").upper(),
                    collector_number=str(card.get("cn") or ""),
                )
            )
    return entries


# --------------------------------------------------------------------------
# Collection loading
# --------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.replace("’", "'").replace("‘", "'")
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def find_collection_candidates(directory: str) -> list[str]:
    directory = os.path.expanduser(directory)
    pattern = os.path.join(directory, "*.csv")
    candidates = [
        f for f in glob.glob(pattern)
        if os.path.basename(f).lower().startswith("manabox")
    ]
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates


@dataclass
class OwnedPrinting:
    scryfall_id: str
    foil: bool
    etched: bool
    quantity: int


@dataclass
class OwnedCard:
    total: int = 0
    foil: int = 0
    nonfoil: int = 0
    printings: list[OwnedPrinting] = field(default_factory=list)


def load_collection(source) -> dict[str, OwnedCard]:
    """Returns normalized card name -> OwnedCard, aggregated across all printings.
    ManaBox exports `Foil` (normal/foil/etched/...) and `Scryfall ID` per row --
    the exact printing you own -- so owned cards can be valued by their real
    treatment (showcase, extended art, etched foil, ...) instead of whichever
    printing the Moxfield decklist happens to reference.

    `source` is a path (str) or an already-open text-mode file-like object
    (e.g. a Flask upload stream wrapped in io.TextIOWrapper), so this works
    both reading from disk and from an in-memory upload."""
    owned: dict[str, OwnedCard] = {}

    def _read(f):
        reader = csv.DictReader(f)
        if "Name" not in (reader.fieldnames or []):
            raise ValueError(
                f"Doesn't look like a ManaBox export (no 'Name' column found). "
                f"Columns seen: {reader.fieldnames}"
            )
        for row in reader:
            name = normalize_name(row.get("Name", ""))
            if not name:
                continue
            try:
                qty = int(float(row.get("Quantity", "1") or "1"))
            except ValueError:
                qty = 1

            finish = (row.get("Foil") or "normal").strip().lower()
            is_foil = finish != "normal"
            is_etched = "etch" in finish

            card = owned.setdefault(name, OwnedCard())
            card.total += qty
            if is_foil:
                card.foil += qty
            else:
                card.nonfoil += qty

            scryfall_id = (row.get("Scryfall ID") or "").strip()
            if scryfall_id:
                for p in card.printings:
                    if p.scryfall_id == scryfall_id and p.foil == is_foil and p.etched == is_etched:
                        p.quantity += qty
                        break
                else:
                    card.printings.append(OwnedPrinting(scryfall_id, is_foil, is_etched, qty))

    if isinstance(source, (str, os.PathLike)):
        with open(source, newline="", encoding="utf-8-sig") as f:
            _read(f)
    else:
        _read(source)
    return owned


def load_overrides(deck_id: str, directory: str) -> tuple[dict[str, int], str | None]:
    """Looks for a previously-saved overrides file for this deck (from the HTML
    report's "Save Overrides" button, CLI mode) in `directory`, e.g. a browser
    download of `{deck_id}_overrides.json`. Picks the most recently modified
    match if there are several (browsers suffix repeat downloads with " (1)",
    etc). Returns (reserved_by_normalized_name, path_used) -- empty dict if
    none found. (The Flask app has its own server-side project store instead
    of this file-scanning approach -- see app.py.)"""
    directory = os.path.expanduser(directory)
    candidates = glob.glob(os.path.join(directory, f"*{deck_id}*overrides*.json"))
    if not candidates:
        return {}, None
    candidates.sort(key=os.path.getmtime, reverse=True)
    path = candidates[0]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        reserved = data.get("reserved") or {}
        return {str(k): int(v) for k, v in reserved.items()}, path
    except (OSError, ValueError, TypeError):
        return {}, None


# --------------------------------------------------------------------------
# Exact-printing pricing via Scryfall (for cards you already own)
# --------------------------------------------------------------------------

SCRYFALL_CARD_API = "https://api.scryfall.com/cards/{scryfall_id}"


def fetch_scryfall_prices_by_id(scryfall_ids: set[str], on_progress=None) -> dict[str, dict]:
    """Fetch current USD prices for specific printings (by Scryfall card ID).
    One request per unique ID, rate-limited per Scryfall's guidelines. Returns
    {scryfall_id: prices_dict}; failed lookups map to {}.

    `on_progress`, if given, is called as on_progress(done, total) after each
    lookup -- callers can use this to drive a spinner/log line without this
    module needing to know about rich, Flask, or any other UI."""
    cache: dict[str, dict] = {}
    ids = sorted(scryfall_ids)
    if not ids:
        return cache

    for i, scryfall_id in enumerate(ids, 1):
        req = urllib.request.Request(
            SCRYFALL_CARD_API.format(scryfall_id=scryfall_id),
            headers={
                "User-Agent": "moxfield-vs-collection-script/1.0 (personal use)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            cache[scryfall_id] = data.get("prices") or {}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            cache[scryfall_id] = {}
        if on_progress:
            on_progress(i, len(ids))
        time.sleep(0.075)
    return cache


SCRYFALL_SEARCH_API = "https://api.scryfall.com/cards/search"


def fetch_cheapest_printings(card_names: set[str], on_progress=None) -> dict[str, dict]:
    """For each unique card name, search every paper printing on Scryfall and
    keep the cheapest nonfoil/foil price found -- a true baseline price,
    instead of whichever single printing a Moxfield decklist entry happens to
    reference (which can be a rare, dramatically more expensive alt-art).
    Returns {card_name: {"nonfoil": (price, url) | None, "foil": (price, url) | None}}.
    A name that can't be resolved simply maps to {"nonfoil": None, "foil": None}
    so callers can fall back to other price data.

    `on_progress`, if given, is called as on_progress(done, total) once per
    card name (each of which may itself take several paginated requests)."""
    cache: dict[str, dict] = {}
    names = sorted(card_names)
    if not names:
        return cache

    max_pages = 10  # safety cap (1750 printings) -- staple lands can have hundreds
    max_attempts = 3  # transient network hiccups shouldn't silently fall back to worse pricing
    for i, name in enumerate(names, 1):
        best_nonfoil: tuple | None = None
        best_foil: tuple | None = None
        query = f'!"{name}" game:paper'
        url = f"{SCRYFALL_SEARCH_API}?q={urllib.parse.quote(query)}&unique=prints"
        try:
            for _ in range(max_pages):
                if not url:
                    break
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "moxfield-vs-collection-script/1.0 (personal use)",
                        "Accept": "application/json",
                    },
                )
                for attempt in range(max_attempts):
                    try:
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            data = json.loads(resp.read().decode("utf-8"))
                        break
                    except urllib.error.HTTPError as e:
                        if attempt == max_attempts - 1:
                            raise
                        if e.code == 429:
                            # Rate-limited -- back off longer than a plain
                            # network hiccup, honoring Retry-After if sent.
                            retry_after = e.headers.get("Retry-After") if e.headers else None
                            try:
                                delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
                            except ValueError:
                                delay = 1.5 * (attempt + 1)
                            time.sleep(delay)
                        else:
                            time.sleep(0.3 * (attempt + 1))
                    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                        if attempt == max_attempts - 1:
                            raise
                        time.sleep(0.3 * (attempt + 1))
                for card in data.get("data", []):
                    purchase_url = (card.get("purchase_uris") or {}).get("tcgplayer")
                    if not purchase_url:
                        continue
                    prices = card.get("prices") or {}
                    nonfoil_price = prices.get("usd")
                    if nonfoil_price:
                        p = float(nonfoil_price)
                        if best_nonfoil is None or p < best_nonfoil[0]:
                            best_nonfoil = (p, purchase_url)
                    foil_price = prices.get("usd_foil")
                    if foil_price:
                        p = float(foil_price)
                        if best_foil is None or p < best_foil[0]:
                            best_foil = (p, purchase_url)
                url = data.get("next_page") if data.get("has_more") else None
                time.sleep(0.075)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            pass
        cache[name] = {"nonfoil": best_nonfoil, "foil": best_foil}
        if on_progress:
            on_progress(i, len(names))
    return cache


def price_for_printing(scryfall_prices: dict, printing: OwnedPrinting) -> float | None:
    """Pick the right price field for a specific owned printing/finish, falling
    back to whatever finish that printing *does* have priced rather than nothing."""
    if printing.etched and scryfall_prices.get("usd_etched"):
        return float(scryfall_prices["usd_etched"])
    if printing.foil and scryfall_prices.get("usd_foil"):
        return float(scryfall_prices["usd_foil"])
    if scryfall_prices.get("usd"):
        return float(scryfall_prices["usd"])
    if scryfall_prices.get("usd_foil"):
        return float(scryfall_prices["usd_foil"])
    if scryfall_prices.get("usd_etched"):
        return float(scryfall_prices["usd_etched"])
    return None


def select_used_printings(
    owned_card: OwnedCard, owned_used: int, prefer_foil: bool
) -> tuple[list[tuple[OwnedPrinting, int]], int]:
    """Pick which specific owned printings cover the `owned_used` copies that
    go into this deck, preferring the finish the decklist wants first. Returns
    (picks, remainder) where remainder is copies not covered by any printing
    that has a Scryfall ID on file (e.g. a row that failed to match on import)."""
    printings = sorted(owned_card.printings, key=lambda p: p.foil != prefer_foil)
    remaining = owned_used
    picks: list[tuple[OwnedPrinting, int]] = []
    for p in printings:
        if remaining <= 0:
            break
        take = min(p.quantity, remaining)
        if take > 0:
            picks.append((p, take))
            remaining -= take
    return picks, remaining


# --------------------------------------------------------------------------
# Categorization, pricing & comparison
# --------------------------------------------------------------------------

def categorize(type_line: str) -> str:
    for bucket in TYPE_PRECEDENCE:
        if bucket in type_line:
            return PLURALS.get(bucket, bucket if bucket.endswith("s") else bucket + "s")
    return "Other"


# --------------------------------------------------------------------------
# Shopping-list grouping (by color, the way a store's binders are organized)
# --------------------------------------------------------------------------

WUBRG = ["W", "U", "B", "R", "G"]
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
_GROUP_RANK = {"Colors": 0, "Artifacts": 1, "Lands": 2}
_SUBGROUP_RANK = {"Multicolor": 0, "White": 1, "Blue": 2, "Black": 3, "Red": 4, "Green": 5, "Colorless": 99}


def shopping_group(entry: CardEntry) -> tuple[str, str]:
    """Groups a card the way a physical store organizes its binders: Lands and
    Artifacts are their own top-level groups; everything else falls under
    Colors, subdivided into the five single-color bins, one Multicolor bin (a
    store keeps one gold binder, not one per exact color pair), and Colorless.
    Returns (group, subgroup) -- subgroup is "" for Lands/Artifacts."""
    if "Land" in entry.type_line:
        return "Lands", ""
    if "Artifact" in entry.type_line:
        return "Artifacts", ""
    colors = [c for c in WUBRG if c in entry.color_identity]
    if not colors:
        subgroup = "Colorless"
    elif len(colors) == 1:
        subgroup = COLOR_NAMES[colors[0]]
    else:
        subgroup = "Multicolor"
    return "Colors", subgroup


def shopping_group_rank(group: str, subgroup: str) -> str:
    """Zero-padded sort key so a plain string comparison in JS orders groups
    correctly: Colors (mono x5, Multicolor, Colorless), Artifacts, Lands."""
    g = _GROUP_RANK.get(group, 9)
    s = _SUBGROUP_RANK.get(subgroup, 50)
    return f"{g:02d}{s:03d}"


def best_prices(entry: CardEntry, foil: bool | None = None, max_results: int = 3,
                 strict: bool = False) -> list[tuple[str, float, str]]:
    """Returns up to max_results (store, price, url) tuples, cheapest first.

    `foil` picks which finish to price -- defaults to the finish the decklist entry
    itself specifies (entry.is_foil). Foil and nonfoil prices come from separate
    fields and can differ a lot, so pass foil=True/False explicitly to price the
    other finish.

    Prefers the cheapest price found across *every* printing of the card (see
    fetch_cheapest_printings / entry.cheapest_nonfoil/foil) -- a true baseline,
    since whichever single printing a Moxfield decklist entry happens to
    reference can be a rare, dramatically more expensive alt-art. Falls back to
    Moxfield's own referenced-printing price/link data if no Scryfall-search
    price was found for this finish (e.g. the search failed or the card
    couldn't be resolved).

    By default, if the requested finish has no listed price, we fall back to
    whatever finish *is* priced (better than showing nothing). Pass
    strict=True to disable that fallback -- used when a caller wants to know
    specifically whether that finish is priced (e.g. the HTML foil/nonfoil toggle).
    """
    want_foil = entry.is_foil if foil is None else foil

    cheapest = entry.cheapest_foil if want_foil else entry.cheapest_nonfoil
    if cheapest is None and not strict:
        cheapest = entry.cheapest_nonfoil if want_foil else entry.cheapest_foil
    if cheapest:
        return [("TCGP", cheapest[0], cheapest[1])]

    results = []
    for label, nonfoil_key, foil_key, nonfoil_url_key, foil_url_key in STORES:
        key = foil_key if want_foil else nonfoil_key
        price = entry.prices.get(key)
        if price in (None, 0) and not strict:
            other_key = nonfoil_key if want_foil else foil_key
            price = entry.prices.get(other_key)
        if price in (None, 0):
            continue

        url_key = foil_url_key if want_foil else nonfoil_url_key
        url = entry.urls.get(url_key)
        if not url:
            continue
        if label == "CK" and not url.startswith("http"):
            url = CARD_KINGDOM_BASE + url

        results.append((label, float(price), url))

    results.sort(key=lambda r: r[1])
    return results[:max_results]


def priced_for_finish(entry: CardEntry, want_foil: bool, max_results: int = 3):
    """Prefer `want_foil`'s pricing; if that finish isn't listed anywhere, fall
    back to the other finish rather than showing nothing. Returns
    (price_list, used_foil) -- used_foil tells you which finish the prices
    actually reflect, in case that differs from what was requested."""
    results = best_prices(entry, foil=want_foil, max_results=max_results, strict=True)
    if results:
        return results, want_foil
    fallback = best_prices(entry, foil=not want_foil, max_results=max_results, strict=True)
    return fallback, (not want_foil)


def build_comparison(
    entries: list[CardEntry], owned_collection: dict[str, OwnedCard], ignore_basics: bool,
    overrides: dict[str, int] | None = None, on_progress=None,
) -> tuple[list[str], dict[str, list[CardResult]], dict]:
    # Pass 1: figure out have/shortfall per entry, and which exact owned
    # printings (by Scryfall ID) cover the copies that go into this deck.
    # `overrides` reserves copies for other decks (saved from a previous HTML
    # report run) -- they're subtracted from `have` before anything else, so a
    # reserved card behaves exactly like a genuinely-missing one.
    per_entry = []
    needed_ids: set[str] = set()
    for e in entries:
        owned_card = owned_collection.get(normalize_name(e.name))
        raw_have = owned_card.total if owned_card else 0
        reserved_qty = (overrides or {}).get(normalize_name(e.name), 0)
        have = max(0, raw_have - reserved_qty)
        shortfall = max(0, e.quantity - have)
        owned_used = e.quantity - shortfall

        picks: list[tuple[OwnedPrinting, int]] = []
        remainder = 0
        if owned_card and owned_used:
            picks, remainder = select_used_printings(owned_card, owned_used, prefer_foil=e.is_foil)
            needed_ids.update(p.scryfall_id for p, _ in picks)

        per_entry.append((e, have, shortfall, owned_used, picks, remainder, reserved_qty))

    # Pass 2: one Scryfall lookup per unique owned printing that's actually in
    # this deck (not your whole collection), so owned cards are valued by the
    # exact treatment you hold rather than whichever printing Moxfield's
    # decklist happens to reference. Also look up the cheapest printing of
    # every card by name (owned and missing alike -- owned cards' hidden "need
    # another copy" panel uses it too), so shortfall/replacement costs reflect
    # a true baseline instead of whichever printing the decklist references.
    all_names = {e.name for e in entries}
    total_work = len(needed_ids) + len(all_names)

    def _scaled_progress(offset):
        if not on_progress or not total_work:
            return None

        def _p(done, _total):
            on_progress(offset + done, total_work)

        return _p

    price_cache = fetch_scryfall_prices_by_id(needed_ids, on_progress=_scaled_progress(0))
    cheapest_cache = fetch_cheapest_printings(all_names, on_progress=_scaled_progress(len(needed_ids)))
    for e in entries:
        hit = cheapest_cache.get(e.name) or {}
        e.cheapest_nonfoil = hit.get("nonfoil")
        e.cheapest_foil = hit.get("foil")

    buckets: dict[str, list[CardResult]] = {}
    totals = {
        "owned": 0, "missing": 0,
        "cost_nonfoil": 0.0, "cost_foil": 0.0,
        "deck_value": 0.0, "owned_value": 0.0,
        "unpriced_count": 0,  # cards (owned or missing) we couldn't find any price for
    }

    for e, have, shortfall, owned_used, picks, remainder, reserved_qty in per_entry:
        if ignore_basics and "Basic" in e.type_line and "Land" in e.type_line:
            bucket = "Basic Lands"
        else:
            bucket = categorize(e.type_line)

        prices = best_prices(e) if shortfall else []

        owned_value = 0.0
        for printing, qty in picks:
            price = price_for_printing(price_cache.get(printing.scryfall_id) or {}, printing)
            if price is not None:
                owned_value += price * qty
            else:
                totals["unpriced_count"] += 1
        if remainder:
            # Copies with no identified printing (e.g. missing Scryfall ID on
            # import) -- fall back to the deck's own reference-printing price.
            fallback = best_prices(e)
            if fallback:
                owned_value += fallback[0][1] * remainder
            else:
                totals["unpriced_count"] += 1

        totals["owned_value"] += owned_value
        totals["deck_value"] += owned_value

        if shortfall:
            totals["owned"] += owned_used
            totals["missing"] += shortfall
            nonfoil_prices = best_prices(e, foil=False)
            foil_prices = best_prices(e, foil=True)
            if nonfoil_prices:
                totals["cost_nonfoil"] += nonfoil_prices[0][1] * shortfall
            if foil_prices:
                totals["cost_foil"] += foil_prices[0][1] * shortfall
            if prices:
                totals["deck_value"] += prices[0][1] * shortfall
            else:
                totals["unpriced_count"] += 1
        else:
            totals["owned"] += e.quantity

        owned_scryfall_id = picks[0][0].scryfall_id if picks else None
        buckets.setdefault(bucket, []).append(
            CardResult(e, have, shortfall, prices, owned_scryfall_id, owned_value, reserved_qty)
        )

    for cards in buckets.values():
        cards.sort(key=lambda r: r.entry.name)

    bucket_names = sorted(buckets.keys(), key=lambda b: BUCKET_ORDER.index(b) if b in BUCKET_ORDER else len(BUCKET_ORDER))
    return bucket_names, buckets, totals


# --------------------------------------------------------------------------
# Rendering: markdown report + CSV
# --------------------------------------------------------------------------

def render_markdown(deck_name: str, deck_url: str, bucket_names: list[str],
                     buckets: dict[str, list[CardResult]], totals: dict) -> tuple[str, list[dict]]:
    lines: list[str] = [f"# {deck_name}", f"Source: {deck_url}", "",
                         f"Cards owned: **{totals['owned']}** &nbsp;|&nbsp; "
                         f"Cards missing: **{totals['missing']}** &nbsp;|&nbsp; "
                         f"Est. cost to complete (cheapest store per card): "
                         f"**${totals['cost_nonfoil']:.2f}** non-foil / **${totals['cost_foil']:.2f}** foil",
                         "",
                         f"Total deck value (today's market, priced by the finish you actually own): "
                         f"**${totals['deck_value']:.2f}** &nbsp;|&nbsp; "
                         f"owned portion: **${totals['owned_value']:.2f}**",
                         ""]
    missing_rows: list[dict] = []

    for bucket in bucket_names:
        cards = buckets[bucket]
        lines.append(f"## {bucket} ({len(cards)})")
        lines.append("")

        owned_lines, missing_lines = [], []
        for r in cards:
            e = r.entry
            commander_str = " (Commander)" if e.section == "commander" else ""
            if r.shortfall == 0:
                owned_lines.append(f"- [x] {e.name}{commander_str} (need {e.quantity}, have {r.have})")
            else:
                price_str = ""
                if r.best:
                    parts = [f"[{label} ${price:.2f}]({url})" for label, price, url in r.best]
                    price_str = " | " + " · ".join(parts)
                have_str = f", have {r.have}" if r.have else ""
                foil_str = " (foil)" if e.is_foil else ""
                missing_lines.append(
                    f"- [ ] {e.name}{commander_str}{foil_str} — need {r.shortfall} more "
                    f"(deck wants {e.quantity}{have_str}){price_str}"
                )
                missing_rows.append({
                    "name": e.name,
                    "bucket": bucket,
                    "needed": r.shortfall,
                    "foil": e.is_foil,
                    "cheapest_price": f"{r.best[0][1]:.2f}" if r.best else "",
                    "cheapest_store": r.best[0][0] if r.best else "",
                    "cheapest_url": r.best[0][2] if r.best else "",
                })

        if owned_lines:
            lines.append(f"**Already own ({len(owned_lines)}):**")
            lines.extend(owned_lines)
            lines.append("")
        if missing_lines:
            lines.append(f"**Need to acquire ({len(missing_lines)}):**")
            lines.extend(missing_lines)
            lines.append("")

    return "\n".join(lines), missing_rows


def write_missing_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "bucket", "needed", "foil", "cheapest_price",
                        "cheapest_store", "cheapest_url"],
        )
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------
# Rendering: standalone HTML report
# --------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg: #0f1117;
  --bg-elevated: #171a23;
  --card-bg: #1c1f2a;
  --card-border: #2a2f3d;
  --text: #e8e9ee;
  --text-dim: #8b90a3;
  --accent: #7dd3fc;
  --owned: #4ade80;
  --missing: #fb7185;
  --gold: #facc15;
  --shadow: 0 4px 16px rgba(0,0,0,0.35);
}}
@media (prefers-color-scheme: light) {{
  :root {{
    --bg: #f4f5f8;
    --bg-elevated: #ffffff;
    --card-bg: #ffffff;
    --card-border: #e2e4ea;
    --text: #1a1c23;
    --text-dim: #63677a;
    --accent: #0284c7;
    --owned: #16a34a;
    --missing: #e11d48;
    --gold: #ca8a04;
    --shadow: 0 2px 10px rgba(20,20,30,0.08);
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.4;
  min-height: 100vh;
}}
body::before {{
  content: "";
  position: fixed;
  inset: -40px;
  background-image: var(--commander-bg-url, none);
  background-size: cover;
  background-position: center top;
  background-repeat: no-repeat;
  filter: blur(16px);
  z-index: -2;
  pointer-events: none;
}}
body::after {{
  content: "";
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, var(--bg) 68%, transparent);
  z-index: -1;
  pointer-events: none;
}}
header {{
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--card-border);
  padding: 20px 24px;
  box-shadow: var(--shadow);
}}
.header-top {{
  display: flex;
  align-items: center;
  gap: 14px;
}}
.header-actions {{
  margin-left: auto;
  flex-shrink: 0;
  display: flex;
  gap: 8px;
}}
.home-link, .shutdown-btn {{
  color: var(--text-dim);
  font-size: 0.85rem;
  text-decoration: none;
  white-space: nowrap;
  padding: 7px 14px;
  border: 1px solid var(--card-border);
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
}}
.home-link:hover {{ color: var(--accent); border-color: var(--accent); }}
.shutdown-btn:hover {{ color: var(--missing); border-color: var(--missing); }}
.card-thumb.commander-thumb {{
  width: 60px;
  height: 84px;
}}
header h1 {{
  margin: 0 0 4px;
  font-size: 1.4rem;
}}
header .source a {{
  color: var(--text-dim);
  font-size: 0.85rem;
  text-decoration: none;
}}
header .source a:hover {{ color: var(--accent); }}
.stats {{
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  margin-top: 14px;
  align-items: center;
}}
.stat {{ font-size: 0.95rem; }}
.stat b {{ font-size: 1.1rem; }}
.stat.owned b {{ color: var(--owned); }}
.stat.missing b {{ color: var(--missing); }}
.stat.cost b {{ color: var(--gold); }}
.stat.value b {{ color: var(--accent); }}
.stat-sub {{ color: var(--text-dim); font-size: 0.8rem; }}
.progress {{
  flex: 1 1 200px;
  min-width: 160px;
  height: 8px;
  background: var(--card-border);
  border-radius: 4px;
  overflow: hidden;
}}
.progress > div {{
  height: 100%;
  background: linear-gradient(90deg, var(--owned), var(--accent));
}}
.controls {{
  display: flex;
  gap: 12px;
  margin-top: 14px;
  flex-wrap: wrap;
  align-items: center;
}}
#search {{
  flex: 1 1 240px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--card-border);
  background: var(--bg);
  color: var(--text);
  font-size: 0.9rem;
}}
#search:focus {{ outline: 2px solid var(--accent); }}
label.toggle {{
  font-size: 0.85rem;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
}}
.segmented {{
  display: inline-flex;
  border: 1px solid var(--card-border);
  border-radius: 8px;
  overflow: hidden;
}}
.segmented .seg-btn {{
  padding: 8px 14px;
  font-size: 0.85rem;
  font-family: inherit;
  background: var(--bg);
  color: var(--text-dim);
  border: none;
  cursor: pointer;
}}
.segmented .seg-btn:first-child {{ border-right: 1px solid var(--card-border); }}
.segmented .seg-btn:hover {{ color: var(--text); }}
.segmented .seg-btn.active {{
  background: var(--accent);
  color: #0f1117;
  font-weight: 600;
}}
.price-filter {{
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;
  color: var(--text-dim);
}}
.price-filter label {{ display: flex; align-items: center; gap: 4px; white-space: nowrap; }}
.price-filter #price-filter-value {{
  color: var(--text);
  font-weight: 600;
  display: inline-block;
  min-width: {value_box_ch}ch;
  text-align: center;
}}
.price-filter input[type="range"] {{
  width: 140px;
  accent-color: var(--accent);
}}
main {{ padding: 20px 24px 60px; max-width: 1100px; margin: 0 auto; }}
details.bucket {{
  margin-bottom: 14px;
  border: 1px solid var(--card-border);
  border-radius: 12px;
  background: var(--bg-elevated);
  overflow: hidden;
}}
details.bucket > summary {{
  cursor: pointer;
  padding: 12px 16px;
  font-weight: 600;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 10px;
}}
details.bucket > summary::-webkit-details-marker {{ display: none; }}
details.bucket > summary::before {{
  content: "▸";
  color: var(--text-dim);
  transition: transform 0.15s ease;
}}
details.bucket[open] > summary::before {{ transform: rotate(90deg); }}
.bucket-count {{ color: var(--text-dim); font-weight: 400; font-size: 0.85rem; }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 10px;
  padding: 4px 16px 16px;
}}
.card {{
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-radius: 10px;
  border-left: 3px solid var(--card-border);
  background: var(--card-bg);
  padding: 8px 12px 10px;
  box-shadow: var(--shadow);
  position: relative;
}}
.card-main {{ display: flex; gap: 10px; }}
.card.owned {{ border-left-color: var(--owned); }}
.card.missing {{ border-left-color: var(--missing); }}
.card-thumb {{
  width: 48px;
  height: 67px;
  border-radius: 5px;
  object-fit: cover;
  flex-shrink: 0;
  background: var(--card-border);
  cursor: zoom-in;
}}
#hover-preview {{
  position: fixed;
  pointer-events: none;
  z-index: 100;
  display: none;
  width: 240px;
  border-radius: 4.75% / 3.5%;
  box-shadow: 0 12px 32px rgba(0,0,0,0.5), 0 0 0 1px var(--card-border);
}}
#hover-preview.show {{ display: block; }}
.card-body {{
  flex: 1;
  min-width: 0;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}}
.card-info {{ flex: 1; min-width: 0; }}
.card-prices {{
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  max-width: 50%;
}}
.card-name {{
  font-weight: 600;
  font-size: 0.95rem;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}}
.color-icons {{
  display: inline-flex;
  gap: 2px;
  align-items: center;
  flex-shrink: 0;
}}
.mana-icon {{
  width: 14px;
  height: 14px;
  display: block;
}}
.icon {{ font-size: 0.85rem; }}
.card.owned .icon {{ color: var(--owned); }}
.card.missing .icon {{ color: var(--missing); }}
.card.owned .icon-missing {{ display: none; }}
.card.missing .icon-owned {{ display: none; }}
.badge {{
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--card-border);
  color: var(--text-dim);
}}
.qty {{
  color: var(--text-dim);
  font-size: 0.8rem;
  margin: 3px 0 6px;
}}
.card.owned .qty-missing {{ display: none; }}
.card.missing .qty-owned {{ display: none; }}
.card.owned .card-prices {{ display: none; }}
.override-toggle {{
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding-bottom: 5px;
  margin-bottom: 1px;
  border-bottom: 1px solid var(--card-border);
  font-size: 0.7rem;
  color: var(--text-dim);
  cursor: pointer;
  user-select: none;
}}
.override-toggle input {{ accent-color: var(--missing); cursor: pointer; flex-shrink: 0; }}
.card.missing .override-toggle {{ color: var(--missing); border-bottom-color: color-mix(in srgb, var(--missing) 35%, var(--card-border)); }}
.prices {{
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}}
.price-pill {{
  font-size: 0.75rem;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--bg);
  border: 1px solid var(--card-border);
  color: var(--text);
  text-decoration: none;
  white-space: nowrap;
}}
.price-pill:hover {{ border-color: var(--accent); color: var(--accent); }}
.price-pill.best {{
  background: color-mix(in srgb, var(--gold) 18%, var(--card-bg));
  border-color: var(--gold);
  color: var(--gold);
  font-weight: 600;
}}
.no-price {{ color: var(--text-dim); font-size: 0.75rem; font-style: italic; text-align: right; }}
.finish-note {{ color: var(--text-dim); font-size: 0.7rem; font-style: italic; margin-bottom: 3px; text-align: right; }}
.prices-foil {{ display: none; }}
body.show-foil-prices .prices-foil {{ display: block; }}
body.show-foil-prices .prices-nonfoil {{ display: none; }}
footer {{
  text-align: center;
  color: var(--text-dim);
  font-size: 0.75rem;
  padding: 20px;
}}
.card.hidden {{ display: none; }}
.btn {{
  cursor: pointer;
  border: none;
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 0.85rem;
  font-weight: 600;
  font-family: inherit;
}}
.btn.primary {{ background: var(--gold); color: #241f00; }}
.btn.primary:hover {{ filter: brightness(1.08); }}
.btn.ghost {{
  background: transparent;
  border: 1px solid var(--card-border);
  color: var(--text);
}}
.btn.ghost:hover {{ border-color: var(--accent); color: var(--accent); }}
.modal-overlay {{
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  z-index: 50;
  align-items: center;
  justify-content: center;
  padding: 20px;
}}
.modal-overlay.open {{ display: flex; }}
.modal {{
  background: var(--bg-elevated);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 20px;
  max-width: 460px;
  width: 100%;
  box-shadow: var(--shadow);
}}
.modal h2 {{ margin: 0 0 4px; font-size: 1.1rem; }}
.modal p.hint {{ margin: 0 0 12px; color: var(--text-dim); font-size: 0.8rem; }}
.modal textarea {{
  width: 100%;
  height: 320px;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  padding: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
  resize: vertical;
}}
.modal-actions {{
  display: flex;
  gap: 10px;
  margin-top: 12px;
  justify-content: flex-end;
}}
</style>
</head>
<body{body_attrs}>
<header>
  <div class="header-top">
    {commander_thumbs_html}
    <div class="header-title">
      <h1>{title}</h1>
      <div class="source"><a href="{deck_url}" target="_blank" rel="noopener noreferrer">{deck_url}</a></div>
    </div>
    {header_actions_html}
  </div>
  <div class="stats">
    <div class="stat owned">Owned <b id="stat-owned">{owned}</b></div>
    <div class="stat missing">Missing <b id="stat-missing">{missing}</b></div>
    <div class="stat cost">Est. cost to complete <b id="stat-cost-nonfoil">${cost_nonfoil:.2f}</b> non-foil &nbsp;/&nbsp; <b id="stat-cost-foil">${cost_foil:.2f}</b> foil</div>
    <div class="stat value">Total deck value (today's market) <b id="stat-deck-value">${deck_value:.2f}</b> &nbsp;<span class="stat-sub">owned portion <span id="stat-owned-value">${owned_value:.2f}</span></span></div>
    <div class="progress"><div id="progress-bar" style="width:{pct:.1f}%"></div></div>
  </div>
  <div class="controls">
    <input id="search" type="search" placeholder="Filter cards by name...">
    <label class="toggle"><input type="checkbox" id="missing-only"> Show only missing</label>
    <div class="segmented" id="price-finish-toggle" title="Switches the buy links on missing cards between foil and non-foil pricing">
      <button type="button" class="seg-btn active" data-value="nonfoil">Non-foil</button>
      <button type="button" class="seg-btn" data-value="foil">Foil</button>
    </div>
    <div class="price-filter" title="Hide missing cards above this price (uses whichever finish is currently shown)">
      <label for="price-filter">Max price <span id="price-filter-value">${slider_max}</span></label>
      <input type="range" id="price-filter" min="0" max="{slider_max}" step="1" value="{slider_max}">
    </div>
    {shopping_button}
    {instore_button}
    <button class="btn ghost" id="save-overrides-btn" title="{save_overrides_title}">&#128190; Save Overrides</button>
  </div>
</header>
<main>
{buckets_html}
</main>
<footer>Generated {generated} &middot; {deck_name}</footer>

<img id="hover-preview" alt="">

<div class="modal-overlay" id="modal-overlay">
  <div class="modal">
    <h2>Shopping list</h2>
    <p class="hint">One line per missing card currently shown (respects your search/price filters), "qty name" &mdash; paste into a store's bulk/mass-entry search box.</p>
    <textarea id="shopping-list-text" readonly></textarea>
    <div class="modal-actions">
      <button class="btn ghost" id="modal-close">Close</button>
      <button class="btn primary" id="modal-copy">Copy to Clipboard</button>
    </div>
  </div>
</div>

<script>
const ORIGINAL_OWNED = {owned};
const ORIGINAL_MISSING = {missing};
const ORIGINAL_COST_NONFOIL = {cost_nonfoil:.4f};
const ORIGINAL_COST_FOIL = {cost_foil:.4f};
const ORIGINAL_DECK_VALUE = {deck_value:.4f};
const ORIGINAL_OWNED_VALUE = {owned_value:.4f};
const DECK_ID = {deck_id_json};
const DECK_NAME = {deck_name_json};

const search = document.getElementById('search');
const missingOnly = document.getElementById('missing-only');
const priceFilter = document.getElementById('price-filter');
const priceFilterValue = document.getElementById('price-filter-value');
const priceFilterMax = priceFilter ? Number(priceFilter.max) : 0;

function updateOverrideCounts() {{
  document.querySelectorAll('details.bucket').forEach(section => {{
    const total = section.querySelectorAll('.card').length;
    const missingN = section.querySelectorAll('.card.missing').length;
    const countEl = section.querySelector('.bucket-count');
    if (countEl) countEl.textContent = '(' + total + ' · ' + missingN + ' missing)';
  }});

  // ORIGINAL_* already reflect any overrides loaded from a saved JSON file (the
  // Python side computed them with those reservations applied), so a checkbox
  // that came pre-checked needs NO delta -- only a checkbox whose current state
  // *differs* from what it started as should move the numbers, and in whichever
  // direction that change implies (checking new ones subtracts, unchecking a
  // pre-existing reservation adds back).
  let owned = ORIGINAL_OWNED, missing = ORIGINAL_MISSING;
  let costNonfoil = ORIGINAL_COST_NONFOIL, costFoil = ORIGINAL_COST_FOIL;
  let deckValue = ORIGINAL_DECK_VALUE, ownedValue = ORIGINAL_OWNED_VALUE;

  document.querySelectorAll('.need-override').forEach(cb => {{
    const card = cb.closest('.card');
    const wasPreexisting = card.dataset.reservedQty === '1';
    if (cb.checked === wasPreexisting) return;  // matches baked-in baseline, no delta

    const sign = cb.checked ? 1 : -1;
    const qty = Number(card.dataset.qty) || 0;
    const priceNonfoil = card.dataset.priceNonfoil ? Number(card.dataset.priceNonfoil) : 0;
    const priceFoil = card.dataset.priceFoil ? Number(card.dataset.priceFoil) : 0;
    const ownedVal = card.dataset.ownedValue ? Number(card.dataset.ownedValue) : 0;
    owned -= sign * qty;
    missing += sign * qty;
    costNonfoil += sign * priceNonfoil * qty;
    costFoil += sign * priceFoil * qty;
    deckValue += sign * ((priceNonfoil * qty) - ownedVal);
    ownedValue -= sign * ownedVal;
  }});

  document.getElementById('stat-owned').textContent = owned;
  document.getElementById('stat-missing').textContent = missing;
  document.getElementById('stat-cost-nonfoil').textContent = '$' + costNonfoil.toFixed(2);
  document.getElementById('stat-cost-foil').textContent = '$' + costFoil.toFixed(2);
  document.getElementById('stat-deck-value').textContent = '$' + deckValue.toFixed(2);
  document.getElementById('stat-owned-value').textContent = '$' + ownedValue.toFixed(2);
  const total = owned + missing;
  const pct = total ? (owned / total * 100) : 100;
  document.getElementById('progress-bar').style.width = pct.toFixed(1) + '%';
}}

document.querySelectorAll('.need-override').forEach(cb => {{
  cb.addEventListener('change', () => {{
    const card = cb.closest('.card');
    card.classList.toggle('missing', cb.checked);
    card.classList.toggle('owned', !cb.checked);
    updateOverrideCounts();
    applyFilter();
  }});
}});

function updateShoppingButtonLabel() {{
  const btn = document.getElementById('shopping-list-btn');
  if (!btn) return;
  const count = document.querySelectorAll('.card.missing:not(.hidden)').length;
  btn.textContent = '\U0001f6d2 Shopping List (' + count + (count === 1 ? ' card)' : ' cards)');
}}

function applyFilter() {{
  const q = search.value.trim().toLowerCase();
  const onlyMissing = missingOnly.checked;
  const showFoilPrices = document.body.classList.contains('show-foil-prices');
  const maxPrice = priceFilter ? Number(priceFilter.value) : Infinity;
  const noLimit = priceFilter && Number(priceFilter.value) >= priceFilterMax;

  document.querySelectorAll('.card').forEach(card => {{
    const name = card.dataset.name;
    const isMissing = card.classList.contains('missing');
    const matchesText = !q || name.includes(q);
    const matchesMissing = !onlyMissing || isMissing;

    let matchesPrice = true;
    if (isMissing && !noLimit) {{
      const priceStr = showFoilPrices ? card.dataset.priceFoil : card.dataset.priceNonfoil;
      const price = priceStr ? Number(priceStr) : null;
      matchesPrice = price === null || price <= maxPrice;
    }}

    card.classList.toggle('hidden', !(matchesText && matchesMissing && matchesPrice));
  }});
  document.querySelectorAll('details.bucket').forEach(section => {{
    const visible = section.querySelectorAll('.card:not(.hidden)').length;
    section.style.display = visible ? '' : 'none';
  }});
  updateShoppingButtonLabel();
}}
search.addEventListener('input', applyFilter);
missingOnly.addEventListener('change', applyFilter);

if (priceFilter) {{
  const updatePriceLabel = () => {{
    const v = Number(priceFilter.value);
    priceFilterValue.textContent = v >= priceFilterMax ? 'no limit' : ('$' + v);
  }};
  updatePriceLabel();
  priceFilter.addEventListener('input', () => {{ updatePriceLabel(); applyFilter(); }});
}}

const priceSegBtns = document.querySelectorAll('#price-finish-toggle .seg-btn');
priceSegBtns.forEach(btn => {{
  btn.addEventListener('click', () => {{
    priceSegBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.body.classList.toggle('show-foil-prices', btn.dataset.value === 'foil');
    applyFilter();
  }});
}});

const hoverPreview = document.getElementById('hover-preview');
document.querySelectorAll('.card-thumb[data-full]').forEach(img => {{
  img.addEventListener('mouseenter', () => {{
    hoverPreview.src = img.dataset.full;
    hoverPreview.classList.add('show');
  }});
  img.addEventListener('mousemove', (e) => {{
    const pad = 18, w = 240, h = Math.round(w * 1.4);
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    if (x + w > window.innerWidth) x = e.clientX - w - pad;
    if (y + h > window.innerHeight) y = window.innerHeight - h - pad;
    hoverPreview.style.left = x + 'px';
    hoverPreview.style.top = Math.max(0, y) + 'px';
  }});
  img.addEventListener('mouseleave', () => {{
    hoverPreview.classList.remove('show');
  }});
}});

const shoppingBtn = document.getElementById('shopping-list-btn');
const modalOverlay = document.getElementById('modal-overlay');
const modalClose = document.getElementById('modal-close');
const modalCopy = document.getElementById('modal-copy');
const shoppingText = document.getElementById('shopping-list-text');

function buildShoppingListText() {{
  const cards = Array.from(document.querySelectorAll('.card.missing:not(.hidden)'));
  const lines = cards.map(el => el.dataset.qty + ' ' + el.dataset.displayName);
  lines.sort((a, b) => a.localeCompare(b));
  return lines.join('\\n');
}}

function openModal() {{
  shoppingText.value = buildShoppingListText();
  modalOverlay.classList.add('open');
  shoppingText.focus();
  shoppingText.select();
}}
function closeModal() {{ modalOverlay.classList.remove('open'); }}

if (shoppingBtn) shoppingBtn.addEventListener('click', openModal);
modalClose.addEventListener('click', closeModal);
modalOverlay.addEventListener('click', (e) => {{ if (e.target === modalOverlay) closeModal(); }});
document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') closeModal(); }});

modalCopy.addEventListener('click', () => {{
  shoppingText.select();
  shoppingText.setSelectionRange(0, 999999);
  let copied = false;
  try {{ copied = document.execCommand('copy'); }} catch (err) {{}}
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(shoppingText.value).catch(() => {{}});
  }}
  modalCopy.textContent = copied ? 'Copied!' : 'Selected — press Cmd/Ctrl+C';
  setTimeout(() => {{ modalCopy.textContent = 'Copy to Clipboard'; }}, 1600);
}});

function csvEscape(value) {{
  const s = String(value ?? '');
  if (/["\\n,]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}}

function buildInStoreRows() {{
  const cards = Array.from(document.querySelectorAll('.card.missing:not(.hidden)'));
  const showFoilPrices = document.body.classList.contains('show-foil-prices');
  const rows = cards.map(el => {{
    const price = showFoilPrices ? el.dataset.priceFoil : el.dataset.priceNonfoil;
    const store = showFoilPrices ? el.dataset.storeFoil : el.dataset.storeNonfoil;
    return {{
      sortKey: el.dataset.groupRank + '|' + el.dataset.displayName,
      group: el.dataset.group,
      subgroup: el.dataset.subgroup,
      qty: el.dataset.qty,
      name: el.dataset.displayName,
      type: el.dataset.type,
      setName: el.dataset.setName,
      setCode: el.dataset.setCode,
      cn: el.dataset.cn,
      finish: showFoilPrices ? 'Foil' : 'Non-foil',
      price: price ? Number(price).toFixed(2) : '',
      store: store || '',
    }};
  }});
  rows.sort((a, b) => a.sortKey.localeCompare(b.sortKey));
  return rows;
}}

function downloadInStoreCsv() {{
  const rows = buildInStoreRows();
  const header = ['Group', 'Subgroup', 'Qty', 'Name', 'Type', 'Set', 'Set Code', 'Collector #', 'Finish', 'Price', 'Store'];
  const lines = [header.map(csvEscape).join(',')];
  rows.forEach(r => {{
    lines.push([r.group, r.subgroup, r.qty, r.name, r.type, r.setName, r.setCode, r.cn, r.finish, r.price, r.store]
      .map(csvEscape).join(','));
  }});
  const csv = lines.join('\\r\\n');
  const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (document.title || 'deck').replace(/[^A-Za-z0-9_-]+/g, '_') + '_shopping_list.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}}

const instoreBtn = document.getElementById('instore-list-btn');
if (instoreBtn) instoreBtn.addEventListener('click', downloadInStoreCsv);

{save_overrides_js}

applyFilter();
</script>
</body>
</html>
"""


def _download_overrides_js() -> str:
    return """function downloadOverrides() {
  const reserved = {};
  document.querySelectorAll('.need-override:checked').forEach(cb => {
    const card = cb.closest('.card');
    reserved[card.dataset.normName] = Number(card.dataset.qty) || 1;
  });
  const payload = {
    deck_id: DECK_ID,
    deck_name: DECK_NAME,
    updated: new Date().toISOString(),
    reserved: reserved,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = DECK_ID + '_overrides.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
const saveOverridesBtn = document.getElementById('save-overrides-btn');
if (saveOverridesBtn) saveOverridesBtn.addEventListener('click', downloadOverrides);"""


def _post_overrides_js(overrides_endpoint: str) -> str:
    endpoint_json = json.dumps(overrides_endpoint)
    return f"""const saveOverridesBtn = document.getElementById('save-overrides-btn');
if (saveOverridesBtn) {{
  saveOverridesBtn.addEventListener('click', () => {{
    const reserved = {{}};
    document.querySelectorAll('.need-override:checked').forEach(cb => {{
      const card = cb.closest('.card');
      reserved[card.dataset.normName] = Number(card.dataset.qty) || 1;
    }});
    const original = saveOverridesBtn.textContent;
    saveOverridesBtn.textContent = 'Saving...';
    fetch({endpoint_json}, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ reserved: reserved }}),
    }})
      .then(r => {{ if (!r.ok) throw new Error('save failed'); return r.json(); }})
      .then(() => {{
        saveOverridesBtn.textContent = 'Saved!';
        setTimeout(() => {{ saveOverridesBtn.textContent = original; }}, 1600);
      }})
      .catch(() => {{
        saveOverridesBtn.textContent = 'Save failed';
        setTimeout(() => {{ saveOverridesBtn.textContent = original; }}, 1600);
      }});
  }});
}}

const shutdownBtn = document.getElementById('shutdown-btn');
if (shutdownBtn) {{
  shutdownBtn.addEventListener('click', () => {{
    if (!confirm('Shut down the server? You will need to relaunch it to use this again.')) return;
    fetch('/shutdown', {{ method: 'POST' }}).catch(() => {{}});
    document.body.innerHTML =
      '<main style="max-width:640px;margin:0 auto;padding:40px 24px;">'
      + '<p style="color:var(--text-dim);">Server stopped. You can close this tab.</p></main>';
  }});
}}"""


def render_html(deck_name: str, deck_url: str, deck_id: str, bucket_names: list[str],
                 buckets: dict[str, list[CardResult]], totals: dict,
                 overrides_endpoint: str | None = None) -> str:
    """Renders the full standalone HTML report.

    `overrides_endpoint`, if given (e.g. "/api/overrides/abc123" for the Flask
    app), makes the "Save Overrides" button POST there via fetch() instead of
    the CLI's default behavior of downloading a `{deck_id}_overrides.json`
    file for you to drop into your ManaBox export folder.
    """
    total_cards = totals["owned"] + totals["missing"]
    pct = (totals["owned"] / total_cards * 100) if total_cards else 100.0
    max_card_price = 0.0

    def _display_scryfall_id(r: CardResult) -> str | None:
        # Show *your* printing's art for owned cards, not whichever printing
        # the decklist happens to reference.
        if r.shortfall == 0 and r.owned_scryfall_id:
            return r.owned_scryfall_id
        return r.entry.scryfall_id

    def _thumb_html(scryfall_id: str | None, css_class: str = "card-thumb") -> str:
        img_url = scryfall_image_url(scryfall_id, size="normal")
        thumb_url = scryfall_image_url(scryfall_id, size="small")
        if thumb_url:
            return (
                f'<img class="{css_class}" src="{html.escape(thumb_url)}" '
                f'data-full="{html.escape(img_url)}" alt="" loading="lazy" decoding="async">'
            )
        return f'<div class="{css_class}"></div>'

    commanders = [r for cards in buckets.values() for r in cards if r.entry.section == "commander"]
    commander_thumbs_html = "".join(
        _thumb_html(_display_scryfall_id(r), css_class="card-thumb commander-thumb") for r in commanders
    )

    body_attrs = ""
    if commanders:
        bg_url = scryfall_image_url(_display_scryfall_id(commanders[0]), size="large")
        if bg_url:
            body_attrs = f' style="--commander-bg-url: url(\'{html.escape(bg_url)}\');"'

    def _pills(price_list, note=None):
        if not price_list:
            return '<div class="no-price">no price found</div>'
        note_html = f'<div class="finish-note">{html.escape(note)}</div>' if note else ''
        return note_html + '<div class="prices">' + "".join(
            f'<a class="price-pill{" best" if i == 0 else ""}" '
            f'href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(label)} ${price:.2f}</a>'
            for i, (label, price, url) in enumerate(price_list)
        ) + '</div>'

    bucket_blocks = []
    for bucket in bucket_names:
        cards = buckets[bucket]
        missing_count = sum(1 for r in cards if r.shortfall > 0)
        card_tiles = []
        for r in cards:
            e = r.entry
            name_esc = html.escape(e.name)
            badges = ""
            if e.section == "commander":
                badges += '<span class="badge">Commander</span>'
            if e.is_foil:
                badges += '<span class="badge">Foil</span>'

            card_colors = [c for c in WUBRG if c in e.color_identity]
            color_icons_html = ""
            if card_colors:
                color_icons_html = '<span class="color-icons">' + "".join(
                    f'<img class="mana-icon" src="{html.escape(mana_symbol_url(c))}" alt="{c}" loading="lazy">'
                    for c in card_colors
                ) + '</span>'

            thumb_html = _thumb_html(_display_scryfall_id(r))

            best_nonfoil, nonfoil_used_foil = priced_for_finish(e, want_foil=False)
            best_foil, foil_used_foil = priced_for_finish(e, want_foil=True)
            nonfoil_note = "not sold non-foil — showing foil price" if best_nonfoil and nonfoil_used_foil else None
            foil_note = "not sold as foil — showing non-foil price" if best_foil and not foil_used_foil else None
            prices_nonfoil_html = f'<div class="prices-nonfoil">{_pills(best_nonfoil, nonfoil_note)}</div>'
            prices_foil_html = f'<div class="prices-foil">{_pills(best_foil, foil_note)}</div>'

            nonfoil_cheapest = best_nonfoil[0][1] if best_nonfoil else None
            foil_cheapest = best_foil[0][1] if best_foil else None
            if nonfoil_cheapest is not None:
                max_card_price = max(max_card_price, nonfoil_cheapest)
            if foil_cheapest is not None:
                max_card_price = max(max_card_price, foil_cheapest)
            price_nonfoil_attr = f'{nonfoil_cheapest:.2f}' if nonfoil_cheapest is not None else ''
            price_foil_attr = f'{foil_cheapest:.2f}' if foil_cheapest is not None else ''
            store_nonfoil_attr = html.escape(best_nonfoil[0][0]) if best_nonfoil else ''
            store_foil_attr = html.escape(best_foil[0][0]) if best_foil else ''

            group, subgroup = shopping_group(e)
            group_rank = shopping_group_rank(group, subgroup)
            norm_name_attr = html.escape(normalize_name(e.name))
            shop_data_attrs = (
                f'data-price-nonfoil="{price_nonfoil_attr}" data-price-foil="{price_foil_attr}" '
                f'data-store-nonfoil="{store_nonfoil_attr}" data-store-foil="{store_foil_attr}" '
                f'data-group="{html.escape(group)}" data-subgroup="{html.escape(subgroup)}" data-group-rank="{group_rank}" '
                f'data-set-name="{html.escape(e.set_name)}" data-set-code="{html.escape(e.set_code)}" '
                f'data-cn="{html.escape(e.collector_number)}" data-type="{html.escape(bucket)}" '
                f'data-norm-name="{norm_name_attr}"'
            )

            if r.shortfall == 0:
                reserved_checked = ' checked' if r.reserved > 0 else ''
                reserved_flag = '1' if r.reserved > 0 else '0'
                reserved_note = (
                    f' &middot; {r.reserved} reserved for another deck' if r.reserved > 0 else ''
                )
                card_tiles.append(f"""
<div class="card owned" data-name="{name_esc.lower()}" data-qty="{e.quantity}" data-display-name="{name_esc}" data-owned-value="{r.owned_value:.2f}" data-reserved-qty="{reserved_flag}" {shop_data_attrs}>
  <label class="override-toggle">
    <input type="checkbox" class="need-override"{reserved_checked}> Need another copy (used elsewhere)
  </label>
  <div class="card-main">
    {thumb_html}
    <div class="card-body">
      <div class="card-info">
        <div class="card-name">
          <span class="icon icon-owned">&#10003;</span><span class="icon icon-missing">&#10007;</span>
          {name_esc}{color_icons_html}{badges}
        </div>
        <div class="qty qty-owned">need {e.quantity} &middot; have {r.have}{reserved_note}</div>
        <div class="qty qty-missing">need {e.quantity} more &middot; marked as used in another deck</div>
      </div>
      <div class="card-prices">
        {prices_nonfoil_html}
        {prices_foil_html}
      </div>
    </div>
  </div>
</div>""")
            else:
                have_str = f" &middot; have {r.have}" if r.have else ""
                card_tiles.append(f"""
<div class="card missing" data-name="{name_esc.lower()}" data-qty="{r.shortfall}" data-display-name="{name_esc}" {shop_data_attrs}>
  <div class="card-main">
    {thumb_html}
    <div class="card-body">
      <div class="card-info">
        <div class="card-name"><span class="icon">&#10007;</span>{name_esc}{color_icons_html}{badges}</div>
        <div class="qty">need {r.shortfall} more &middot; deck wants {e.quantity}{have_str}</div>
      </div>
      <div class="card-prices">
        {prices_nonfoil_html}
        {prices_foil_html}
      </div>
    </div>
  </div>
</div>""")

        bucket_blocks.append(f"""<details class="bucket" open>
<summary>{html.escape(bucket)} <span class="bucket-count">({len(cards)} &middot; {missing_count} missing)</span></summary>
<div class="grid">{''.join(card_tiles)}</div>
</details>""")

    any_missing = totals["missing"] > 0
    slider_max = max(1, math.ceil(max_card_price)) if max_card_price else 1
    value_box_ch = max(len(f"${slider_max}"), len("no limit"))
    deck_id_json = json.dumps(deck_id)
    deck_name_json = json.dumps(deck_name)

    if any_missing:
        shopping_button = '<button class="btn primary" id="shopping-list-btn">&#128722; Shopping List</button>'
        instore_button = (
            '<button class="btn ghost" id="instore-list-btn" '
            'title="Download a CSV of what\'s currently shown, grouped like a store\'s binders">'
            '&#127978; Export In-Store CSV</button>'
        )
    else:
        shopping_button = ""
        instore_button = ""

    if overrides_endpoint:
        save_overrides_js = _post_overrides_js(overrides_endpoint)
        save_overrides_title = "Save which owned cards are reserved for other decks -- remembered automatically for this deck"
        header_actions_html = (
            '<div class="header-actions">'
            '<a class="home-link" href="/">&larr; New comparison</a>'
            '<button type="button" class="shutdown-btn" id="shutdown-btn" title="Stops the local server">&#9209; Shut Down</button>'
            '</div>'
        )
    else:
        save_overrides_js = _download_overrides_js()
        save_overrides_title = (
            "Download a JSON file remembering which owned cards are reserved for other decks -- "
            "drop it in your ManaBox export folder and future runs will account for it automatically"
        )
        header_actions_html = ""

    return HTML_TEMPLATE.format(
        title=html.escape(deck_name),
        deck_name=html.escape(deck_name),
        deck_url=html.escape(deck_url),
        owned=totals["owned"],
        missing=totals["missing"],
        cost_nonfoil=totals["cost_nonfoil"],
        cost_foil=totals["cost_foil"],
        deck_value=totals["deck_value"],
        owned_value=totals["owned_value"],
        pct=pct,
        buckets_html="\n".join(bucket_blocks),
        generated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        shopping_button=shopping_button,
        instore_button=instore_button,
        commander_thumbs_html=commander_thumbs_html,
        slider_max=slider_max,
        value_box_ch=value_box_ch,
        body_attrs=body_attrs,
        deck_id_json=deck_id_json,
        deck_name_json=deck_name_json,
        save_overrides_js=save_overrides_js,
        save_overrides_title=save_overrides_title,
        header_actions_html=header_actions_html,
    )
