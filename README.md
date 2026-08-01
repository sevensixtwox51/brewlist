# Brewlist

Compare a [Moxfield](https://moxfield.com) or [Archidekt](https://archidekt.com)
decklist against your [ManaBox](https://manabox.app) card collection: see what
you already own, what's missing, and what it'll cost to finish the deck at
today's market prices — sorted, filtered, and priced automatically.

Available two ways: a **web app** (paste a URL, upload a CSV, done — no
terminal needed) and the original **CLI** (menu-driven, generates
Markdown/HTML/CSV reports on disk).

## What it does

- Fetches a decklist straight from Moxfield's or Archidekt's API given just
  the deck URL — paste either kind, it's auto-detected
- Matches it against a ManaBox collection export (`Quantity`, `Foil`,
  `Scryfall ID`, etc.) — no manual bookkeeping
- Prices **owned** cards by the *exact printing you actually have* (showcase,
  extended art, etched foil, ...), not just whichever printing the decklist
  happens to reference
- Prices **missing** cards from the true cheapest printing available across
  TCGPlayer, Card Kingdom, and ManaPool (decklists sometimes reference a
  rare, much pricier alt-art printing by default), foil and non-foil toggle
- All pricing comes from a local index built from MTGJSON's public bulk
  data, so every comparison is accurate from the first click — no separate
  "accurate pricing" step to run. The index is a one-time ~325MB download,
  refreshed automatically once a week (or on demand); after that, lookups
  are instant instead of a live call per card
- Each price shows a ▲/▼ trend arrow when it's moved 2%+ over the past
  week, using MTGJSON's 90-day price history
- Flags cards on WotC's official Commander "Game Changers" list with a badge
  on the card and a deck-wide count in the header — useful for checking
  your deck against its intended bracket
- For Commander decks, flags any card that's banned/not legal in the format
  with a badge and a header summary — Moxfield's format field is used
  directly; for Archidekt (which doesn't expose one), this is inferred from
  an EDH bracket value or a "Commander" category card being present
- For Commander decks, a "Combos" panel shows any known combos your deck
  already has all the pieces for, plus the most notable combos you're
  exactly one card away from completing — via
  [Commander Spellbook](https://commanderspellbook.com)'s public API, which
  also flags mass-land-denial and extra-turn cards and gives its own
  power/style rating for the deck (not the official WotC bracket system)
- Total deck value at today's market price, plus the owned portion
- Groups missing cards the way a physical store organizes its binders
  (Lands, Artifacts, then Colors split into mono-color / Multicolor /
  Colorless bins) for an exportable in-store shopping CSV
- A price slider to cap what you're willing to spend per card
- Per-card "reserved for another deck" overrides, so a card you technically
  own but have committed to a different build still shows as needed here —
  persisted so you don't have to re-mark it every time
- A blurred commander-art backdrop, because why not

## Requirements

- Python 3.9+ (developed and tested on 3.14)
- pip

## Install

```bash
git clone https://github.com/sevensixtwox51/brewlist.git
cd brewlist
pip3 install -r requirements.txt
```

If your Python install is "externally managed" (common on macOS with
Homebrew Python) and `pip3 install` refuses to run, add
`--break-system-packages`:

```bash
pip3 install --break-system-packages -r requirements.txt
```

## Usage: web app (recommended)

**macOS:** double-click `Brewlist.command` in Finder. It finds a free
port automatically, starts the app, and opens your browser to it. Closing
the terminal window (or the "Shut Down" button in the page itself) stops
the server.

**Any OS / from a terminal:**

```bash
python3 app.py
```

Then open **http://localhost:5000**. (macOS's AirPlay Receiver often
squats on port 5000 — if you hit a "port in use" error, either disable it
in *System Settings → General → AirDrop & Handoff*, or run on a different
port: `PORT=5050 python3 app.py`.)

From there:

1. Paste a Moxfield or Archidekt deck URL (e.g. `https://moxfield.com/decks/...`
   or `https://archidekt.com/decks/...`)
2. Upload your ManaBox collection export (`.csv`) the first time — after
   that, it's remembered, so you only need to re-upload when your actual
   collection changes
3. Hit **Compare**

That comparison already uses accurate cheapest-printing prices — the first
time you ever run one (or after the weekly auto-refresh), it downloads a
~325MB price index from MTGJSON, which takes under a minute; after that,
every comparison is instant. You can also refresh that index on demand from
the home page's **Refresh Price Data** button.

The app remembers each deck you've looked at as a "project": any
"reserved for another deck" overrides you save, plus your last-used
options, are recalled automatically the next time you paste that same URL
— no re-uploading, no re-checking boxes.

## Usage: CLI

```bash
python3 brewlist_cli.py
```

Menu-driven — it'll ask for the deck URL, look for a ManaBox export in
`~/Downloads` (or wherever you point it), and let you choose which report
formats to generate (HTML / Markdown / CSV, or all three).

Or scripted, for automation — `--open` generates the HTML report and opens
it in your browser:

```bash
python3 brewlist_cli.py https://moxfield.com/decks/... --open
```

(An Archidekt URL works the same way: `https://archidekt.com/decks/... --open`.)

Every card is priced from its cheapest printing using the local price index
described above (downloads automatically the first time, then instant). Use
`--refresh-price-index` on its own to force-update that index sooner than
its automatic weekly refresh.

Run `python3 brewlist_cli.py --help` for the full flag list.

## Where your data lives

Everything the app remembers — your uploaded collection, any saved
per-deck overrides, and the local cheapest-price index (see above) — is
stored locally under `data/`, which is excluded from version control
(`.gitignore`). Nothing leaves your machine except the calls to Moxfield's,
Archidekt's, Scryfall's, MTGJSON's, and (for Commander decks) Commander
Spellbook's public APIs needed to fetch decklists, prices, and combos.

## Project layout

| File | Purpose |
| --- | --- |
| `brewlist_core.py` | Shared logic: Moxfield/Archidekt/Scryfall/MTGJSON fetching, ManaBox parsing, price comparison, HTML report rendering. No UI dependencies — imported by both entry points below. |
| `app.py` | Flask web app. |
| `brewlist_cli.py` | Terminal CLI (interactive menu or scriptable flags). |
| `Brewlist.command` | macOS double-click launcher for the web app. |
