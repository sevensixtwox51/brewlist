"""EDHREC commander-popularity lookups -- how often a given legendary
creature is actually played as a Commander, and its rank among the site's
own full ranking, in three time windows (2 years / month / week).

EDHREC has no official public API for this. Every function here parses the
same JSON its own site's "Load More" button fetches -- confirmed live, not
guessed. The first ~100 entries come from the __NEXT_DATA__ blob React
embeds in the server-rendered HTML of e.g. edhrec.com/commanders/month;
that page's own cardlist carries a "more": "commanders/month-pastmonth-1
.json" pointer, fetchable at https://json.edhrec.com/pages/<that path> --
which itself carries the *next* page's "more" pointer, and so on until a
page comes back with "more": null. Following that chain all the way
(confirmed live: 45-68 pages depending on window, ~100 commanders per page,
tailing off around rank 5,000+ at 1 deck each) gives a real, complete rank
for essentially any commander that's been played at all in that window --
not just a Top 100, which is all the first version of this module used
(a real gap: a user found a card ranked #102 on EDHREC's own "Load More"
view that this module reported as unranked).

Fetching the full chain for all 3 windows is ~150-190 small JSON requests
-- a couple seconds of network time total (see the docstring on
_fetch_full_top_list for the measured per-page cost). Per direct user
request, this is NOT refreshed on a timer -- the cache is written once
(bootstrapped automatically the first time it's ever empty) and then only
updated again when the user explicitly asks for it (the UI's refresh
icon), specifically so this never silently re-hits edhrec.com just because
the app happened to run again, and to keep total request volume against a
site with no official API/rate-limit contract as low as it can reasonably
be. A manual refresh is itself throttled (_MIN_MANUAL_REFRESH_INTERVAL,
persisted across restarts) so mashing the button can't hammer it either.
Either way, nothing here blocks a page load waiting on a fetch --
ensure_top_lists_async/refresh_all always run the actual HTTP calls in a
background thread and return immediately; the client re-polls a few times
to pick up the finished result, same "never let a live third-party call
block or break the app" discipline find_deck_combos() (Commander
Spellbook) already follows.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_MODULE_DIR, "data", "edhrec_cache.json")

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_REQUEST_TIMEOUT = 15
_PAGE_DELAY = 0.1  # be a polite scraper -- no official API to rate-limit against
_MAX_PAGES = 150  # real depth is 45-68 pages (verified live) -- this is a safety cap, not the expected trip count

# window name -> EDHREC's own URL path suffix. "2years" is the site's
# default /commanders page; the other two are literal sub-paths of it.
WINDOWS = {"2years": "", "month": "/month", "week": "/week"}

# Deliberately NOT time-based auto-refresh -- per direct user request, this
# cache is refreshed only when the user clicks the refresh control (or on
# the very first-ever use, when there's nothing cached yet at all), never
# silently on a timer just because the app happened to run again. The
# minimum gap enforced between two manual refreshes, specifically to avoid
# hammering a site with no official API/rate-limit contract if someone
# mashes the refresh button -- persisted in the cache file itself (not just
# in memory) so it survives an app restart too.
_MIN_MANUAL_REFRESH_INTERVAL = 15 * 60

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)

_refresh_lock = threading.Lock()
_refreshing: set[str] = set()  # windows currently being refreshed by a background thread


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


def _fetch_json_page(path: str) -> dict | None:
    req = urllib.request.Request(f"https://json.edhrec.com/pages/{path}", headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
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


def _cardviews_to_entries(cardviews: list[dict]) -> list[dict]:
    return [
        {"name": c.get("name", ""), "slug": c.get("slug", ""), "rank": c.get("rank"), "num_decks": c.get("num_decks")}
        for c in cardviews
    ]


def _fetch_full_top_list(window: str) -> list[dict] | None:
    """Follows EDHREC's own "more" pagination chain from the main
    /commanders[/month|/week] page all the way to the end (a page with
    "more": null), collecting every page's cardviews. Measured live: each
    JSON page is ~30KB and takes well under 200ms including _PAGE_DELAY;
    a full chain (45-68 pages) finishes in a few seconds. Best-effort
    partial results -- if the first page fails, returns None; if a later
    page fails, returns whatever pages were already collected rather than
    discarding all of it."""
    suffix = WINDOWS[window]
    data = _fetch_next_data(f"https://edhrec.com/commanders{suffix}")
    if not data:
        return None
    try:
        cardlist = data["props"]["pageProps"]["data"]["container"]["json_dict"]["cardlists"][0]
    except (KeyError, IndexError, TypeError):
        return None
    entries = _cardviews_to_entries(cardlist.get("cardviews") or [])
    more = cardlist.get("more")
    pages = 1
    while more and pages < _MAX_PAGES:
        time.sleep(_PAGE_DELAY)
        page = _fetch_json_page(more)
        if page is None:
            break  # network hiccup mid-chain -- keep what we already have
        entries.extend(_cardviews_to_entries(page.get("cardviews") or []))
        more = page.get("more")
        pages += 1
    return entries


def _refresh_window_in_background(window: str) -> None:
    def run():
        try:
            entries = _fetch_full_top_list(window)
            if entries is None:
                return  # best-effort -- leave whatever (possibly stale, possibly absent) cache entry there was
            cache = _load_cache()
            cache.setdefault("top", {})[window] = {"fetched_at": time.time(), "cards": entries}
            _save_cache(cache)
        finally:
            with _refresh_lock:
                _refreshing.discard(window)
    threading.Thread(target=run, daemon=True).start()


def ensure_top_lists_async(cache: dict | None = None) -> dict:
    """Non-blocking: bootstraps any of the 3 windows that have NEVER been
    fetched at all (a cold start -- e.g. this is the very first time the
    Commander filter has ever been opened) in a background thread, then
    immediately returns the cache as it currently stands. Deliberately does
    NOT refresh a window just because it's old -- see the module docstring
    and _MIN_MANUAL_REFRESH_INTERVAL: once bootstrapped, a window only
    updates again when refresh_all() is explicitly called (the UI's refresh
    icon). Callers serving a live web request should always use this,
    never the blocking fetch directly, and re-poll shortly after to pick up
    the finished background result."""
    if cache is None:
        cache = _load_cache()
    with _refresh_lock:
        for window in WINDOWS:
            if window in cache.get("top", {}) or window in _refreshing:
                continue
            _refreshing.add(window)
            _refresh_window_in_background(window)
    return cache


def is_refreshing() -> bool:
    with _refresh_lock:
        return bool(_refreshing)


def top_list_status(cache: dict | None = None) -> dict:
    """{"refreshing": bool, "windows": {window: {"fetched_at": float|None,
    "count": int}}} -- backs the UI's "last updated" display and refresh
    button state."""
    if cache is None:
        cache = _load_cache()
    windows = {}
    for window in WINDOWS:
        entry = cache.get("top", {}).get(window)
        windows[window] = {
            "fetched_at": entry.get("fetched_at") if entry else None,
            "count": len(entry.get("cards", [])) if entry else 0,
        }
    return {"refreshing": is_refreshing(), "windows": windows}


def refresh_all() -> dict:
    """Manual refresh -- the UI's refresh icon calls this. Kicks off a
    background refresh of all 3 windows (skipping any already mid-refresh)
    regardless of how old they are, EXCEPT throttled by
    _MIN_MANUAL_REFRESH_INTERVAL since the last manual refresh, persisted
    in the cache file itself so the cooldown survives an app restart, not
    just an in-memory guard a restart would reset. Returns {"started":
    bool, "retry_after_seconds": int|None} -- retry_after_seconds is only
    set when throttled."""
    cache = _load_cache()
    now = time.time()
    last = cache.get("last_manual_refresh_at", 0)
    elapsed = now - last
    if elapsed < _MIN_MANUAL_REFRESH_INTERVAL:
        return {"started": False, "retry_after_seconds": round(_MIN_MANUAL_REFRESH_INTERVAL - elapsed)}
    cache["last_manual_refresh_at"] = now
    _save_cache(cache)
    with _refresh_lock:
        for window in WINDOWS:
            if window in _refreshing:
                continue
            _refreshing.add(window)
            _refresh_window_in_background(window)
    return {"started": True, "retry_after_seconds": None}


def _build_name_index(cache: dict) -> dict[str, dict[str, dict]]:
    """{window: {normalized_name: card}} -- built once per bulk lookup so
    looking up N commanders is O(N + total ranked entries) instead of
    O(N x total ranked entries) from a fresh linear scan per name (the
    full ranking chain is thousands of entries per window, not a Top 100
    anymore)."""
    index: dict[str, dict[str, dict]] = {}
    for window, entry in cache.get("top", {}).items():
        index[window] = {c["name"].strip().lower(): c for c in entry.get("cards", [])}
    return index


def commander_popularity(name: str, cache: dict | None = None, index: dict[str, dict[str, dict]] | None = None) -> dict | None:
    """Returns {"num_decks": int, "slug": str, "url": str, "rank": {window:
    int, ...}} for one commander -- `rank`/`num_decks` cover every window
    this card actually appears in (played at least once as a commander in
    that window; with the full ranking chain this is now most real
    commanders, not just a Top 100). None if the card doesn't appear in any
    cached window at all (never raises).

    `index`, if given (see _build_name_index), is used instead of scanning
    `cache` fresh -- a caller doing many lookups (bulk_commander_popularity)
    should always pass one; a single ad-hoc lookup can omit both and this
    builds a throwaway index from a freshly-loaded cache."""
    if index is None:
        if cache is None:
            cache = _load_cache()
        index = _build_name_index(cache)
    nm = name.strip().lower()
    ranks: dict[str, int] = {}
    slug = None
    num_decks = None
    for window, by_name in index.items():
        c = by_name.get(nm)
        if c:
            ranks[window] = c["rank"]
            slug = slug or c["slug"]
            if num_decks is None:
                num_decks = c["num_decks"]
    if not ranks:
        return None
    return {
        "num_decks": num_decks,
        "slug": slug,
        "url": f"https://edhrec.com/commanders/{slug}" if slug else None,
        "rank": ranks,
    }


def bulk_commander_popularity(names: list[str]) -> dict[str, dict]:
    """Looks up popularity for many commanders against the cached full
    rankings -- a pure in-memory operation once the daily cache is warm, no
    per-name network calls at all (unlike the old Top-100-only version,
    the full ranking chain means there's no per-card fallback fetch left to
    do: a card missing from every window's full list genuinely hasn't been
    played as a commander at all in any of them). Also kicks off a
    background refresh of anything stale via ensure_top_lists_async, so a
    cold cache fills itself in for the next call rather than staying empty
    forever."""
    cache = ensure_top_lists_async()
    index = _build_name_index(cache)
    results: dict[str, dict] = {}
    for name in names:
        popularity = commander_popularity(name, index=index)
        if popularity is not None:
            results[name] = popularity
    return results
