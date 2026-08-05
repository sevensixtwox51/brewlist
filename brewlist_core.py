"""
Brewlist core: shared logic for comparing a Moxfield or Archidekt decklist
against a ManaBox collection export -- deck/Scryfall fetching, collection
loading, price comparison, and HTML report rendering. No terminal/UI
dependencies (no `rich`, no `input()`) so this module can be imported by both
the CLI (brewlist_cli.py) and the Flask app (app.py).
"""

from __future__ import annotations

import csv
import datetime
import glob
import gzip
import html
import json
import math
import os
import re
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

__all__ = [
    "CardEntry", "CardResult", "OwnedPrinting", "OwnedCard",
    "parse_deck_ref", "deck_key", "split_deck_key", "fetch_deck", "extract_entries",
    "normalize_name", "find_collection_candidates", "load_collection", "load_overrides",
    "fetch_scryfall_prices_by_id", "price_for_printing", "select_used_printings",
    "price_index_age_days", "price_index_built_at", "rebuild_price_index", "ensure_price_index", "game_changers_in_index",
    "load_store_prefs", "save_store_prefs", "STORE_LABELS", "STORE_DISPLAY_NAMES", "update_from_git",
    "EXTRA_STORE_LABELS", "PICKABLE_STORE_LABELS",
    "commander_legality_in_index", "deck_is_commander_format",
    "find_deck_combos", "estimate_deck_bracket", "BRACKET_TAG_LABELS", "scryfall_prices_in_index",
    "budget_alt_data_in_index", "BUDGET_ALT_MIN_PRICE",
    "PRICE_INDEX_PATH", "PRICE_INDEX_MAX_AGE_DAYS",
    "categorize", "shopping_group", "shopping_group_rank",
    "best_prices", "priced_for_finish", "build_comparison",
    "render_markdown", "write_missing_csv", "render_html",
    "scryfall_image_url",
]

MOXFIELD_API = "https://api2.moxfield.com/v2/decks/all/{deck_id}"
ARCHIDEKT_API = "https://archidekt.com/api/decks/{deck_id}/"

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
STORE_LABELS = [s[0] for s in STORES]
# Cardmarket -- a real 4th MTGJSON price provider, but EUR-denominated (every
# other store here is USD), so it's deliberately NOT part of STORES: it never
# participates in "cheapest across stores" sorting or any dollar total, only
# shown as an extra, informational pill when the user opts in. See
# CardEntry.cardmarket_nonfoil/foil and rebuild_price_index.
EXTRA_STORE_LABELS = ["CM"]
PICKABLE_STORE_LABELS = STORE_LABELS + EXTRA_STORE_LABELS
STORE_DISPLAY_NAMES = {"TCGP": "TCGplayer", "CK": "Card Kingdom", "MP": "ManaPool", "CM": "Cardmarket"}

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
    # Per-store cheapest (store, price, url) found across every paper printing
    # of this card, sorted cheapest first -- see ensure_price_index. Filled in
    # by build_comparison before pricing happens, None until then / if no
    # printing of this card was found in the index.
    cheapest_nonfoil: list[tuple[str, float, str]] | None = None
    cheapest_foil: list[tuple[str, float, str]] | None = None
    # Per-store % price change over the last PRICE_TREND_LOOKBACK_DAYS (see
    # rebuild_price_index) -- {"TCGP": 3.2, "CK": -5.1, ...}. None for a
    # store/finish without that much price history yet (e.g. a very
    # recently printed card).
    price_trend_nonfoil: dict[str, float] | None = None
    price_trend_foil: dict[str, float] | None = None
    # Cardmarket's own cheapest-across-printings (price, url) -- EUR, not USD
    # like everything else here, so it's deliberately kept out of
    # cheapest_nonfoil/foil and every dollar total (deck value, cost to
    # complete). Shown informationally only when "CM" is in the user's
    # selected stores. See rebuild_price_index / EXTRA_STORE_LABELS.
    cardmarket_nonfoil: tuple[float, str] | None = None
    cardmarket_foil: tuple[float, str] | None = None
    # Functional-alternative suggestions for this (missing) card -- see
    # budget_alt_data_in_index / _compute_budget_alt_groups. cheaper_alt_tag
    # is the Scryfall Oracle Tag label used to find them (e.g. "mana rock").
    # owned_alternatives: [(display_name, scryfall_id), ...] for same-role
    # cards you already own (free -- shown regardless of their own market
    # price; scryfall_id is your actual owned printing, for a hover-preview
    # image, None if unknown). cheaper_alternatives: [(name, price,
    # scryfall_id), ...] for same-role cards you don't own, cheapest first,
    # guaranteed cheaper than this card's own price (scryfall_id here is
    # just a representative printing, not tied to any specific one you'd
    # buy). Only set for missing cards priced above BUDGET_ALT_MIN_PRICE.
    cheaper_alt_tag: str | None = None
    owned_alternatives: list[tuple[str, str | None]] | None = None
    cheaper_alternatives: list[tuple[str, float, str | None]] | None = None
    # Whether this card is on WotC's official Commander "Game Changers" list
    # -- see game_changers_in_index. Only set (and only meaningful) when
    # build_comparison was told this deck is Commander format; False otherwise.
    is_game_changer: bool = False
    # Commander legality ("Legal", "Banned", "Restricted", ...) if known --
    # see commander_legality_in_index. Only set (and only meaningful) when
    # build_comparison was told this deck is Commander format; None otherwise.
    commander_legality: str | None = None
    # From Commander Spellbook's estimate-bracket lookup (see
    # estimate_deck_bracket) -- only set when is_commander_format and that
    # lookup succeeded; False otherwise (including on lookup failure, so a
    # transient outage never falsely flags a card).
    mass_land_denial: bool = False
    extra_turn: bool = False


@dataclass
class CardResult:
    entry: CardEntry
    have: int
    shortfall: int
    best: list[tuple[str, float, str]]  # (store, price, url), cheapest first
    owned_scryfall_id: str | None = None  # the exact printing you own, if known
    owned_is_foil: bool | None = None  # whether that owned printing is foil; None if not known
    owned_value: float = 0.0  # today's market value of the copies you're using here
    reserved: int = 0  # copies subtracted from `have` because a saved override reserves them elsewhere


# --------------------------------------------------------------------------
# Deck fetching -- Moxfield and Archidekt both have plain public JSON APIs
# (no auth needed), so a deck URL is dispatched to the matching source's
# fetch/extract pair. Everything downstream (build_comparison, pricing, HTML
# rendering) only ever sees the shared CardEntry shape.
# --------------------------------------------------------------------------

def parse_deck_ref(url_or_id: str) -> tuple[str, str]:
    """Returns (source, deck_id) -- source is "moxfield" or "archidekt".
    A bare ID with no recognizable URL defaults to "moxfield" (unchanged
    behavior from before Archidekt support existed)."""
    url_or_id = url_or_id.strip()
    match = re.search(r"archidekt\.com/decks/(\d+)", url_or_id)
    if match:
        return "archidekt", match.group(1)
    match = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", url_or_id)
    if match:
        return "moxfield", match.group(1)
    # Assume the user passed a bare Moxfield deck id already.
    return "moxfield", url_or_id.rstrip("/").split("/")[-1]


def deck_key(source: str, deck_id: str) -> str:
    """Storage/URL key for a deck (project files, override lookups, etc).
    Moxfield keeps its bare deck_id, unprefixed, so anything saved before
    Archidekt support existed still resolves; Archidekt gets a prefix since
    its IDs are plain numbers with no similar built-in collision avoidance."""
    return f"archidekt-{deck_id}" if source == "archidekt" else deck_id


def split_deck_key(key: str) -> tuple[str, str]:
    if key.startswith("archidekt-"):
        return "archidekt", key[len("archidekt-"):]
    return "moxfield", key


def fetch_deck(source: str, deck_id: str) -> dict:
    if source == "archidekt":
        return _fetch_archidekt_deck(deck_id)
    return _fetch_moxfield_deck(deck_id)


def extract_entries(source: str, deck: dict, include_sideboard: bool, include_maybeboard: bool) -> list[CardEntry]:
    if source == "archidekt":
        return _extract_archidekt_entries(deck, include_sideboard, include_maybeboard)
    return _extract_moxfield_entries(deck, include_sideboard, include_maybeboard)


def deck_is_commander_format(source: str, deck: dict) -> bool:
    """Whether this deck is Commander/EDH, used to decide whether it makes
    sense to check cards against Commander's banned list (see
    commander_legality_in_index) -- legality warnings are only ever shown
    for this format, since that's what the rest of this tool (Game Changers,
    bracket-adjacent info) is oriented around anyway.

    Moxfield gives a clean, direct format string. Archidekt's own format
    field is an undocumented numeric enum we don't have a verified mapping
    for, so instead this looks for independent Commander-specific signals
    already present in the deck data (an "edhBracket" value, or a card
    explicitly categorized "Commander") rather than guess the enum wrong."""
    if source == "archidekt":
        if deck.get("edhBracket") is not None:
            return True
        return any("Commander" in (c.get("categories") or []) for c in deck.get("cards", []))
    return (deck.get("format") or "").strip().lower() == "commander"


# --------------------------------------------------------------------------
# Moxfield
# --------------------------------------------------------------------------

def _fetch_moxfield_deck(deck_id: str) -> dict:
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


def _extract_moxfield_entries(deck: dict, include_sideboard: bool, include_maybeboard: bool) -> list[CardEntry]:
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
# Archidekt
# --------------------------------------------------------------------------

def _fetch_archidekt_deck(deck_id: str) -> dict:
    url = ARCHIDEKT_API.format(deck_id=deck_id)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "moxfield-vs-collection-script/1.0 (personal use)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Archidekt deck '{deck_id}' not found (404). Check the URL.")
        raise ValueError(f"Archidekt API returned HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach Archidekt API: {e.reason}")


_ARCHIDEKT_COLOR_LETTERS = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}


