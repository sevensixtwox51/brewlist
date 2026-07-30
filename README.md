# Moxfield vs. ManaBox

Compare a [Moxfield](https://moxfield.com) decklist against your
[ManaBox](https://manabox.app) card collection: see what you already own,
what's missing, and what it'll cost to finish the deck at today's market
prices — sorted, filtered, and priced automatically.

Available two ways: a **web app** (paste a URL, upload a CSV, done — no
terminal needed) and the original **CLI** (menu-driven, generates
Markdown/HTML/CSV reports on disk).

## What it does

- Fetches a decklist straight from Moxfield's API given just the deck URL
- Matches it against a ManaBox collection export (`Quantity`, `Foil`,
  `Scryfall ID`, etc.) — no manual bookkeeping
- Prices **owned** cards by the *exact printing you actually have* (showcase,
  extended art, etched foil, ...) via Scryfall, not just whichever printing
  the Moxfield decklist happens to reference
- Prices **missing** cards from Moxfield's TCGPlayer / Card Kingdom / ManaPool
  data, cheapest store first, foil and non-foil toggle
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
git clone https://github.com/sevensixtwox51/mtg-inventory.git
cd mtg-inventory
pip3 install -r requirements.txt
```

If your Python install is "externally managed" (common on macOS with
Homebrew Python) and `pip3 install` refuses to run, add
`--break-system-packages`:

```bash
pip3 install --break-system-packages -r requirements.txt
```

## Usage: web app (recommended)

**macOS:** double-click `Compare_Deck.command` in Finder. It finds a free
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

1. Paste a Moxfield deck URL (e.g. `https://moxfield.com/decks/...`)
2. Upload your ManaBox collection export (`.csv`) the first time — after
   that, it's remembered, so you only need to re-upload when your actual
   collection changes
3. Hit **Compare**

The app remembers each deck you've looked at as a "project": any
"reserved for another deck" overrides you save, plus your last-used
options, are recalled automatically the next time you paste that same URL
— no re-uploading, no re-checking boxes.

## Usage: CLI

```bash
python3 moxfield_vs_collection.py
```

Menu-driven — it'll ask for the deck URL, look for a ManaBox export in
`~/Downloads` (or wherever you point it), and let you choose which report
formats to generate (HTML / Markdown / CSV, or all three).

Or scripted, for automation — `--open` generates the HTML report and opens
it in your browser:

```bash
python3 moxfield_vs_collection.py https://moxfield.com/decks/... --open
```

Run `python3 moxfield_vs_collection.py --help` for the full flag list.

## Where your data lives

Everything the web app remembers — your uploaded collection and any saved
per-deck overrides — is stored locally under `data/`, which is excluded
from version control (`.gitignore`). Nothing leaves your machine except
the calls to Moxfield's and Scryfall's public APIs needed to fetch decklists
and prices.

## Project layout

| File | Purpose |
| --- | --- |
| `mtg_core.py` | Shared logic: Moxfield/Scryfall fetching, ManaBox parsing, price comparison, HTML report rendering. No UI dependencies — imported by both entry points below. |
| `app.py` | Flask web app. |
| `moxfield_vs_collection.py` | Terminal CLI (interactive menu or scriptable flags). |
| `Compare_Deck.command` | macOS double-click launcher for the web app. |
