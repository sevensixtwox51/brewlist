"""EDHREC commander-popularity lookups -- how often a given legendary
creature is actually played as a Commander, and its rank among the site's
own Top 100 list, in three time windows (2 years / month / week).

EDHREC has no official public API for this. Every function here parses the
same __NEXT_DATA__ JSON blob React embeds in the server-rendered HTML of
edhrec.com's own pages -- confirmed live, not guessed: both the /commanders
(Top 100 leaderboard) pages and a single card's own /commanders/<slug> page
carry the exact same "cardviews": [{"name", "slug", "rank", "num_decks"}, ...]
shape their own site's UI is built from.

Two real, verified constraints shape what this can honestly report:
- Only the Top 100 leaderboard pages carry a "rank" at all -- a single
  commander's own page (used as the fallback for anything outside the Top
  100) gives an exact num_decks but no rank, because EDHREC itself doesn't
  publish "you are #4,832 of every commander" anywhere. Most owned
  legendary creatures will fall in this bucket, not the Top 100 one --
  Archangel Avacyn (1,487 decks, confirmed live) doesn't even place in the
  2-year Top 100, whose #100 cutoff is in the tens of thousands.
- A commander's own page has no /month or /week variant (confirmed: a
  direct request 404s) -- only the Top 100 leaderboards are windowed.
  So a commander outside a given window's Top 100 has no popularity number
  for that specific window at all, only the (always-available) 2-year
  fallback count from its own page.

Everything here is best-effort and never raises on a network/parse failure
-- a stale or missing cache entry just means no badge shows for that card,
same "don't let a live third-party call block or break the app" discipline
find_deck_combos() (Commander Spellbook) already follows.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_MODULE_DIR, "data", "edhrec_cache.json")

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_REQUEST_TIMEOUT = 15

# window name -> (URL path suffix, cache key). "2years" is EDHREC's default
# /commanders page; the other two are literal sub-paths of it.
WINDOWS = {"2years": "", "month": "/month", "week": "/week"}

# Top-100 leaderboards move slowly enough (and cost so little to refetch --
# 3 requests total) that a daily refresh is simply always "fresh enough";
# per-commander single-card lookups are cached far longer (30 days) since
# there are potentially dozens of them per collection and their number
# barely moves week to week for anything not already trending.
_TOP_LIST_MAX_AGE = 24 * 3600
_COMMANDER_MAX_AGE = 30 * 24 * 3600

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


def _slugify(name: str) -> str:
    """EDHREC's own URL-slug rule, reverse-engineered from real slugs on
    the live site (not guessed): lowercase, apostrophes dropped outright
    (Y'shtola -> yshtola, not y-shtola), every other run of non-alphanumeric
    characters (spaces, commas, " // " on a double-faced card) collapsed to
    a single hyphen. Confirmed against dozens of real name/slug pairs from
    the live Top 100 list, including a DFC ("Frodo, Adventurous Hobbit //
    Sam, Loyal Attendant" -> "frodo-adventurous-hobbit-sam-loyal-attendant")
    and an existing in-name hyphen ("Cloud, Ex-SOLDIER" -> "cloud-ex-soldier")."""
    s = name.lower().replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _fetch_next_data(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE_PATH)


def _fetch_top_list(window: str) -> list[dict] | None:
    suffix = WINDOWS[window]
    data = _fetch_next_data(f"https://edhrec.com/commanders{suffix}")
    if not data:
        return None
    try:
        cardlists = data["props"]["pageProps"]["data"]["container"]["json_dict"]["cardlists"]
        cardviews = cardlists[0]["cardviews"]
    except (KeyError, IndexError, TypeError):
        return None
    return [
        {"name": c.get("name", ""), "slug": c.get("slug", ""), "rank": c.get("rank"), "num_decks": c.get("num_decks")}
        for c in cardviews
    ]


def ensure_top_lists(cache: dict | None = None) -> dict:
    """Refreshes any of the 3 Top 100 leaderboards older than
    _TOP_LIST_MAX_AGE (or missing). Returns the full cache dict -- pass one
    back in (from a prior call in the same request) to avoid re-reading the
    file, or omit to load it fresh."""
    if cache is None:
        cache = _load_cache()
    top = cache.setdefault("top", {})
    now = time.time()
    changed = False
    for window in WINDOWS:
        entry = top.get(window)
        if entry and now - entry.get("fetched_at", 0) < _TOP_LIST_MAX_AGE:
            continue
        cardviews = _fetch_top_list(window)
        if cardviews is None:
            continue  # best-effort -- keep whatever (possibly stale, possibly absent) entry there was
        top[window] = {"fetched_at": now, "cards": cardviews}
        changed = True
    if changed:
        _save_cache(cache)
    return cache


def _fetch_commander_num_decks(slug: str) -> int | None:
    # The real deck-count text ("EDH deck recommendations from 1,487
    # Commander decks...") lives only in the page's <meta> description tag,
    # NOT in the __NEXT_DATA__ JSON blob (confirmed live -- that JSON's own
    # "description" field is a different, generic one-liner with no
    # number at all) -- so this reads raw HTML instead of going through
    # _fetch_next_data.
    req = urllib.request.Request(
        f"https://edhrec.com/commanders/{slug}",
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    m = re.search(r'meta content="([^"]*Commander decks[^"]*)"', html)
    if not m:
        return None
    m = re.search(r"([\d,]+)\s+Commander decks", m.group(1))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def commander_popularity(name: str, cache: dict | None = None) -> dict | None:
    """Returns {"num_decks": int, "slug": str, "url": str, "rank": {window:
    int, ...}} for one commander -- `rank` only has entries for windows
    where this card actually places in that Top 100 (usually none, for
    anything short of a genuine staple; see module docstring). None if
    nothing could be found at all (never raises).

    Reads/writes the on-disk cache directly (own load/save) so a single
    call is always safe standalone; a caller doing many lookups in one
    request should load the cache once with _load_cache(), pass it via
    `cache`, and call _save_cache() itself afterward instead (see
    bulk_commander_popularity)."""
    own_cache = cache is None
    if cache is None:
        cache = _load_cache()
    cache = ensure_top_lists(cache)

    nm = name.strip().lower()
    ranks: dict[str, int] = {}
    matched_slug = None
    matched_num_decks = None
    for window, entry in cache.get("top", {}).items():
        for c in entry.get("cards", []):
            if c["name"].strip().lower() == nm:
                ranks[window] = c["rank"]
                matched_slug = matched_slug or c["slug"]
                if matched_num_decks is None:
                    matched_num_decks = c["num_decks"]

    commanders_cache = cache.setdefault("commanders", {})
    cached = commanders_cache.get(nm)
    now = time.time()
    # A cached entry is "fresh" (skip re-fetching) whether it found a real
    # count or genuinely found nothing (num_decks None) -- caching the
    # negative result too matters here: without it, a legendary creature
    # that just isn't a real/tracked commander (banned, never played, a
    # slug guess this module gets wrong) would get a live fetch attempt on
    # every single poll of a collection's worth of candidates forever.
    if cached and now - cached.get("fetched_at", 0) < _COMMANDER_MAX_AGE:
        num_decks = cached.get("num_decks")
        slug = cached.get("slug")
    else:
        # Transform DFCs (e.g. "Archangel Avacyn // Avacyn, the Purifier")
        # use only their front face's name in EDHREC's own URL -- confirmed
        # live ("archangel-avacyn", not the full joined name) -- while
        # other "//"-named cards (Adventure, split, a two-legend Modal DFC)
        # use the full joined name. Try the front-face-only slug first when
        # there's a "//" since that's the more common real Commander case
        # (most Commander-legal DFCs are the transform kind), falling back
        # to the full name if that 404s.
        candidates = [name.split(" // ")[0]] if " // " in name else [name]
        if " // " in name:
            candidates.append(name)
        num_decks = None
        slug = None
        for candidate in candidates:
            s = _slugify(candidate)
            fetched = _fetch_commander_num_decks(s)
            if fetched is not None:
                num_decks, slug = fetched, s
                break
        commanders_cache[nm] = {"num_decks": num_decks, "slug": slug, "fetched_at": now}
        if own_cache:
            _save_cache(cache)

    if matched_num_decks is not None and num_decks is None:
        num_decks = matched_num_decks
    if not slug:
        slug = matched_slug or (_slugify(name.split(" // ")[0]) if name else None)
    if num_decks is None and not ranks:
        return None
    return {
        "num_decks": num_decks,
        "slug": slug,
        "url": f"https://edhrec.com/commanders/{slug}" if slug else None,
        "rank": ranks,
    }


def bulk_commander_popularity(names: list[str], max_new_fetches: int = 25) -> dict[str, dict]:
    """Looks up popularity for many commanders in one call, sharing a single
    cache load/save instead of one file round-trip per name. `max_new_fetches`
    caps how many *uncached* commanders get a live network fetch in this one
    call (each is a real HTTP request to edhrec.com) -- names beyond that cap
    that aren't already cached are simply skipped this round rather than
    stalling the whole request; call again (e.g. the client re-polling while
    the "Commander" filter is open) to progressively fill in the rest."""
    cache = ensure_top_lists()
    results: dict[str, dict] = {}
    new_fetches = 0
    now = time.time()
    for name in names:
        nm = name.strip().lower()
        cached = cache.get("commanders", {}).get(nm)
        is_fresh = cached and now - cached.get("fetched_at", 0) < _COMMANDER_MAX_AGE
        in_top_list = any(
            any(c["name"].strip().lower() == nm for c in entry.get("cards", []))
            for entry in cache.get("top", {}).values()
        )
        if not is_fresh and not in_top_list:
            if new_fetches >= max_new_fetches:
                continue
            new_fetches += 1
        popularity = commander_popularity(name, cache=cache)
        if popularity is not None:
            results[name] = popularity
    _save_cache(cache)  # cheap even when nothing new was fetched -- simpler than tracking exactly what changed
    return results