def _extract_archidekt_entries(deck: dict, include_sideboard: bool, include_maybeboard: bool) -> list[CardEntry]:
    entries: list[CardEntry] = []
    for c in deck.get("cards", []):
        categories = c.get("categories") or []
        if c.get("companion"):
            section = "companion"
        elif "Commander" in categories:
            section = "commander"
        elif "Sideboard" in categories:
            if not include_sideboard:
                continue
            section = "sideboard"
        elif "Maybeboard" in categories:
            if not include_maybeboard:
                continue
            section = "maybeboard"
        else:
            section = "mainboard"

        card = c.get("card") or {}
        oracle = card.get("oracleCard") or {}
        edition = card.get("edition") or {}
        prices = card.get("prices") or {}
        tcg_product_id = card.get("tcgProductId")

        # Archidekt's own price blob covers CK/Cardmarket/ManaPool too, but
        # only gives raw vendor product IDs for TCGPlayer (not CK/MP), so a
        # clickable link is only reliably buildable for TCGPlayer here. This
        # is just the fast-path fallback anyway -- "Get Accurate Prices"
        # (the local MTGJSON-based price index) works identically regardless
        # of source, since it only ever needs the card name.
        entries.append(
            CardEntry(
                name=oracle.get("name", ""),
                quantity=c.get("quantity", 1),
                type_line=" ".join((oracle.get("superTypes") or []) + (oracle.get("types") or [])),
                is_foil="foil" in (c.get("modifier") or "").lower(),
                section=section,
                prices={"usd": prices.get("tcg"), "usd_foil": prices.get("tcgfoil")},
                urls={
                    "tcgPlayerUrl": f"https://www.tcgplayer.com/product/{tcg_product_id}" if tcg_product_id else None,
                },
                scryfall_id=card.get("uid"),
                color_identity=[
                    _ARCHIDEKT_COLOR_LETTERS[name]
                    for name in (oracle.get("colorIdentity") or [])
                    if name in _ARCHIDEKT_COLOR_LETTERS
                ],
                set_name=edition.get("editionname") or "",
                set_code=(edition.get("editioncode") or "").upper(),
                collector_number=str(card.get("collectorNumber") or ""),
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
SCRYFALL_BULK_DATA_API = "https://api.scryfall.com/bulk-data"


def _scryfall_bulk_download_url(bulk_type: str) -> str | None:
    """Looks up the current JSONL download URL for a Scryfall bulk-data file
    type (e.g. "oracle_tags") -- these URLs are timestamped and regenerated
    whenever Scryfall rebuilds the file, so they can't be hardcoded. Returns
    None on any failure (network, unexpected response shape, type not
    found) -- callers should treat that as "this optional data isn't
    available right now", not a hard error."""
    try:
        req = urllib.request.Request(
            SCRYFALL_BULK_DATA_API,
            headers={"User-Agent": "brewlist/1.0 (personal use)", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for entry in data.get("data") or []:
            if entry.get("type") == bulk_type:
                return entry.get("jsonl_download_uri")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        pass
    return None


def fetch_scryfall_prices_by_id(scryfall_ids: set[str], on_progress=None) -> dict[str, dict]:
    """Fetch current USD prices for specific printings (by Scryfall card ID),
    live from Scryfall's API, one request per unique ID. This is now only
    the rare fallback build_comparison uses for a scryfall_id missing from
    the local MTGJSON-derived index (see scryfall_prices_in_index) -- e.g. a
    printing released after the index's last weekly refresh. Returns
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


# --------------------------------------------------------------------------
# Cheapest-printing baseline pricing -- a local index built from MTGJSON's
# public bulk data (AllPrintings for card identity + official purchase
# links, AllPrices for 90 days of retail price history), reduced to the
# cheapest paper nonfoil/foil price at each of TCGPlayer, Card Kingdom, and
# ManaPool per unique card name, plus a 7-day price-trend indicator computed
# from that same history. This replaced an earlier per-card live-search
# implementation: with ~90 unique names in a typical deck, live searches
# routinely tripped Scryfall's rate limiter. A combined ~325MB download
# refreshed on a schedule (see PRICE_INDEX_MAX_AGE_DAYS) has no such risk
# and, once warm, resolves every card in a deck instantly with zero network
# calls. (An earlier version of this used Scryfall's own bulk data instead --
# smaller at ~75MB, but Scryfall only tracks TCGPlayer/Cardmarket prices, so
# it couldn't show Card Kingdom or ManaPool.)
#
# TCGPlayer and Card Kingdom links are MTGJSON's own official affiliate
# redirect links (purchaseUrls), guaranteed to resolve to the exact product.
# ManaPool isn't in that data, so its link is instead constructed from the
# (verified) https://manapool.com/card/<set>/<number>/<slug> pattern --
# best-effort, unlike the other two.
# --------------------------------------------------------------------------

MTGJSON_ALL_PRINTINGS_URL = "https://mtgjson.com/api/v5/AllPrintings.json.gz"
# 90 days of daily price history per printing (not just today's price) --
# used both for the current price and for the price-trend indicator (see
# _trend_price), since the trend needs a real reference point in the past,
# not just an artifact of how recently the index happened to last refresh.
MTGJSON_ALL_PRICES_URL = "https://mtgjson.com/api/v5/AllPrices.json.gz"
PRICE_TREND_LOOKBACK_DAYS = 7

# Budget-alternative suggestions (see _compute_budget_alternatives) use
# Scryfall's Oracle Tags -- community-curated functional tags like "mana
# rock" or "ramp", NOT anything LLM-generated. A tag's own taggings count
# is used as a rough specificity filter: too rare isn't useful (barely any
# alternatives to suggest), too common (e.g. "activated ability", "spot
# removal") is too broad a bucket to mean much.
BUDGET_ALT_MIN_TAG_COUNT = 5
BUDGET_ALT_MAX_TAG_COUNT = 1200
BUDGET_ALT_MAX_RESULTS = 3
# Missing cards cheaper than this don't get alternative suggestions shown
# -- not worth the UI noise for a $8 card.
BUDGET_ALT_MIN_PRICE = 20.0
# Tags that describe a rules mechanic, a flavor/community quirk, or print
# metadata rather than a deck-building *role* -- e.g. Mana Crypt is tagged
# both "mana rock" (its actual function) and "coin flip" (an incidental
# drawback clause with fewer total taggings, which the raw specificity
# filter alone would have wrongly preferred). Found by testing real chase
# cards (Mana Crypt, Swords to Plowshares, Demonic Tutor, Gaea's Cradle,
# Wasteland, The Tabernacle at Pendrell Vale) and checking what tag got
# picked -- not guessed. Tags with "cycle" anywhere in the label (print-
# cycle metadata, e.g. "cycle-usg-legendary-land", "supercycle-legendary-land")
# are excluded by substring match, not listed here.
BUDGET_ALT_EXCLUDED_TAGS = {
    "coin flip", "drawback", "doesn't untap", "burn-you",
    "activated ability", "triggered ability",
    "single target instant/sorcery", "meme", "bible reference", "namesake spell",
    "full refund", "creature count matters",
}
# Whole administrative branches of Scryfall's tag hierarchy to exclude (see
# _compute_budget_alt_groups, which walks every tag's full ancestor set --
# a tag can have more than one parent -- to check membership). Found after
# Mana Drain (a counterspell) got matched with Dark Ritual via its
# "interrupt" tag ("This spell was an interrupt before Sixth Edition Rules
# introduced the stack" -- a pre-6E rules-template classification, not a
# functional role); a deliberate sweep of the full tag set for similar
# "deprecated"/"obsolete" language turned up a sibling branch
# ("deprecated mechanics", 17 tags like "old lifelink"/"deprecated p/t
# counter") and a third ("type errata", creature-type correction metadata
# like "type errata hound") that would have had the exact same problem.
BUDGET_ALT_EXCLUDED_BRANCHES = {"deprecated card types", "deprecated mechanics", "type errata"}
# A short, evidence-checked list of unambiguous "this is the card's actual
# deck-building role" tags -- checked before the generic has-parent/fewest-
# taggings heuristic below, since that heuristic alone still isn't reliable
# enough: Mana Drain also carries "ritual" (67 taggings, has a parent) for
# its delayed bonus-mana clause, which beats its real "counterspell" tag
# (97 taggings) on count alone even after excluding "interrupt". Every
# label here was confirmed to actually exist in Scryfall's tag set before
# adding it, not guessed.
BUDGET_ALT_PREFERRED_TAGS = {
    "counterspell", "counterspell-soft", "protection", "recursion", "mana rock",
    "ramp", "removal-exile", "spot removal", "tutor-card", "tutor-to-hand",
    "discard", "fog", "extra turn", "sweeper", "pure draw", "draw",
}

_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
PRICE_INDEX_PATH = os.path.join(_CORE_DIR, "data", "price_index.json")
STORE_PREFS_PATH = os.path.join(_CORE_DIR, "data", "store_prefs.json")
PRICE_INDEX_MAX_AGE_DAYS = 7
# Bumped whenever the on-disk shape of the index changes (e.g. the Scryfall ->
# MTGJSON switch, which changed "nonfoil"/"foil" from a single [price, url]
# to a list of [store, price, url]; or the AllPricesToday -> AllPrices switch,
# which added "nonfoil_trend"/"foil_trend") so a stale on-disk file from an
# older version of this code is treated as needing a rebuild rather than
# silently missing data or crashing. Also bumped for budget-alt tag-
# selection logic fixes with no shape change (v11: BUDGET_ALT_PREFERRED_TAGS;
# v12: BUDGET_ALT_EXCLUDED_BRANCHES), since this is the only lever available
# to force an immediate rebuild rather than waiting up to
# PRICE_INDEX_MAX_AGE_DAYS for stale picks to self-heal.
PRICE_INDEX_FORMAT_VERSION = 13


# --------------------------------------------------------------------------
# Self-update -- a plain `git pull`, run via subprocess (never a shell
# string or an OS-specific script) so it behaves identically on macOS,
# Windows, and Linux as long as git is on PATH. Requires the repo to have
# been `git clone`d (not downloaded as a ZIP) and be public so a plain
# `git pull` needs no credentials -- see README.
# --------------------------------------------------------------------------

def update_from_git(path: str = _CORE_DIR) -> dict:
    """Pulls the latest code from the repo `path` is inside (defaults to
    this module's own directory). Returns {"ok", "updated", "message"}:

    - ok=False: git isn't installed, this isn't a git checkout, or the pull
      itself failed (e.g. local edits conflict with the update) -- "message"
      explains why, safe to show directly to the user.
    - ok=True, updated=False: already on the latest version.
    - ok=True, updated=True: pulled new commits -- the running process is
      still the old code (Python doesn't hot-reload already-imported
      modules), so the app needs restarting to actually use them.
    """
    if not os.path.isdir(os.path.join(path, ".git")):
        return {
            "ok": False, "updated": False,
            "message": "This doesn't look like a git checkout -- if you downloaded a ZIP instead of "
                       "running 'git clone', re-install with git to enable updates.",
        }
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=path, capture_output=True, text=True, errors="replace", timeout=30,
        )
    except FileNotFoundError:
        return {"ok": False, "updated": False, "message": "git isn't installed (or isn't on your PATH) -- install it to enable updates."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "updated": False, "message": "Timed out reaching GitHub -- check your connection and try again."}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git pull failed").strip()
        return {"ok": False, "updated": False, "message": detail}

    output = (result.stdout or "").strip()
    updated = "up to date" not in output.lower()
    message = "Updated! Restart Brewlist to use the new version." if updated else "Already up to date."
    return {"ok": True, "updated": updated, "message": message}


def load_store_prefs(path: str = STORE_PREFS_PATH) -> list[str]:
    """Which stores (see STORE_LABELS) to show pricing from, saved from the
    web app's initial screen. Defaults to every store if nothing's been
    saved yet, the file is unreadable, or the saved list is empty/invalid --
    this is a preference, not something that should ever silently hide all
    pricing."""
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f).get("stores")
        selected = [s for s in PICKABLE_STORE_LABELS if s in (saved or [])]
        if selected:
            return selected
    except (OSError, ValueError):
        pass
    return list(STORE_LABELS)


def save_store_prefs(stores: list[str], path: str = STORE_PREFS_PATH) -> None:
    """Persists which stores to show pricing from -- see load_store_prefs."""
    selected = [s for s in PICKABLE_STORE_LABELS if s in stores]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"stores": selected}, f)


def price_index_built_at(path: str = PRICE_INDEX_PATH) -> datetime.datetime | None:
    """The local price index's build timestamp (UTC), or None if it doesn't
    exist or can't be read."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            built_at = json.load(f).get("built_at")
        return datetime.datetime.fromisoformat(built_at)
    except (OSError, ValueError, TypeError):
        return None


def price_index_age_days(path: str = PRICE_INDEX_PATH) -> float | None:
    """Age of the local price index in days, or None if it doesn't exist or
    can't be read (treated the same as "needs a rebuild" by callers)."""
    built = price_index_built_at(path)
    if built is None:
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - built).total_seconds() / 86400


def _content_length(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "brewlist/1.0 (personal use)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return 0


def _download_to_file(url: str, tmp_path: str, on_progress, offset: int, total_bytes: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "brewlist/1.0 (personal use)"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(tmp_path, "wb") as out:
        downloaded = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if on_progress:
                on_progress(offset + downloaded, total_bytes)


def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[’']", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _trend_price(date_prices: dict, lookback_days: int = PRICE_TREND_LOOKBACK_DAYS) -> tuple[float | None, float | None]:
    """Given a {date_str: price} dict (a single store/finish's history),
    returns (current_price, reference_price) where reference_price is the
    price from the closest available date at least `lookback_days` before
    the most recent one -- None if there's no date that far back yet (e.g.
    a card printed within the lookback window)."""
    if not date_prices:
        return None, None
    dates = sorted(date_prices)
    current = date_prices[dates[-1]]
    target = datetime.date.fromisoformat(dates[-1]) - datetime.timedelta(days=lookback_days)
    reference = None
    for d in dates:
        if datetime.date.fromisoformat(d) <= target:
            reference = date_prices[d]
        else:
            break
    return current, reference


def _latest_price(date_prices: dict) -> float | None:
    """Given a {date_str: price} dict, returns just the most recent price."""
    if not date_prices:
        return None
    return date_prices[max(date_prices)]


def _compute_budget_alt_groups(
    oracle_id_by_name: dict[str, str], display_name_by_name: dict[str, str],
    is_land_by_name: dict[str, bool], scryfall_id_by_name: dict[str, str], tags_gz_path: str,
) -> dict[str, dict]:
    """Precomputes "which cards share a functional role" groups, using
    Scryfall's Oracle Tags bulk file (community-curated functional tags
    like "mana rock" or "ramp" -- see BUDGET_ALT_* above). Grouping only --
    no pricing or ownership here, since which alternatives are actually
    cheap or already-owned depends on a specific price index snapshot and a
    specific user's collection, both only available later in
    build_comparison. This is just "what else does the same thing."

    For each card, picks one tag to represent its role: among its tags that
    aren't excluded (BUDGET_ALT_EXCLUDED_TAGS / "cycle" anywhere in the label) and whose
    total taggings count falls in [MIN, MAX]_TAG_COUNT, tags that have a
    parent in Scryfall's tag hierarchy (e.g. "mana rock" under "ramp") are
    preferred over root-level tags, ties broken by fewest total taggings
    (more specific). This matters: raw specificity alone picks wrong --
    Mana Crypt's "coin flip" tag (an incidental drawback clause) has fewer
    total taggings than its actual "mana rock" tag, so the parent-first
    preference is what keeps the pick meaningful.

    Returns {} if the tags file can't be read -- this is a nice-to-have,
    never worth failing the whole index build over.

    Returns {"tag_by_name": {name: tag_id}, "tag_labels": {tag_id: label},
    "groups": {tag_id: [[name, display_name, is_land, scryfall_id], ...]}}
    -- "groups" only includes tag_ids that at least one card actually
    picked (via tag_by_name), not every tag in the file. is_land lets
    build_comparison restrict a land's suggested alternatives to other
    lands -- a creature or spell sharing an oracle tag with a utility land
    isn't a drop-in
    replacement for it in the mana base. scryfall_id is just for a
    hover-preview image (see scryfall_image_url) -- any printing works, so
    it's not tied to a specific one.
    """
    try:
        name_by_oracle_id: dict[str, str] = {}
        for nm, oid in oracle_id_by_name.items():
            name_by_oracle_id.setdefault(oid, nm)

        # Two passes: first collect every oracle tag's raw metadata (need the
        # full set to walk parent_ids, including tags that turn out
        # excluded), then build the filtered structures actually used below.
        all_tags_by_id: dict[str, dict] = {}
        with gzip.open(tags_gz_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                tag = json.loads(line)
                if tag.get("type") == "oracle":
                    all_tags_by_id[tag["id"]] = tag

        # Whole administrative branches to exclude, not just individual
        # labels -- e.g. "deprecated mechanics" alone has 17 children like
        # "old lifelink"/"deprecated p/t counter" that describe how old
        # card templating worked, not what a card does today. Found by
        # deliberately searching the full tag set for "deprecated"/
        # "obsolete" after the "interrupt" bug (see BUDGET_ALT_EXCLUDED_TAGS)
        # turned out to be one card type branch among several similar ones.
        # A tag can have more than one parent_id, so this walks the full
        # ancestor set, not just a single chain.
        branch_root_ids = {
            tid for tid, t in all_tags_by_id.items()
            if (t.get("label") or "") in BUDGET_ALT_EXCLUDED_BRANCHES
        }
        branch_memo: dict[str, bool] = {}

        def _under_excluded_branch(tag_id: str) -> bool:
            if tag_id in branch_memo:
                return branch_memo[tag_id]
            branch_memo[tag_id] = False  # cycle guard while this id is in progress
            tag = all_tags_by_id.get(tag_id)
            result = False
            if tag:
                for pid in (tag.get("parent_ids") or []):
                    if pid in branch_root_ids or _under_excluded_branch(pid):
                        result = True
                        break
            branch_memo[tag_id] = result
            return result

        taggings_by_tag: dict[str, list[str]] = {}  # tag_id -> [oracle_id, ...]
        tag_labels: dict[str, str] = {}
        tag_has_parent: dict[str, bool] = {}
        for tag_id, tag in all_tags_by_id.items():
            label = tag.get("label") or tag_id
            if label in BUDGET_ALT_EXCLUDED_TAGS or "cycle" in label or _under_excluded_branch(tag_id):
                continue
            oids = [t["oracle_id"] for t in (tag.get("taggings") or []) if t.get("oracle_id")]
            if oids:
                taggings_by_tag[tag_id] = oids
                tag_labels[tag_id] = label
                tag_has_parent[tag_id] = bool(tag.get("parent_ids"))

        tags_by_oracle_id: dict[str, list[tuple[str, int]]] = {}
        for tag_id, oids in taggings_by_tag.items():
            count = len(oids)
            for oid in oids:
                tags_by_oracle_id.setdefault(oid, []).append((tag_id, count))

        tag_by_name: dict[str, str] = {}
        for nm, oid in oracle_id_by_name.items():
            candidate_tags = [
                (tag_id, count) for tag_id, count in tags_by_oracle_id.get(oid, [])
                if BUDGET_ALT_MIN_TAG_COUNT <= count <= BUDGET_ALT_MAX_TAG_COUNT
            ]
            if not candidate_tags:
                continue
            # Prefer a known-unambiguous role tag (BUDGET_ALT_PREFERRED_TAGS)
            # first; then a tag with a parent (a specific sub-category, e.g.
            # "mana rock") over a root-level tag; within each tier, prefer
            # the fewest total taggings (more specific/useful).
            tag_id, _count = min(
                candidate_tags,
                key=lambda t: (tag_labels[t[0]] not in BUDGET_ALT_PREFERRED_TAGS, not tag_has_parent[t[0]], t[1]),
            )
            tag_by_name[nm] = tag_id

        needed_tag_ids = set(tag_by_name.values())
        groups: dict[str, list[list]] = {}
        for tag_id in needed_tag_ids:
            seen = set()
            members = []
            for oid in taggings_by_tag.get(tag_id, []):
                nm = name_by_oracle_id.get(oid)
                if nm and nm not in seen:
                    seen.add(nm)
                    members.append([
                        nm, display_name_by_name.get(nm, nm), is_land_by_name.get(nm, False),
                        scryfall_id_by_name.get(nm),
                    ])
            if members:
                groups[tag_id] = members

        return {"tag_by_name": tag_by_name, "tag_labels": tag_labels, "groups": groups}
    except (OSError, ValueError, KeyError, gzip.BadGzipFile):
        return {}


def rebuild_price_index(path: str = PRICE_INDEX_PATH, on_progress=None) -> dict[str, dict]:
    """Downloads MTGJSON's AllPrintings + AllPrices (90-day history) bulk
    data and reduces them to {normalized_name: {"nonfoil": [[store, price,
    url], ...] sorted cheapest first, "foil": [...], "nonfoil_trend": {store:
    pct_change, ...}, "foil_trend": {...}}}, writing the result to `path`.
    Double-faced/split cards are indexed under both their combined name and
    their front-face name, since decklists typically reference only the
    front face.

    The price-trend percentages compare each store's current price for
    whichever printing ends up cheapest against its own price from
    PRICE_TREND_LOOKBACK_DAYS ago -- omitted for a store/finish without that
    much history yet (e.g. a card printed within the lookback window).

    `on_progress(bytes_done, bytes_total)`, if given, reports combined
    download progress across both files; parsing afterward is fast enough
    not to need its own granular progress.

    Raises ValueError on any network failure (nothing is written in that
    case, so a prior index -- if any -- is left untouched)."""
    printings_size = _content_length(MTGJSON_ALL_PRINTINGS_URL)
    prices_size = _content_length(MTGJSON_ALL_PRICES_URL)
    total_bytes = printings_size + prices_size

    os.makedirs(os.path.dirname(path), exist_ok=True)
    printings_tmp = path + ".printings.download"
    prices_tmp = path + ".prices.download"
    try:
        _download_to_file(MTGJSON_ALL_PRINTINGS_URL, printings_tmp, on_progress, 0, total_bytes)
        _download_to_file(MTGJSON_ALL_PRICES_URL, prices_tmp, on_progress, printings_size, total_bytes)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        for p in (printings_tmp, prices_tmp):
            if os.path.exists(p):
                os.remove(p)
        raise ValueError(f"Could not download MTGJSON's price data: {e}")

    try:
        with gzip.open(prices_tmp, "rt", encoding="utf-8") as f:
            prices_by_uuid = (json.load(f) or {}).get("data") or {}
        with gzip.open(printings_tmp, "rt", encoding="utf-8") as f:
            all_printings = (json.load(f) or {}).get("data") or {}

        working: dict[str, dict] = {}
        # Cards on WotC's official Commander "Game Changers" list, by name --
        # tracked unconditionally for every paper card (not just ones with
        # price data), since bracket-level info shouldn't depend on whether
        # a printing happened to be priced.
        game_changers: set[str] = set()
        # Commander-legality per name (Legal/Banned/Restricted/etc, straight
        # from MTGJSON) -- legality is a name-level fact for essentially all
        # real cards, so whichever printing we see last for a name is fine;
        # also tracked unconditionally, same reasoning as Game Changers.
        commander_legality: dict[str, str] = {}
        # Exact-printing USD prices keyed by Scryfall ID (TCGPlayer-sourced,
        # same fields Scryfall's own /cards/<id> used to provide) -- this is
        # what lets owned-card pricing (matching your ManaBox collection's
        # recorded Scryfall ID to the exact printing you hold) be a local
        # lookup instead of a live per-card Scryfall API call.
        by_scryfall_id: dict[str, dict] = {}
        # Scryfall oracle_id per name -- MTGJSON already carries this in
        # identifiers.scryfallOracleId, so no extra download is needed just
        # to get it. Used to cross-reference against Scryfall's Oracle Tags
        # for budget-alternative suggestions (see _compute_budget_alt_groups).
        oracle_id_by_name: dict[str, str] = {}
        # Proper-cased display name per normalized name -- the index itself
        # is keyed by normalize_name() (lowercased), which is right for
        # lookups but wrong to show a user directly (see the same fix
        # elsewhere for e.name vs normalized keys).
        display_name_by_name: dict[str, str] = {}
        # Whether a name is a Land -- budget-alternative suggestions for a
        # land should only ever suggest other lands (a creature or spell
        # that shares an oracle tag with a utility land isn't actually a
        # drop-in replacement for it in the mana base). See
        # _compute_budget_alt_groups / the is_land filter in build_comparison.
        is_land_by_name: dict[str, bool] = {}
        # One representative printing's Scryfall ID per name -- just for a
        # hover-preview image on budget-alternative suggestions (see
        # scryfall_image_url), so it doesn't matter which printing; first
        # one seen wins, same as display_name_by_name.
        scryfall_id_by_name: dict[str, str] = {}
        for set_obj in all_printings.values():
            for card in set_obj.get("cards", []):
                if "paper" not in (card.get("availability") or []):
                    continue

                name = card.get("name") or ""
                names = {normalize_name(name)}
                face_name = card.get("faceName")
                if face_name and face_name != name:
                    names.add(normalize_name(face_name))
                names.discard("")
                if not names:
                    continue

                is_land = "Land" in (card.get("types") or [])
                card_scryfall_id = (card.get("identifiers") or {}).get("scryfallId")
                for nm in names:
                    display_name_by_name.setdefault(nm, face_name if (face_name and normalize_name(face_name) == nm) else name)
                    is_land_by_name.setdefault(nm, is_land)
                    if card_scryfall_id:
                        scryfall_id_by_name.setdefault(nm, card_scryfall_id)

                if card.get("isGameChanger"):
                    game_changers.update(names)

                legality = (card.get("legalities") or {}).get("commander")
                if legality:
                    for nm in names:
                        commander_legality[nm] = legality

                scryfall_oracle_id = (card.get("identifiers") or {}).get("scryfallOracleId")
                if scryfall_oracle_id:
                    for nm in names:
                        oracle_id_by_name.setdefault(nm, scryfall_oracle_id)

                uuid = card.get("uuid")
                price_entry = prices_by_uuid.get(uuid) if uuid else None
                if not price_entry:
                    continue
                paper = price_entry.get("paper") or {}
                purchase_urls = card.get("purchaseUrls") or {}

                scryfall_id = (card.get("identifiers") or {}).get("scryfallId")
                if scryfall_id:
                    tcg_retail = (paper.get("tcgplayer") or {}).get("retail") or {}
                    by_scryfall_id[scryfall_id] = {
                        "usd": _latest_price(tcg_retail.get("normal") or {}),
                        "usd_foil": _latest_price(tcg_retail.get("foil") or {}),
                        "usd_etched": _latest_price(tcg_retail.get("etched") or {}),
                    }

                set_code = (card.get("setCode") or "").lower()
                number = card.get("number") or ""
                manapool_url = (
                    f"https://manapool.com/card/{set_code}/{number}/{_slugify(name)}"
                    if set_code and number and name else None
                )

                stores = [
                    ("TCGP", "tcgplayer", purchase_urls.get("tcgplayer"), purchase_urls.get("tcgplayer")),
                    ("CK", "cardkingdom", purchase_urls.get("cardKingdom"), purchase_urls.get("cardKingdomFoil")),
                    ("MP", "manapool", manapool_url, manapool_url),
                ]
                for label, price_key, nonfoil_url, foil_url in stores:
                    retail = (paper.get(price_key) or {}).get("retail") or {}
                    nonfoil_price, nonfoil_ref = _trend_price(retail.get("normal") or {})
                    foil_price, foil_ref = _trend_price(retail.get("foil") or {})

                    for nm in names:
                        entry = working.setdefault(nm, {"nonfoil": {}, "foil": {}})
                        if nonfoil_price and nonfoil_url:
                            p = float(nonfoil_price)
                            cur = entry["nonfoil"].get(label)
                            if cur is None or p < cur[0]:
                                entry["nonfoil"][label] = [p, nonfoil_url, nonfoil_ref]
                        if foil_price and foil_url:
                            p = float(foil_price)
                            cur = entry["foil"].get(label)
                            if cur is None or p < cur[0]:
                                entry["foil"][label] = [p, foil_url, foil_ref]

                # Cardmarket -- EUR, kept separate from the stores loop above
                # (see EXTRA_STORE_LABELS): still "cheapest across every
                # printing of this name", just in its own currency, never
                # blended into cheapest_nonfoil/foil or any dollar total.
                cm_url = purchase_urls.get("cardmarket")
                if cm_url:
                    cm_retail = (paper.get("cardmarket") or {}).get("retail") or {}
                    cm_nonfoil_price, _ = _trend_price(cm_retail.get("normal") or {})
                    cm_foil_price, _ = _trend_price(cm_retail.get("foil") or {})
                    for nm in names:
                        entry = working.setdefault(nm, {"nonfoil": {}, "foil": {}})
                        if cm_nonfoil_price:
                            p = float(cm_nonfoil_price)
                            cur = entry.get("cardmarket_nonfoil")
                            if cur is None or p < cur[0]:
                                entry["cardmarket_nonfoil"] = (p, cm_url)
                        if cm_foil_price:
                            p = float(cm_foil_price)
                            cur = entry.get("cardmarket_foil")
                            if cur is None or p < cur[0]:
                                entry["cardmarket_foil"] = (p, cm_url)
    finally:
        for p in (printings_tmp, prices_tmp):
            if os.path.exists(p):
                os.remove(p)

    def _trends(current_by_store: dict) -> dict[str, float] | None:
        trend = {}
        for label, (p, _u, ref) in current_by_store.items():
            if ref:
                trend[label] = (p - ref) / ref * 100
        return trend or None

    index: dict[str, dict] = {}
    for nm, entry in working.items():
        nonfoil_list = sorted(([label, p, u] for label, (p, u, _ref) in entry["nonfoil"].items()), key=lambda t: t[1])
        foil_list = sorted(([label, p, u] for label, (p, u, _ref) in entry["foil"].items()), key=lambda t: t[1])
        index[nm] = {
            "nonfoil": nonfoil_list or None,
            "foil": foil_list or None,
            "nonfoil_trend": _trends(entry["nonfoil"]),
            "foil_trend": _trends(entry["foil"]),
            "cardmarket_nonfoil": list(entry["cardmarket_nonfoil"]) if entry.get("cardmarket_nonfoil") else None,
            "cardmarket_foil": list(entry["cardmarket_foil"]) if entry.get("cardmarket_foil") else None,
        }

    # Budget-alternative groupings -- a separate, best-effort download
    # (Scryfall's Oracle Tags, ~6MB) after the main MTGJSON pull above. Never
    # allowed to fail the whole index build: any problem here just means no
    # alternative suggestions this time, everything else is unaffected.
    budget_alt_groups: dict[str, dict] = {}
    tags_tmp = path + ".tags.download"
    tags_url = _scryfall_bulk_download_url("oracle_tags")
    if tags_url:
        try:
            _download_to_file(tags_url, tags_tmp, None, 0, 0)
            budget_alt_groups = _compute_budget_alt_groups(
                oracle_id_by_name, display_name_by_name, is_land_by_name, scryfall_id_by_name, tags_tmp
            )
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            pass
        finally:
            if os.path.exists(tags_tmp):
                os.remove(tags_tmp)

    payload = {
        "format_version": PRICE_INDEX_FORMAT_VERSION,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "card_count": len(index),
        "prices": index,
        "game_changers": sorted(game_changers),
        "commander_legality": commander_legality,
        "scryfall_prices": by_scryfall_id,
        "budget_alt_tag_by_name": budget_alt_groups.get("tag_by_name") or {},
        "budget_alt_tag_labels": budget_alt_groups.get("tag_labels") or {},
        "budget_alt_groups": budget_alt_groups.get("groups") or {},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return index


def scryfall_prices_in_index(path: str = PRICE_INDEX_PATH) -> dict[str, dict]:
    """Returns {scryfall_id: {"usd", "usd_foil", "usd_etched"}} for exact-
    printing pricing, read from the local price index (see
    rebuild_price_index/ensure_price_index -- call that first to make sure
    the index is actually present/fresh). Empty dict if the index doesn't
    exist or predates this field. Used for owned-card pricing (matching
    ManaBox's recorded Scryfall ID to the exact printing you hold) --
    fetch_scryfall_prices_by_id() falls back to a live lookup only for IDs
    missing here (e.g. a printing too new for the last index build)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("scryfall_prices") or {}
    except (OSError, ValueError):
        return {}


def budget_alt_data_in_index(path: str = PRICE_INDEX_PATH) -> dict:
    """Returns {"tag_by_name": {name: tag_id}, "tag_labels": {tag_id: label},
    "groups": {tag_id: [[name, display_name], ...]}} -- "which cards share a
    functional role" groupings, read from the local price index (see
    _compute_budget_alt_groups). build_comparison uses this plus a live
    price index / owned-collection lookup to decide, per missing card,
    which group members are already owned vs. cheaper to buy -- that split
    can't be precomputed at index-build time since it depends on a specific
    user's collection. All three keys default to {} if the index doesn't
    have this data yet (predates this field, or the Oracle Tags download
    failed the last time the index was built -- best-effort, see
    rebuild_price_index)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "tag_by_name": data.get("budget_alt_tag_by_name") or {},
            "tag_labels": data.get("budget_alt_tag_labels") or {},
            "groups": data.get("budget_alt_groups") or {},
        }
    except (OSError, ValueError):
        return {"tag_by_name": {}, "tag_labels": {}, "groups": {}}


def commander_legality_in_index(path: str = PRICE_INDEX_PATH) -> dict[str, str]:
    """Returns {normalized_name: legality} (e.g. "Legal", "Banned",
    "Restricted") for the Commander format, read from the local price index
    (see rebuild_price_index/ensure_price_index -- call that first to make
    sure the index is actually present/fresh). Empty dict if the index
    doesn't exist or predates this field."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("commander_legality") or {}
    except (OSError, ValueError):
        return {}


def game_changers_in_index(path: str = PRICE_INDEX_PATH) -> set[str]:
    """Returns the set of normalized card names on WotC's official Commander
    "Game Changers" list, read from the local price index (see
    rebuild_price_index/ensure_price_index -- call that first to make sure
    the index is actually present/fresh). Empty set if the index doesn't
    exist or predates this field."""
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f).get("game_changers") or [])
    except (OSError, ValueError):
        return set()


def ensure_price_index(path: str = PRICE_INDEX_PATH, on_progress=None, force_refresh: bool = False) -> dict[str, dict]:
    """Returns the local {name: {"nonfoil":..., "foil":...}} price index,
    rebuilding it (a real download) only if it's missing, older than
    PRICE_INDEX_MAX_AGE_DAYS, or force_refresh is set. A warm index returns
    instantly with no network calls."""
    if not force_refresh:
        age = price_index_age_days(path)
        if age is not None and age < PRICE_INDEX_MAX_AGE_DAYS:
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                if payload.get("format_version") != PRICE_INDEX_FORMAT_VERSION:
                    raise ValueError("stale index format")
                if on_progress:
                    on_progress(1, 1)
                return payload.get("prices") or {}
            except (OSError, ValueError):
                pass  # corrupt or outdated cache file -- fall through and rebuild
    return rebuild_price_index(path, on_progress=on_progress)


# --------------------------------------------------------------------------
# Commander Spellbook -- a live, single-request-per-deck lookup (not a bulk
# index like the price data above) against the public combo database at
# backend.commanderspellbook.com, for two Commander-specific enrichments:
#
# 1. Which known combos this deck already has all the pieces for, and which
#    it's exactly one card away from completing.
# 2. Per-card mass-land-denial/extra-turn flags, which MTGJSON's data
#    doesn't have (see PRICE_INDEX_FORMAT_VERSION's history) but WotC's
#    bracket criteria do care about.
#
# Note: the "bracketTag" this API returns (Ruthless/Spicy/Powerful/Oddball/
# Core/Exhibition/Banned) is Commander Spellbook's own community power/style
# rating, NOT the official WotC Bracket 1-5 system -- only "Core" and
# "Exhibition" happen to share a name with WotC's brackets. Surface it
# labeled as theirs, not as an official bracket number.
#
# Both lookups are best-effort: any failure (network, timeout, bad response)
# returns None rather than raising, since this is a small volunteer-run
# service and its unavailability shouldn't break the rest of the report.
# --------------------------------------------------------------------------

COMMANDER_SPELLBOOK_API = "https://backend.commanderspellbook.com"

BRACKET_TAG_LABELS = {
    "E": "Exhibition", "C": "Core", "P": "Powerful", "O": "Oddball",
    "S": "Spicy", "R": "Ruthless", "B": "Banned",
}


def _commander_spellbook_deck_payload(entries: list[CardEntry]) -> dict:
    main = [{"card": e.name, "quantity": e.quantity} for e in entries if e.section != "commander"]
    commanders = [{"card": e.name, "quantity": e.quantity} for e in entries if e.section == "commander"]
    return {"main": main, "commanders": commanders}


def _commander_spellbook_post(endpoint: str, payload: dict) -> dict | None:
    try:
        req = urllib.request.Request(
            f"{COMMANDER_SPELLBOOK_API}/{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "brewlist/1.0 (personal use)"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return None


def _simplify_combo(raw: dict, deck_names: set[str] | None = None) -> dict:
    uses = [u["card"]["name"] for u in raw.get("uses", [])]
    produces = [p["feature"]["name"] for p in raw.get("produces", [])]
    # raw["id"] here is this specific card-combination's *variant* ID (e.g.
    # "513-5034--46") -- what commanderspellbook.com/combo/<id>/ actually
    # expects. raw["of"][0]["id"] looks similar but is the abstract combo
    # *template* this variant realizes (a plain int, e.g. 26516); visiting
    # /combo/<that>/ redirects to the site's own canonical variant for that
    # template, which is very often a *different* card pairing than the one
    # this deck actually has -- confirmed by comparing the two live.
    combo_id = raw.get("id")
    result = {
        "uses": uses,
        "produces": produces,
        "url": f"https://commanderspellbook.com/combo/{combo_id}/" if combo_id else None,
        "popularity": raw.get("popularity") or 0,
    }
    if deck_names is not None:
        result["missing"] = [nm for nm in uses if normalize_name(nm) not in deck_names]
    return result


def find_deck_combos(entries: list[CardEntry], max_almost: int = 8) -> dict | None:
    """Queries Commander Spellbook for combos this deck already has all the
    pieces for ("included"), and the most notable combos it's exactly one
    card away from ("almost_included", ranked by popularity and capped at
    `max_almost` -- a real deck can be "one card away" from dozens of
    obscure combos via generic staples like Sol Ring, so this keeps the
    result focused on ones actually worth surfacing). Returns None on any
    failure (see module note above)."""
    payload = _commander_spellbook_deck_payload(entries)
    data = _commander_spellbook_post("find-my-combos", payload)
    if data is None:
        return None
    results = data.get("results") or {}
    deck_names = {normalize_name(e.name) for e in entries}
    included = [_simplify_combo(c) for c in results.get("included") or []]
    almost_raw = sorted(results.get("almostIncluded") or [], key=lambda c: c.get("popularity") or 0, reverse=True)
    almost_total = len(almost_raw)
    almost = [_simplify_combo(c, deck_names) for c in almost_raw[:max_almost]]
    return {"included": included, "almost_included": almost, "almost_total": almost_total}


def estimate_deck_bracket(entries: list[CardEntry]) -> dict | None:
    """Queries Commander Spellbook for its own power/style tag for this deck
    (see BRACKET_TAG_LABELS) plus per-card mass-land-denial/extra-turn flags
    (normalized_name -> {"massLandDenial": bool, "extraTurn": bool}).
    Returns None on any failure (see module note above)."""
    payload = _commander_spellbook_deck_payload(entries)
    data = _commander_spellbook_post("estimate-bracket", payload)
    if data is None or "bracketTag" not in data:
        return None
    cards = {}
    for c in data.get("cards") or []:
        name = (c.get("card") or {}).get("name")
        if name:
            cards[normalize_name(name)] = {
                "massLandDenial": bool(c.get("massLandDenial")),
                "extraTurn": bool(c.get("extraTurn")),
            }
    return {"tag": data["bracketTag"], "cards": cards, "combo_count": len(data.get("combos") or [])}


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
                 strict: bool = False, stores: set[str] | None = None) -> list[tuple[str, float, str]]:
    """Returns up to max_results (store, price, url) tuples, cheapest first.

    `foil` picks which finish to price -- defaults to the finish the decklist entry
    itself specifies (entry.is_foil). Foil and nonfoil prices come from separate
    fields and can differ a lot, so pass foil=True/False explicitly to price the
    other finish.

    Prefers the cheapest price found across *every* printing of the card at
    each of TCGPlayer/Card Kingdom/ManaPool (see ensure_price_index /
    entry.cheapest_nonfoil/foil) -- a true baseline, since whichever single
    printing the source decklist entry happens to reference can be a rare,
    dramatically more expensive alt-art. Falls back to the decklist's own
    referenced-printing price/link data if no baseline price was found for
    this finish (e.g. the lookup failed or the card couldn't be resolved).

    By default, if the requested finish has no listed price, we fall back to
    whatever finish *is* priced (better than showing nothing). Pass
    strict=True to disable that fallback -- used when a caller wants to know
    specifically whether that finish is priced (e.g. the HTML foil/nonfoil toggle).

    `stores`, if given, restricts results to those store labels (see
    STORE_LABELS) -- only matters for the raw decklist-price fallback below,
    since entry.cheapest_nonfoil/foil is already filtered by store preference
    when build_comparison sets it.
    """
    want_foil = entry.is_foil if foil is None else foil

    cheapest = entry.cheapest_foil if want_foil else entry.cheapest_nonfoil
    if not cheapest and not strict:
        cheapest = entry.cheapest_nonfoil if want_foil else entry.cheapest_foil
    if cheapest:
        return cheapest[:max_results]

    results = []
    for label, nonfoil_key, foil_key, nonfoil_url_key, foil_url_key in STORES:
        if stores is not None and label not in stores:
            continue
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
    is_commander_format: bool = False, stores: list[str] | None = None,
) -> tuple[list[str], dict[str, list[CardResult]], dict]:
    # Which stores (TCGP/CK/MP) to show pricing from -- see load_store_prefs.
    # Defaults to the saved preference (every store, until the user narrows
    # it from the web app's initial screen) when the caller doesn't pass one
    # explicitly.
    selected_stores = set(stores) if stores is not None else set(load_store_prefs())
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

    # Pass 2: price everything from the local MTGJSON-derived index (see
    # ensure_price_index) -- this covers owned-card exact-printing pricing
    # (matched by Scryfall ID), cheapest-across-printings pricing for every
    # card, and (for Commander decks) Game Changers/legality, all as local
    # lookups once the index is warm. The index only triggers a real
    # download when it's missing or older than a week (see
    # PRICE_INDEX_MAX_AGE_DAYS) -- that's the only case this pass is
    # genuinely slow; otherwise every card in a deck resolves instantly with
    # zero network calls.
    by_name = ensure_price_index(on_progress=on_progress)
    scryfall_prices = scryfall_prices_in_index()
    price_cache: dict[str, dict] = {}
    live_fallback_ids = set()
    for sid in needed_ids:
        hit = scryfall_prices.get(sid)
        if hit:
            price_cache[sid] = hit
        else:
            live_fallback_ids.add(sid)
    if live_fallback_ids:
        # Rare: a printing too new for the last index build (MTGJSON's data
        # is only as fresh as the last weekly refresh). A live per-ID lookup
        # for just these stragglers, not the common case.
        price_cache.update(fetch_scryfall_prices_by_id(live_fallback_ids))

    game_changers_count = 0
    game_changers_names: list[str] = []
    banned_count = 0
    # Game Changers/legality are Commander-bracket concepts -- meaningless
    # (and misleading) outside that format, so both are gated on it.
    game_changers = game_changers_in_index() if is_commander_format else set()
    commander_legality = commander_legality_in_index() if is_commander_format else {}
    for e in entries:
        hit = by_name.get(normalize_name(e.name)) or {}
        nonfoil_list = [tuple(t) for t in hit["nonfoil"]] if hit.get("nonfoil") else []
        foil_list = [tuple(t) for t in hit["foil"]] if hit.get("foil") else []
        e.cheapest_nonfoil = [t for t in nonfoil_list if t[0] in selected_stores] or None
        e.cheapest_foil = [t for t in foil_list if t[0] in selected_stores] or None
        e.price_trend_nonfoil = hit.get("nonfoil_trend")
        e.price_trend_foil = hit.get("foil_trend")
        if "CM" in selected_stores:
            e.cardmarket_nonfoil = tuple(hit["cardmarket_nonfoil"]) if hit.get("cardmarket_nonfoil") else None
            e.cardmarket_foil = tuple(hit["cardmarket_foil"]) if hit.get("cardmarket_foil") else None
        if is_commander_format:
            e.is_game_changer = normalize_name(e.name) in game_changers
            if e.is_game_changer:
                game_changers_count += 1
                game_changers_names.append(e.name)
            e.commander_legality = commander_legality.get(normalize_name(e.name))
            if e.commander_legality and e.commander_legality != "Legal":
                banned_count += 1

    # Commander Spellbook lookups -- a live request each (not a bulk index),
    # only for Commander decks (both the combos and the bracket tag are
    # Commander-specific concepts). Best-effort: either call can return None
    # (network hiccup, service down) without affecting anything else here.
    combos = None
    bracket_estimate = None
    if is_commander_format:
        if on_progress:
            on_progress(0, 0, "combos")
        combos = find_deck_combos(entries)
        bracket_estimate = estimate_deck_bracket(entries)
        if bracket_estimate:
            for e in entries:
                info = bracket_estimate["cards"].get(normalize_name(e.name))
                if info:
                    e.mass_land_denial = info["massLandDenial"]
                    e.extra_turn = info["extraTurn"]

    budget_alt = budget_alt_data_in_index()

    buckets: dict[str, list[CardResult]] = {}
    totals = {
        "owned": 0, "missing": 0,
        "cost_nonfoil": 0.0, "cost_foil": 0.0,
        "deck_value": 0.0, "owned_value": 0.0,
        "unpriced_count": 0,  # cards (owned or missing) we couldn't find any price for
        "game_changers": game_changers_count,  # only meaningful when is_commander_format was set
        "game_changers_names": sorted(game_changers_names),
        "banned_count": banned_count,  # only meaningful when is_commander_format was set
        "combos": combos,  # {"included": [...], "almost_included": [...], "almost_total": N} or None
        "bracket_tag": bracket_estimate["tag"] if bracket_estimate else None,
    }

    for e, have, shortfall, owned_used, picks, remainder, reserved_qty in per_entry:
        if ignore_basics and "Basic" in e.type_line and "Land" in e.type_line:
            bucket = "Basic Lands"
        else:
            bucket = categorize(e.type_line)

        prices = best_prices(e, stores=selected_stores) if shortfall else []

        if shortfall and prices and prices[0][1] >= BUDGET_ALT_MIN_PRICE:
            self_price = prices[0][1]
            self_norm = normalize_name(e.name)
            self_is_land = "Land" in e.type_line
            tag_id = budget_alt["tag_by_name"].get(self_norm)
            group = budget_alt["groups"].get(tag_id) if tag_id else None
            if group:
                owned_alts: list[tuple[str, str | None]] = []
                cheap_candidates: list[tuple[str, float, str | None]] = []
                for member_norm, member_display, member_is_land, member_scryfall_id in group:
                    if member_norm == self_norm or member_is_land != self_is_land:
                        continue
                    member_owned = owned_collection.get(member_norm)
                    if member_owned and member_owned.total > 0:
                        # Show *your* printing's art, not a generic one --
                        # same reasoning as _display_scryfall_id for owned
                        # card tiles.
                        owned_id = member_owned.printings[0].scryfall_id if member_owned.printings else member_scryfall_id
                        owned_alts.append((member_display, owned_id))
                        continue
                    member_hit = by_name.get(member_norm) or {}
                    member_prices = [p for _label, p, _url in (member_hit.get("nonfoil") or [])]
                    if member_prices:
                        cheapest = min(member_prices)
                        if cheapest < self_price:
                            cheap_candidates.append((member_display, cheapest, member_scryfall_id))
                if owned_alts or cheap_candidates:
                    e.cheaper_alt_tag = budget_alt["tag_labels"].get(tag_id)
                    if owned_alts:
                        e.owned_alternatives = owned_alts[:BUDGET_ALT_MAX_RESULTS]
                    if cheap_candidates:
                        cheap_candidates.sort(key=lambda c: c[1])
                        e.cheaper_alternatives = cheap_candidates[:BUDGET_ALT_MAX_RESULTS]

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
            fallback = best_prices(e, stores=selected_stores)
            if fallback:
                owned_value += fallback[0][1] * remainder
            else:
                totals["unpriced_count"] += 1

        totals["owned_value"] += owned_value
        totals["deck_value"] += owned_value

        if shortfall:
            totals["owned"] += owned_used
            totals["missing"] += shortfall
            nonfoil_prices = best_prices(e, foil=False, stores=selected_stores)
            foil_prices = best_prices(e, foil=True, stores=selected_stores)
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
        owned_is_foil = picks[0][0].foil if picks else None
        buckets.setdefault(bucket, []).append(
            CardResult(e, have, shortfall, prices, owned_scryfall_id, owned_is_foil, owned_value, reserved_qty)
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
<link rel="icon" type="image/svg+xml" href="https://svgs.scryfall.io/card-symbols/PW.svg">
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
.kofi-link {{ display: flex; align-items: center; border-radius: 8px; overflow: hidden; }}
.kofi-link img {{ display: block; height: 30px; width: auto; }}
.kofi-link:hover {{ opacity: 0.85; }}
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
.stat.game-changers b {{ color: var(--gold); }}
.has-tooltip {{ position: relative; cursor: help; }}
.has-tooltip .tooltip-popup {{
  display: none;
  position: absolute;
  bottom: 100%;
  left: 0;
  margin-bottom: 8px;
  background: var(--bg-elevated);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 0.8rem;
  font-weight: 400;
  color: var(--text);
  white-space: normal;
  width: max-content;
  max-width: 320px;
  box-shadow: var(--shadow);
  z-index: 20;
}}
.has-tooltip:hover .tooltip-popup {{ display: block; }}
.stat.banned b {{ color: var(--missing); }}
.stat-sub {{ color: var(--text-dim); font-size: 0.8rem; }}
.price-basis-note {{ color: var(--text-dim); font-size: 0.8rem; }}
.progress-labeled {{
  flex: 1 1 200px;
  min-width: 160px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}}
.progress-label {{ color: var(--text-dim); font-size: 0.8rem; }}
.progress {{
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
/* Same look as details.bucket, but deliberately NOT that class/selector --
   the search/filter JS below hides any details.bucket with zero visible
   .card children, which would silently hide this panel (it has
   .combo-item children instead, not .card). */
details.combos-panel {{
  margin-bottom: 14px;
  border: 1px solid var(--card-border);
  border-radius: 12px;
  background: var(--bg-elevated);
  overflow: hidden;
}}
details.combos-panel > summary {{
  cursor: pointer;
  padding: 12px 16px;
  font-weight: 600;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 10px;
}}
details.combos-panel > summary::-webkit-details-marker {{ display: none; }}
details.combos-panel > summary::before {{
  content: "▸";
  color: var(--text-dim);
  transition: transform 0.15s ease;
}}
details.combos-panel[open] > summary::before {{ transform: rotate(90deg); }}
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
.card.foil {{ overflow: hidden; --mx: 50%; --my: 50%; }}
.card.foil::before {{
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: radial-gradient(
    circle at var(--mx) var(--my),
    rgba(255, 255, 255, 0.22) 0%,
    rgba(120, 200, 255, 0.14) 25%,
    rgba(255, 80, 220, 0.12) 45%,
    transparent 70%
  );
  mix-blend-mode: soft-light;
  opacity: 0;
  transition: opacity 0.7s ease;
}}
.card.foil:hover::before {{ opacity: 1; }}
.card.foil > * {{ position: relative; z-index: 1; }}
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
  text-decoration: none;
}}
a.badge:hover {{ text-decoration: underline; }}
.badge.game-changer {{
  background: color-mix(in srgb, var(--gold) 22%, var(--card-bg));
  color: var(--gold);
  text-transform: none;
  letter-spacing: normal;
}}
.badge.banned {{
  background: color-mix(in srgb, var(--missing) 22%, var(--card-bg));
  color: var(--missing);
  text-transform: none;
  letter-spacing: normal;
}}
.combos-note {{ color: var(--text-dim); font-size: 0.85rem; margin: 4px 0 14px; }}
.combo-list {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 10px;
  padding: 4px 16px 16px;
}}
.combo-item {{
  border-radius: 10px;
  border-left: 3px solid var(--gold);
  background: var(--card-bg);
  padding: 10px 14px;
  box-shadow: var(--shadow);
}}
.combo-item.almost {{ border-left-color: var(--accent); }}
.combo-title {{ font-weight: 600; font-size: 0.95rem; margin-bottom: 4px; }}
.combo-produces {{ color: var(--text-dim); font-size: 0.85rem; margin-bottom: 4px; }}
.combo-missing {{ color: var(--missing); font-size: 0.85rem; margin-bottom: 6px; }}
.combo-link {{ color: var(--accent); font-size: 0.8rem; text-decoration: none; }}
.combo-link:hover {{ text-decoration: underline; }}
.combo-toggle {{
  display: block;
  padding: 10px 16px;
  font-size: 0.8rem;
  color: var(--text-dim);
  cursor: pointer;
  user-select: none;
  border-top: 1px solid var(--card-border);
}}
.combo-toggle input {{ margin-right: 6px; cursor: pointer; }}
#almost-combos-list {{ display: none; }}
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
.price-pill.cardmarket {{
  border-style: dashed;
  color: var(--text-dim);
}}
.trend {{
  font-size: 0.68rem;
  font-weight: 600;
  white-space: nowrap;
}}
.trend-up {{ color: var(--missing); }}
.trend-down {{ color: var(--owned); }}
.no-price {{ color: var(--text-dim); font-size: 0.75rem; font-style: italic; text-align: right; }}
.finish-note {{ color: var(--text-dim); font-size: 0.7rem; font-style: italic; margin-bottom: 3px; text-align: right; }}
.prices-foil {{ display: none; }}
body.show-foil-prices .prices-foil {{ display: block; }}
body.show-foil-prices .prices-nonfoil {{ display: none; }}
.budget-alt-note {{
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--card-border);
  font-size: 0.75rem;
  color: var(--text-dim);
  display: flex;
  flex-direction: column;
  gap: 4px;
}}
.budget-alt-note a {{ color: var(--accent); text-decoration: none; }}
.budget-alt-note a:hover {{ text-decoration: underline; }}
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
    {game_changers_html}
    {legality_html}
    {bracket_tag_html}
    <div class="progress-labeled" title="Share of this deck you already own">
      <span class="progress-label">Deck completion <b id="progress-pct">{pct:.0f}%</b></span>
      <div class="progress"><div id="progress-bar" style="width:{pct:.1f}%"></div></div>
    </div>
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
    {cheapest_pricing_html}
  </div>
</header>
<main>
{combos_html}
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
  document.getElementById('progress-pct').textContent = pct.toFixed(0) + '%';
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
document.querySelectorAll('.card-thumb[data-full], .alt-link[data-full]').forEach(img => {{
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

document.querySelectorAll('.card.foil').forEach(card => {{
  card.addEventListener('mousemove', (e) => {{
    const rect = card.getBoundingClientRect();
    card.style.setProperty('--mx', ((e.clientX - rect.left) / rect.width * 100) + '%');
    card.style.setProperty('--my', ((e.clientY - rect.top) / rect.height * 100) + '%');
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

const showAlmostCombos = document.getElementById('show-almost-combos');
const almostCombosList = document.getElementById('almost-combos-list');
if (showAlmostCombos && almostCombosList) {{
  showAlmostCombos.addEventListener('change', () => {{
    almostCombosList.style.display = showAlmostCombos.checked ? 'grid' : 'none';
  }});
}}

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
                 overrides_endpoint: str | None = None, is_commander_format: bool = False) -> str:
    """Renders the full standalone HTML report. Every price/enrichment here
    reflects build_comparison's single always-accurate pass (see its
    docstring) -- there's no separate "fast vs. accurate" mode to reconcile.

    `overrides_endpoint`, if given (e.g. "/api/overrides/abc123" for the Flask
    app), makes the "Save Overrides" button POST there via fetch() instead of
    the CLI's default behavior of downloading a `{deck_id}_overrides.json`
    file for you to drop into your ManaBox export folder.

    `is_commander_format` decides whether Commander-specific enrichment
    (Game Changers, legality, combos, bracket rating) is shown at all --
    CardResult.entry.commander_legality/is_game_changer/etc. are only
    meaningful when it's true (see build_comparison's own param of the same
    name).
    """
    total_cards = totals["owned"] + totals["missing"]
    pct = (totals["owned"] / total_cards * 100) if total_cards else 100.0
    max_card_price = 0.0

    game_changers_html = ""
    if is_commander_format:
        gc_names = totals.get("game_changers_names") or []
        tooltip_text = (
            "Game Changers in this deck: " + ", ".join(gc_names)
            if gc_names else "None found in this deck"
        )
        game_changers_html = (
            '<div class="stat game-changers has-tooltip" '
            'title="On WotC\'s official Commander Game Changers list">'
            f'&#9889; Game Changers <b>{len(gc_names)}</b>'
            f'<div class="tooltip-popup">{html.escape(tooltip_text)}</div></div>'
        )

    legality_html = ""
    if is_commander_format:
        banned_count = totals.get("banned_count", 0)
        if banned_count:
            legality_html = (
                '<div class="stat banned" title="Cards not legal in Commander (banned/restricted)">'
                f'&#9940; Not Legal in Commander <b>{banned_count}</b></div>'
            )
        else:
            legality_html = '<div class="stat-sub">&#10003; No Commander-banned cards found</div>'

    bracket_tag_html = ""
    bracket_tag = totals.get("bracket_tag")
    if is_commander_format and bracket_tag:
        label = BRACKET_TAG_LABELS.get(bracket_tag, bracket_tag)
        # Same URL shape as commanderspellbook.com/find-my-combos/ itself
        # uses for its own "paste your decklist url" field (confirmed live,
        # not guessed) -- lets a user see Spellbook's own fuller breakdown
        # (an estimated WotC bracket *number*, plus why) for this exact deck.
        spellbook_url = "https://commanderspellbook.com/find-my-combos/?deckUrl=" + urllib.parse.quote(deck_url, safe="")
        bracket_tag_html = (
            '<div class="stat-sub" title="Commander Spellbook\'s own power/style rating for this deck -- '
            'not the official WotC Bracket 1-5 system, click to see their full breakdown">'
            'Commander Spellbook rating: '
            f'<a href="{html.escape(spellbook_url)}" target="_blank" rel="noopener noreferrer">'
            f'<b>{html.escape(label)}</b></a></div>'
        )

    combos_html = ""
    if is_commander_format:
        combos_data = totals.get("combos")
        if combos_data is None:
            combos_html = (
                '<div class="combos-note">Combo lookup unavailable right now (Commander Spellbook may be '
                'down) -- everything else in this report is unaffected.</div>'
            )
        else:
            included = combos_data.get("included") or []
            almost = combos_data.get("almost_included") or []
            almost_total = combos_data.get("almost_total") or 0

            def _combo_item(c, show_missing=False):
                title = " + ".join(html.escape(n) for n in c["uses"])
                produces = ", ".join(html.escape(p) for p in c["produces"]) or "an effect"
                link_html = (
                    f'<a href="{html.escape(c["url"])}" target="_blank" rel="noopener noreferrer" '
                    f'class="combo-link">View on Commander Spellbook &rarr;</a>' if c.get("url") else ""
                )
                missing_html = ""
                if show_missing and c.get("missing"):
                    names = ", ".join(html.escape(n) for n in c["missing"])
                    missing_html = f'<div class="combo-missing">Missing: <b>{names}</b></div>'
                cls = "combo-item almost" if show_missing else "combo-item"
                return (
                    f'<div class="{cls}"><div class="combo-title">{title}</div>'
                    f'<div class="combo-produces">&rarr; {produces}</div>{missing_html}{link_html}</div>'
                )

            if included or almost:
                included_html = (
                    "".join(_combo_item(c) for c in included) if included
                    else '<div class="combos-note">No combos fully in this deck yet.</div>'
                )
                toggle_html = ""
                almost_section_html = ""
                if almost:
                    almost_label = (
                        f'Show {almost_total} "almost there" combos (showing top {len(almost)} most popular)'
                        if almost_total > len(almost) else f'Show {almost_total} "almost there" combos'
                    )
                    toggle_html = (
                        '<label class="combo-toggle">'
                        '<input type="checkbox" id="show-almost-combos"> '
                        f'{html.escape(almost_label)}</label>'
                    )
                    almost_items_html = "".join(_combo_item(c, show_missing=True) for c in almost)
                    almost_section_html = f'<div class="combo-list" id="almost-combos-list">{almost_items_html}</div>'

                combos_html = (
                    '<details class="combos-panel">'
                    f'<summary>&#128279; Combos <span class="bucket-count">({len(included)} in deck)</span></summary>'
                    f'<div class="combo-list">{included_html}</div>'
                    f'{toggle_html}'
                    f'{almost_section_html}'
                    '</details>'
                )
            else:
                combos_html = '<div class="combos-note">&#10003; No known combos found in this deck (via Commander Spellbook)</div>'

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

    def _trend_span(label, trend):
        pct = (trend or {}).get(label)
        if pct is None or abs(pct) < 2:
            return ''
        arrow, cls = ('▲', 'trend-up') if pct > 0 else ('▼', 'trend-down')
        direction = 'up' if pct > 0 else 'down'
        return (
            f' <span class="trend {cls}" title="{label} price {direction} {abs(pct):.0f}% '
            f'over the last {PRICE_TREND_LOOKBACK_DAYS} days">{arrow}{abs(pct):.0f}%</span>'
        )

    def _pills(price_list, note=None, trend=None, cardmarket=None):
        if not price_list and not cardmarket:
            return '<div class="no-price">no price found</div>'
        note_html = f'<div class="finish-note">{html.escape(note)}</div>' if note else ''
        pills_html = "".join(
            f'<a class="price-pill{" best" if i == 0 else ""}" '
            f'href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(label)} ${price:.2f}{_trend_span(label, trend)}</a>'
            for i, (label, price, url) in enumerate(price_list)
        )
        if cardmarket:
            cm_price, cm_url = cardmarket
            pills_html += (
                f'<a class="price-pill cardmarket" href="{html.escape(cm_url)}" '
                'target="_blank" rel="noopener noreferrer" '
                'title="Cardmarket, in EUR -- informational only, not used for cheapest-price picks or deck totals">'
                f'CM &euro;{cm_price:.2f}</a>'
            )
        return note_html + '<div class="prices">' + pills_html + '</div>'

    bucket_blocks = []
    for bucket in bucket_names:
        cards = buckets[bucket]
        missing_count = sum(1 for r in cards if r.shortfall > 0)
        card_tiles = []
        for r in cards:
            e = r.entry
            name_esc = html.escape(e.name)
            # For an owned card, whether it's actually foil depends on which
            # printing you own (r.owned_is_foil), not the decklist's own foil
            # flag (e.entry.is_foil is just what finish the decklist entry
            # itself references, e.g. when shopping to fill a shortfall).
            is_foil = r.owned_is_foil if r.shortfall == 0 and r.owned_is_foil is not None else e.is_foil
            badges = ""
            if e.section == "commander":
                badges += '<span class="badge">Commander</span>'
            if is_foil:
                badges += '<span class="badge">Foil</span>'
            if e.is_game_changer:
                badges += (
                    '<a class="badge game-changer" target="_blank" rel="noopener noreferrer" '
                    'href="https://magic.wizards.com/en/news/announcements/introducing-commander-brackets-beta" '
                    'title="On WotC\'s official Commander Game Changers list -- click to read their reasoning">'
                    '&#9889; Game Changer</a>'
                )
            if e.commander_legality and e.commander_legality != "Legal":
                badges += (
                    f'<span class="badge banned" title="Commander legality: {html.escape(e.commander_legality)}">'
                    f'&#9940; {html.escape(e.commander_legality)}</span>'
                )
            if e.mass_land_denial:
                badges += '<span class="badge banned" title="Flagged as mass land denial (Commander Spellbook)">&#9940; Mass Land Denial</span>'
            if e.extra_turn:
                badges += '<span class="badge game-changer" title="Extra-turn effect (Commander Spellbook)">&#9203; Extra Turn</span>'

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
            nonfoil_trend = e.price_trend_foil if nonfoil_used_foil else e.price_trend_nonfoil
            foil_trend = e.price_trend_foil if foil_used_foil else e.price_trend_nonfoil
            prices_nonfoil_html = f'<div class="prices-nonfoil">{_pills(best_nonfoil, nonfoil_note, nonfoil_trend, e.cardmarket_nonfoil)}</div>'
            prices_foil_html = f'<div class="prices-foil">{_pills(best_foil, foil_note, foil_trend, e.cardmarket_foil)}</div>'

            budget_alt_html = ""
            if e.owned_alternatives or e.cheaper_alternatives:
                tag_label = html.escape(e.cheaper_alt_tag or "")
                title_attr = (
                    f'title="Other cards tagged &quot;{tag_label}&quot; on Scryfall '
                    '(community-curated function tags, not an AI guess)"'
                )

                def _scryfall_search_url(card_name: str) -> str:
                    return "https://scryfall.com/search?q=" + urllib.parse.quote(f'!"{card_name}"')

                def _alt_link(name: str, scryfall_id: str | None, label: str) -> str:
                    # Reuses the same hover-preview mechanism as the main
                    # card thumbnails (#hover-preview, see the shared JS
                    # listener below) -- data-full is just omitted when no
                    # scryfall_id is known, leaving a plain link.
                    full_url = scryfall_image_url(scryfall_id) if scryfall_id else None
                    full_attr = f' data-full="{html.escape(full_url)}"' if full_url else ""
                    return (
                        f'<a class="alt-link" href="{_scryfall_search_url(name)}"{full_attr} '
                        f'target="_blank" rel="noopener noreferrer">{label}</a>'
                    )

                owned_row_html = ""
                if e.owned_alternatives:
                    owned_links = " &middot; ".join(
                        _alt_link(name, scryfall_id, html.escape(name))
                        for name, scryfall_id in e.owned_alternatives
                    )
                    owned_row_html = (
                        f'<div {title_attr}>&#9989; You already own (tagged &quot;{tag_label}&quot;): {owned_links}</div>'
                    )

                cheap_row_html = ""
                if e.cheaper_alternatives:
                    cheap_links = " &middot; ".join(
                        _alt_link(name, scryfall_id, f'{html.escape(name)} (${price:.2f})')
                        for name, price, scryfall_id in e.cheaper_alternatives
                    )
                    cheap_row_html = (
                        f'<div {title_attr}>&#128161; Cheaper to buy (tagged &quot;{tag_label}&quot;): {cheap_links}</div>'
                    )

                budget_alt_html = f'<div class="budget-alt-note">{owned_row_html}{cheap_row_html}</div>'

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

            foil_class = " foil" if is_foil else ""

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
<div class="card owned{foil_class}" data-name="{name_esc.lower()}" data-qty="{e.quantity}" data-display-name="{name_esc}" data-owned-value="{r.owned_value:.2f}" data-reserved-qty="{reserved_flag}" {shop_data_attrs}>
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
<div class="card missing{foil_class}" data-name="{name_esc.lower()}" data-qty="{r.shortfall}" data-display-name="{name_esc}" {shop_data_attrs}>
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
  {budget_alt_html}
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

    kofi_html = (
        '<a class="kofi-link" href="https://ko-fi.com/imtotallymeh" target="_blank" '
        'rel="noopener noreferrer" title="Support Brewlist on Ko-fi">'
        '<img src="https://storage.ko-fi.com/cdn/kofi5.png?v=3" alt="Support me on Ko-fi" loading="lazy"></a>'
    )
    if overrides_endpoint:
        save_overrides_js = _post_overrides_js(overrides_endpoint)
        save_overrides_title = "Save which owned cards are reserved for other decks -- remembered automatically for this deck"
        header_actions_html = (
            '<div class="header-actions">'
            f'{kofi_html}'
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
        header_actions_html = f'<div class="header-actions">{kofi_html}</div>'

    cheapest_pricing_html = (
        '<span class="price-basis-note" title="Every price reflects the cheapest paper printing found for that card">'
        '&#10003; Accurate pricing</span>'
    )

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
        game_changers_html=game_changers_html,
        legality_html=legality_html,
        bracket_tag_html=bracket_tag_html,
        combos_html=combos_html,
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
        cheapest_pricing_html=cheapest_pricing_html,
    )
