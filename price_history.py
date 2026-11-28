"""Price history for the cards you own.

MTGJSON's AllPrices file carries about 90 days of daily TCGplayer prices per printing. The card database throws all of that
away except today's price and a 7-day change, so this module keeps the daily series for the printings in your collection,
in data/price_history.json, and keeps adding to it every time the database is refreshed (so it grows past 90 days).

It is the same TCGplayer price the app already values your cards at (see brewlist_core.price_for_printing), per finish:
"n" nonfoil, "f" foil, "e" etched. Only your own printings are stored, which keeps the file a few megabytes.

Nothing here imports brewlist_core at the top: brewlist_core's index rebuild imports this module to capture history while it
is already reading the price file.
"""
import datetime
import gzip
import json
import os

HISTORY_NAME = "price_history.json"
KEEP_DAYS = 800
_FINISH_ORDER = {"etched": ("e", "f", "n"), "foil": ("f", "n", "e"), "normal": ("n", "f", "e")}  # what price_for_printing falls back to

_cache: dict = {"path": None, "mtime": None, "ids": {}}


def history_path(data_dir: str) -> str:
    return os.path.join(data_dir, HISTORY_NAME)


def owned_scryfall_ids(data_dir: str) -> set[str]:
    """Scryfall ids of every printing in the collection file next to the history (empty if there is none)."""
    from brewlist_core import load_collection  # imported here: brewlist_core imports this module
    path = os.path.join(data_dir, "collection.csv")
    if not os.path.isfile(path):
        return set()
    try:
        owned = load_collection(path)
    except (ValueError, OSError):
        return set()
    return {p.scryfall_id for oc in owned.values() for p in oc.printings if p.scryfall_id}


def capture(scryfall_id: str | None, paper: dict, ids: set[str], hist: dict) -> None:
    """While walking MTGJSON's cards: remember the TCGplayer daily series of one printing if it is one you own."""
    if not scryfall_id or scryfall_id not in ids:
        return
    retail = ((paper.get("tcgplayer") or {}).get("retail")) or {}
    entry = {}
    for key, finish in (("n", "normal"), ("f", "foil"), ("e", "etched")):
        series = retail.get(finish) or {}
        if series:
            entry[key] = {d: float(p) for d, p in series.items() if p is not None}
    if entry:
        hist[scryfall_id] = entry


def extract(prices_by_uuid: dict, all_printings: dict, ids: set[str]) -> dict:
    """The whole-file version of capture, for a history-only refresh."""
    hist: dict = {}
    if not ids:
        return hist
    for set_obj in all_printings.values():
        for card in set_obj.get("cards", []):
            pe = prices_by_uuid.get(card.get("uuid"))
            if pe:
                capture((card.get("identifiers") or {}).get("scryfallId"), pe.get("paper") or {}, ids, hist)
    return hist


def merge(data_dir: str, fresh: dict) -> None:
    """Adds freshly read series to the stored ones (new days are added, the same day is overwritten), drops anything older
    than KEEP_DAYS, and writes the file atomically."""
    path = history_path(data_dir)
    stored = _read(path)
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    for sid, finishes in fresh.items():
        into = stored.setdefault(sid, {})
        for key, series in finishes.items():
            into.setdefault(key, {}).update(series)
    for sid in list(stored):
        for key in list(stored[sid]):
            stored[sid][key] = {d: p for d, p in stored[sid][key].items() if d >= cutoff}
            if not stored[sid][key]:
                del stored[sid][key]
        if not stored[sid]:
            del stored[sid]
    os.makedirs(data_dir, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "updated": datetime.date.today().isoformat(), "ids": stored}, f, separators=(",", ":"))
    os.replace(tmp, path)


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("ids") or {}
    except (OSError, ValueError):
        return {}


def load(data_dir: str) -> dict:
    """{scryfall_id: {"n": {date: price}, ...}}, re-read only when the file changed."""
    path = history_path(data_dir)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache["path"] != path or _cache["mtime"] != mtime:
        _cache.update(path=path, mtime=mtime, ids=_read(path))
    return _cache["ids"]


def mtime(data_dir: str) -> float:
    try:
        return os.path.getmtime(history_path(data_dir))
    except OSError:
        return 0.0


def series_for(entry: dict | None, foil: bool, etched: bool, release: str | None = None) -> list[tuple[str, float]]:
    """The dated prices (oldest first) of the finish the app values this printing at, falling back the same way
    price_for_printing does when that finish has no price. Prices from before the set's release date (`release`, an ISO date)
    are dropped: they are preorder prices, which run well off what the card actually sells for."""
    if not entry:
        return []
    for key in _FINISH_ORDER["etched" if etched else ("foil" if foil else "normal")]:
        s = entry.get(key)
        if s:
            return sorted((d, p) for d, p in s.items() if not release or d >= release)
    return []


