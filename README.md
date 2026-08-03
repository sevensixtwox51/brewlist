# Brewlist

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/imtotallymeh)

Compare a [Moxfield](https://moxfield.com) or [Archidekt](https://archidekt.com)
decklist against your [ManaBox](https://manabox.app) card collection: see what
you already own, what's missing, and what it'll cost to finish the deck at
today's market prices — sorted, filtered, and priced automatically.

Available two ways: a **web app** (paste a URL, upload a CSV, done — no
terminal needed) and the original **CLI** (menu-driven, generates
Markdown/HTML/CSV reports on disk).

![Comparison results — pricing, Game Changers, Commander legality, deck completion](screenshots/results.png)

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
  rare, much pricier alt-art printing by default), foil and non-foil toggle.
  Pick which stores to price from on the home page (all three checked by
  default, saved for next time) — Cardmarket is also available as a 4th,
  opt-in option, shown separately in EUR since it's never blended into any
  dollar total or "cheapest" comparison
- All pricing comes from a local index built from MTGJSON's public bulk
  data, so every comparison is accurate from the first click — no separate
  "accurate pricing" step to run. The index is a one-time ~325MB download,
  refreshed automatically once a week (or on demand); after that, lookups
  are instant instead of a live call per card
- Each price shows a ▲/▼ trend arrow when it's moved 2%+ over the past
  week, using MTGJSON's 90-day price history
- For missing cards priced $20+, suggests cheaper same-role alternatives to
  buy, or flags it if you already own one — e.g. a missing Mana Crypt
  surfaces budget mana rocks, a missing fetch land surfaces cheaper fetches
  you don't have (or the one you already do). Uses Scryfall's community-
  curated Oracle Tags to find same-function cards — no AI-generated guesses

  ![Budget-alternative suggestions on a card tile](screenshots/budget-alternatives.png)
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

  ![Combos panel](screenshots/combos.png)
- Total deck value at today's market price, plus the owned portion
- A **Shopping List** button opens a plain-text, alphabetized list of
  what's still missing (quantity + name), pre-selected and one click away
  from your clipboard
- A separate **Export In-Store CSV** button groups missing cards the way a
  physical store organizes its binders (Lands, Artifacts, then Colors
  split into mono-color / Multicolor / Colorless bins) as a downloadable
  CSV file
- A price slider to cap what you're willing to spend per card
- Per-card "reserved for another deck" overrides, so a card you technically
  own but have committed to a different build still shows as needed here —
  persisted so you don't have to re-mark it every time
- A blurred commander-art backdrop, because why not

## Requirements

- Python 3.9+ (developed and tested on 3.14)
- pip
- git (needed to install and to check for updates -- see below)

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

On Windows, use `pip` and `python` instead of `pip3`/`python3` if those
aren't recognized (depends on how Python was installed).

## Updating

The web app has a **Check for Updates** button on the home page. From the
CLI:

```bash
python3 brewlist_cli.py --update
```

Both pull the latest code straight from GitHub with a plain `git pull` --
no separate download step, works the same on macOS, Windows, or Linux.
This only works if you installed with `git clone` as shown above (not a
downloaded ZIP); if it pulls new commits, restart the app afterward to
actually run the new code.

## Usage: web app (recommended)

![Home page](screenshots/home.png)

**macOS:** double-click `Brewlist.command` in Finder. If you see "'Brewlist.command'
can't be opened because it is from an unidentified developer" -- this happens
when the file was downloaded via a browser (e.g. GitHub's "Download ZIP"
button) rather than `git clone`, which triggers macOS's Gatekeeper quarantine
on unsigned scripts. Right-click the file and choose **Open** instead of
double-clicking (bypasses it just for that file), or run this in Terminal
first:

```bash
xattr -dr com.apple.quarantine Brewlist.command
```

**Windows:** double-click `Brewlist.bat` in File Explorer.

**Linux:** most file managers won't run a `.sh` file on double-click by
default, so from a terminal in the `brewlist` folder:

```bash
./brewlist.sh
```

**Any OS / from a terminal:**

```bash
python3 app.py
```

Either way, it finds a free port automatically (starting at 5050, since
macOS's AirPlay Receiver often squats on 5000) and opens your browser to
it -- same behavior on macOS, Windows, and Linux. Closing the terminal
window (or the "Shut Down" button in the page itself) stops the server.
Set `PORT` (e.g. `PORT=6000 python3 app.py`) to force a specific port
instead of auto-picking one.

From there:

1. Paste a Moxfield or Archidekt deck URL (e.g. `https://moxfield.com/decks/...`
   or `https://archidekt.com/decks/...`)
2. Upload your ManaBox collection export (`.csv`) the first time — after
   that, it's remembered, so you only need to re-upload when your actual
   collection changes
3. Hit **Compare**

That comparison already uses accurate cheapest-printing prices — the first
time you ever run one (or after the weekly auto-refresh), it downloads a
~325MB card database from MTGJSON, which takes under a minute; after that,
every comparison is instant. You can also refresh that database on demand
from the home page's **Refresh Database** button.

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

Every card is priced from its cheapest printing using the local card
database described above (downloads automatically the first time, then
instant). Use `--refresh-price-index` on its own to force-update that
database sooner than its automatic weekly refresh.

Run this for the full flag list:

```bash
python3 brewlist_cli.py --help
```

## Where your data lives

Everything the app remembers — your uploaded collection, any saved
per-deck overrides, your store-pricing preference, and the local card
database (see above) — is stored locally under `data/`, which is excluded
from version control
(`.gitignore`). Nothing leaves your machine except the calls to Moxfield's,
Archidekt's, Scryfall's, MTGJSON's, and (for Commander decks) Commander
Spellbook's public APIs needed to fetch decklists, prices, and combos.

## Support

Brewlist is a free, personal project — if it's saved you time or money
building your next deck, consider [buying me a coffee on
Ko-fi](https://ko-fi.com/imtotallymeh). Not required, but always
appreciated!

## Project layout

| File | Purpose |
| --- | --- |
| `brewlist_core.py` | Shared logic: Moxfield/Archidekt/Scryfall/MTGJSON fetching, ManaBox parsing, price comparison, HTML report rendering. No UI dependencies — imported by both entry points below. |
| `app.py` | Flask web app. |
| `brewlist_cli.py` | Terminal CLI (interactive menu or scriptable flags). |
| `Brewlist.command` | macOS double-click launcher for the web app. |
| `Brewlist.bat` | Windows double-click launcher for the web app. |
| `brewlist.sh` | Linux/other-POSIX launcher for the web app (run from a terminal). |