def change_since(series: list[tuple[str, float]], days: int) -> float | None:
    """Latest price minus the price `days` before the latest date; None if the series is too short to reach back that far."""
    if len(series) < 2:
        return None
    last_date = datetime.date.fromisoformat(series[-1][0])
    target = (last_date - datetime.timedelta(days=days)).isoformat()
    ref = None
    for d, p in series:
        if d <= target:
            ref = p
        else:
            break
    return None if ref is None else series[-1][1] - ref


def card_changes(prints: list[dict], hist: dict) -> dict:
    """What one card you own gained or lost, in dollars across all its copies: {"chg7", "chg30"} (None where the history
    does not reach back that far). `prints` are the card's printings as the Browse payload lists them: id, f (0/1/2), q."""
    out = {}
    for label, days in (("chg7", 7), ("chg30", 30)):
        total, seen = 0.0, False
        for x in prints:
            s = series_for(hist.get(x["id"]), x.get("f") == 1, x.get("f") == 2, x.get("rel"))
            ch = change_since(s, days)
            if ch is not None:
                total += ch * (x.get("q") or 1)
                seen = True
        out[label] = round(total, 2) if seen else None
    return out


def collection_change(printings: list[dict], hist: dict, days: int) -> float | None:
    """How much the cards you own gained or lost in value over `days`, counting only printings that were already priced (and
    released) back then, so a card that came out last week does not show up as a gain."""
    total, seen = 0.0, False
    for x in printings:
        ch = change_since(series_for(hist.get(x["id"]), x.get("f") == 1, x.get("f") == 2, x.get("rel")), days)
        if ch is not None:
            total += ch * (x.get("q") or 1)
            seen = True
    return round(total, 2) if seen else None


def collection_series(printings: list[dict], hist: dict) -> dict:
    """What the collection you own TODAY was worth on each past day: {"dates", "values", "covered", "copies", "total_copies"}.
    A printing counts from its set's release date on (never before: preorder prices are not what the card is worth), and one
    missing a day counts at its last known price. Printings with no history at all are left out and reported in `copies`."""
    rows = []
    covered = copies = 0
    for x in printings:
        q = x.get("q") or 1
        copies += q
        s = series_for(hist.get(x["id"]), x.get("f") == 1, x.get("f") == 2, x.get("rel"))
        if s:
            rows.append((q, s))
            covered += q
    dates = sorted({d for _, s in rows for d, _p in s})
    values = [0.0] * len(dates)
    for q, s in rows:
        j, cur = 0, None
        for i, d in enumerate(dates):
            while j < len(s) and s[j][0] <= d:
                cur = s[j][1]
                j += 1
            if cur is not None:
                values[i] += q * cur
    return {"dates": dates, "values": [round(v, 2) for v in values], "covered": covered, "total_copies": copies}


def build_history_only(data_dir: str, on_progress=None) -> int:
    """Downloads MTGJSON's AllPrintings and AllPrices and stores the history of your printings -- without rebuilding the card
    database. Returns how many printings were found. Raises ValueError on a network failure."""
    import urllib.error
    from brewlist_core import MTGJSON_ALL_PRICES_URL, MTGJSON_ALL_PRINTINGS_URL, _content_length, _download_to_file
    ids = owned_scryfall_ids(data_dir)
    if not ids:
        raise ValueError("No collection on file yet -- upload your ManaBox export first.")
    printings_size, prices_size = _content_length(MTGJSON_ALL_PRINTINGS_URL), _content_length(MTGJSON_ALL_PRICES_URL)
    total = printings_size + prices_size
    p_tmp, q_tmp = os.path.join(data_dir, "history.printings.download"), os.path.join(data_dir, "history.prices.download")
    try:
        try:
            _download_to_file(MTGJSON_ALL_PRINTINGS_URL, p_tmp, on_progress, 0, total)
            _download_to_file(MTGJSON_ALL_PRICES_URL, q_tmp, on_progress, printings_size, total)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise ValueError(f"Could not download MTGJSON's price data: {e}")
        if on_progress:
            on_progress(0, 0, "processing")
        with gzip.open(q_tmp, "rt", encoding="utf-8") as f:
            prices = (json.load(f) or {}).get("data") or {}
        with gzip.open(p_tmp, "rt", encoding="utf-8") as f:
            printings = (json.load(f) or {}).get("data") or {}
        hist = extract(prices, printings, ids)
        merge(data_dir, hist)
        return len(hist)
    finally:
        for p in (p_tmp, q_tmp):
            if os.path.exists(p):
                os.remove(p)
