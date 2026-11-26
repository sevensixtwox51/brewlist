# Brewlist

<p align="center">
  <a href="https://ko-fi.com/imtotallymeh"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="ko-fi"></a>
</p>

Brewlist builds Commander decks from the cards you already own,
and checks any [Moxfield](https://moxfield.com) or
[Archidekt](https://archidekt.com) decklist against your
[ManaBox](https://manabox.app) collection.

- **Build a deck** with the **Deck Builder**: pick a commander from your
  collection and Brewlist fills in the rest from what you own, using real
  EDHREC deck data, Scryfall Oracle Tags and Commander Spellbook combos. Then
  analyze it (mana curve, mana and turn-clock simulation, bracket estimate)
  and export it back to Moxfield or Archidekt.
- **Compare a decklist**: paste a deck URL to see what you already own,
  what's missing, and what it'll cost to finish at today's market prices,
  with budget alternatives, Game Changers, legality and combos.
- **See your collection**: a **Collection insights** page shows where your
  collection is deep and where it's thin, by color, role and theme, and a
  **Browse cards** view lists and adds up the cards you own under any filter.
- **Research a commander**: **Commander research** looks at any commander,
  owned or not: how people build it, how much of that build you own and what
  it costs to finish, and what the build is weak to.

Available two ways: a **web app** (upload a CSV, build or paste a URL, done —
no terminal needed) and the original **CLI** (menu-driven, generates
Markdown/HTML/CSV reports on disk) — the CLI only does the comparison; the
Deck Builder is web-app only.

![Deck Builder — build a Commander deck from your own collection](screenshots/deck-builder.png)

![Comparison results — pricing, Game Changers, Commander legality, deck completion](screenshots/results.png)

## The Decks page

The **Decks** page is home base for both tools: a **Compare** panel on top
(paste a decklist URL) and your saved decks as art tiles below, badged **Brew**
or **Comparison**, with a filter, a **Brew something new** tile, and a small
&times; to delete one. Links across the top open **Collection & Settings**,
**Collection insights** and **Commander research**. The app is dark by default
and switches to light with your system.

![Decks page: Compare panel, Brew and Comparison tiles](screenshots/decks.png)

## Deck Builder

Build a deck from what you own at `/builder` (or the **Brew something new**
tile). A saved brew can be run through the same report a compared deck gets.

### Find cards

- Filter by name, type, color or set. Colors go beyond WUBRG to every named
  guild, shard and wedge, with an **Exact colors** option; the color pips cycle
  **allowed**, **required**, off (the colorless pip adds colorless cards, **×**
  clears them all). The **Set** filter is a multiselect, newest first, showing
  how many cards you own of each set. Every dropdown shows live counts
- **Search by tag** uses Scryfall's Oracle Tags (the data behind `otag:`):
  try "sacrifice outlet" or "landfall". The 🏷 button on a tile lists its
  tags. The search box also matches a card's flavor or crossover name
  (type "Chekov" and you get Tatyova)
- **Sort** by name or set (like a binder), or for commanders by popularity or
  completeness. **View** switches between Cards, Gallery and Compact

![The three views side by side: Cards, Gallery and Compact](screenshots/view-modes.png)

- The 🔗 button opens **Works with...**: owned cards that complement it
  (sacrifice outlets with death payoffs, landfall with land ramp...) plus
  cards that share its rarer tags

![Works with: owned sacrifice outlets for a death-payoff creature](screenshots/works-with.png)

### Build the deck

- A new deck starts on the list of commanders you own; **★** sets one, and the
  grid goes back to every card. **+** adds a card, and you can drag a tile into
  the deck panel (drop a commander on the banner to set it). Clearing the
  commander brings the list back

![Dragging a card over the deck panel](screenshots/drag-to-add.png)

- **Rate my commanders** scores every commander you own by how much of its
  EDHREC deck your collection can fill (slot filler and signature cards show on
  hover), for its most common EDHREC deck type. Sort by **Most complete first**
  to see what you can build best today. Nothing is rated until you click it

![Commanders sorted by completeness](screenshots/commander-ratings.png)

- **Suggest cards** is a guided five-step flow: pick an EDHREC **deck type** for
  your commander, then review Synergy, Ramp, Draw, Interaction and Lands one
  step at a time, with a reason on each pick and **Not owned** or **Slot filler**
  tags where they apply. **Quick suggest** takes the whole list at once. Picks are ranked by combos you're one card from finishing
  (Commander Spellbook), then EDHREC synergy scores and Oracle Tags, then
  whatever the deck's shape (default 38 lands / 10 ramp / 10 draw / 11
  interaction / 30 synergy) still needs. No API key or per-build cost, and
  nothing is added until you approve it

![Suggest cards: step 1 of 5, Creatures & Synergy, with the Deck type picker](screenshots/suggest.png)

- **Engine commanders** (Teysa and Aristocrats, Kwia and Lifegain) get a
  plan-aware build that ranks enablers and payoffs first, and the standard
  shape is kept if the plan version's goldfish odds are clearly worse
- Options: a **Preferred theme** (Voltron, Reanimator, Stax, ...), **Set
  Selection** to limit which sets Suggest may use, **Intended bracket** to
  avoid too many Game Changers, and **Deck mix targets**
- **⇄** on a deck card suggests owned replacements for the same role.
  **Optimize Deck** looks for owned one-card-away combos and proposes swaps

### Analyze, save and export

- **Analyze Deck** shows a **Rule 0 summary** (colors, bracket, Game Changers,
  mass land denial, extra turns), combos, the mana curve and a sample hand,
  colored sources against Karsten's numbers, a **mana simulation**, a **Turn
  Clock** (how fast the deck kills an opponent who does nothing), a
  **Resilience** check, and **Synergy Packages**. **Print Battle Card** makes a
  one-page summary for the table
- **Matchups** (in Analyze) says what the deck is weak to and strong against.
  It reads what the deck and its commander depend on (death triggers, enter
  triggers, tokens, the graveyard, artifacts and so on), names the cards that
  punish each one and whether you already hold an answer, and lists commanders
  that go after it. A deck whose commander needs creatures to die, for
  example, is flagged for exile and bounce removal. It also rates the deck
  against common deck types, naming popular commanders of each kind (ones the
  build should handle, and ones that tend to give it trouble; click one to
  research it). These are rules of thumb from the deck's own cards, not a
  simulation, and it all works offline. Cards you already own are marked with
  a check, and the rest show the cheapest price

![Matchups: what a Teysa Karlov build is weak to, with the cards that punish it and the ones to answer them (shown on the Commander research page, where the same panel runs on a typical list)](screenshots/matchups.png)

![Analyze Deck: mana simulation, curve, sample hand](screenshots/analyze.png)

- **Save** keeps it on the Decks page. **View full report** runs it through the
  compare report. **Copy Decklist** (your exact printings) pastes into Moxfield
  or Archidekt, and **Export CSV** gives a binder-ordered list for pulling the
  cards

## Compare a decklist

Paste a Moxfield or Archidekt link on the Decks page:

- See what you own and what's missing, with prices at today's market. Owned
  cards are priced by the *exact printing you have*, missing ones by the
  cheapest printing across the stores you pick (TCGPlayer, Card Kingdom and
  ManaPool; Cardmarket is an opt-in extra, shown separately in EUR), with a foil
  toggle and ▲/▼ trend arrows from MTGJSON's 90-day history. The **Prices from**
  picker on the Compare panel saves as soon as you tick a box, and the same
  choice sets the prices everywhere else: Deck Builder suggestions, Commander
  research, Matchups and Collection insights (so "ManaPool only" means
  ManaPool prices throughout; a card that store doesn't list shows no price)
- Prices come from a local MTGJSON index (a one-time ~325MB download,
  refreshed weekly or on demand), so lookups are instant
- For missing cards over $20, **budget alternatives** from Scryfall's Oracle
  Tags (or the one you already own), no AI guesses. A stand-in for a dual land or
  a colored mana source has to make at least the same colors

![Budget-alternative suggestions on a card tile](screenshots/budget-alternatives.png)

- **Game Changers** and banned/not-legal cards are badged, with header counts
- A **Combos** panel lists combos you have and ones you're one card from
  (Commander Spellbook), and an **Estimated Bracket** follows WotC's published
  rules (1-2, 3 or 4+)

![Combos panel](screenshots/combos.png)

- Deck value, a **Deck Analysis** panel (curve, colors, sample hand), a
  **Shopping List**, an **In-Store CSV** grouped like a store's binders, a price
  slider, and per-card "reserved for another deck" overrides that are
  remembered
- The same **View** menu and flavor-name search as the builder, and a blurred
  commander-art backdrop

## Collection insights

The **Collection insights** link on the Decks page opens `/collection`, a
read-only look at what you own that stays out of the builder and the compare
flow. Everything is worked out on your computer from your collection, the local
card database and Scryfall's Oracle Tags.

- **Snapshot and colors:** unique cards, copies, market value, foils, sets, and
  your unique nonland cards by color identity
- **Roles by color:** a grid of ramp, card draw, spot removal, board wipes,
  counterspells, tutors, land search (landcycling, Cultivate and the like, kept
  out of Tutors), recursion, sacrifice outlets, token makers and lifegain
  against each color. Each cell shows your count and the share of all
  such cards that exist in that color. Every row is shaded from red (your
  lowest color for that job) to green (your highest), and a toggle switches the
  shading to the share you own instead of the count

![Collection insights: snapshot, colors and the role-by-color grid](screenshots/collection-insights.png)

- **Hover for a quick look, click for the cards:** hovering a number shows its
  count, the most-played cards and their art; clicking a role cell, a theme, a color, a card
  type, a curve bar or a creature type opens a card view of exactly those
  cards (hover a card to enlarge it; double-faced cards have a flip button), with a filter box (click the white tutors cell and you get your white
  tutors)

![Clicking a cell shows the cards behind it](screenshots/collection-cards.png)

- **Popular cards to add:** the same card view has a **Popular to add** tab with
  the most popular Commander-legal cards like these that you don't own (by EDHREC
  rank), each with its cheapest nonfoil price and a link to that listing, so a thin
  spot comes with suggestions for filling it (a cell with none owned opens on
  that tab)

![Popular to add: the most popular white tutors you don't own, with prices](screenshots/collection-popular.png)

- **Browse cards:** a second tab on the page lists every card you own (lands
  included) and lets you filter it by name or type, card type, color (click a
  color to allow it, again to require it, with an exact-colors toggle like the
  builder's), sets, price of a copy (judged by the priciest copy you own of a
  card), foils, and the roles and themes above. Sort by name, priciest copy,
  total value, mana value, popularity or copies. The totals follow the
  filters: unique cards, copies, total value, average per card and the
  priciest copy. It shows pictures or a compact list, with hover to enlarge and
  flip buttons. Click a card for a lightbox with a big picture and every
  printing of it you own (set, foil or not, copies and price; click one to see
  that art), and step through the list with the arrow keys. Foil printings
  get the same hover shimmer as the compare report's foil tiles, and in the
  lightbox the card tilts toward your pointer as if you were holding it, with a
  glare that sweeps across it (and a rainbow band for a foil). Surge foils are labeled as such
  (found from Scryfall's promo types; your foil printings are looked up once,
  the first time you open Browse, which takes a few seconds and needs the
  internet, and saved). Value is the
  market price of the exact printing you own, the same prices the Market
  value tile adds up

![Browse cards: green creatures worth at least $1, sorted by total value, with the totals above](screenshots/collection-browse.png)

- **Themes:** graveyard, sacrifice, tokens, +1/+1 counters, landfall,
  spellslinger and more, each showing how much of that plan's cards you own
  next to your other plans (Oracle Tags are crowd-made, so read it as "roughly
  how deep")

![Collection insights: how deep each theme is](screenshots/collection-themes.png)

- **Types, curve and tribes:** card types, the mana curve, and your deepest
  creature types

## Commander research

The **Commander research** link on the Decks page opens `/research`. Pick any
Commander-legal commander, one you own or one you're thinking of buying, and it
shows (the list is sorted by how popular each commander is as a commander on
EDHREC, with the same past 2 years / month / week choice the builder has):

- **How people build it:** EDHREC's deck types for that commander, most played
  first.
- **What you own of the typical build:** for the deck type you pick, the cards
  most often included (a consensus of what people play, not one real decklist),
  how many you own, the cheapest price of each one you're missing and the total
  to complete it, whether you own the commander, and the same "buildable from
  your collection" percentage the builder shows on commander tiles.
- **What that build is weak to:** the Matchups analysis from Analyze, run on
  the typical list, with a check mark on the answers you already own. Handy
  when someone keeps beating you with a commander and you want to know what to
  add to one of your decks, or when you're deciding what to build.

A commander's EDHREC data is fetched the first time you look at it and saved,
so only the first look at one that isn't saved yet needs the internet.

![Commander research: Teysa Karlov's deck types, what you own of the typical Aristocrats build and what it costs to finish](screenshots/commander-research.png)

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

Both the web app and the CLI check for updates when they launch and only say
something if there is one (a banner on the Setup or Decks page, or a line in
the terminal). To check on demand, use the **Check for Updates** button on the
Setup page, or:

```bash
python3 brewlist_cli.py --update
```

Either one runs a plain `git pull`, so it only works if you installed with
`git clone` (not a downloaded ZIP). Restart the app afterward to run the new
code.

## Usage: web app (recommended)

![Home page](screenshots/home.png)

Launch it:

- **macOS:** double-click `Brewlist.command`. If macOS says it can't be opened
  because it's from an unidentified developer (it does that when the file came
  from a ZIP download rather than `git clone`), right-click it and choose
  **Open**, or run `xattr -dr com.apple.quarantine Brewlist.command` once
- **Windows:** double-click `Brewlist.bat`
- **Linux:** run `./brewlist.sh` from a terminal
- **Any OS:** `python3 app.py`

It picks a free port (starting at 5050, since macOS's AirPlay Receiver often
uses 5000) and opens your browser. Close the terminal or use **Shut Down** to
stop it. If the server stops answering, a banner says so and points you to Copy
Decklist so an open deck isn't lost. It also **quits by itself after 15 minutes
of no use** (a tab left open doesn't count, and it never stops mid-job). Set
`BREWLIST_IDLE_EXIT_MINUTES` to change that (minimum 2, or `0` to turn it off;
it's off in Docker). `PORT`, `HOST` and `NO_BROWSER=1` are also available.

It opens to a **Setup** page: upload your ManaBox collection export (`.csv`;
drag it onto the drop zone or click to choose it) and hit **Continue to Decks**.
The collection is remembered, so you only return to Setup (the **⚙ Collection &
Settings** link) to replace it.

**To build a deck**

1. On the **Decks** page, click **Brew something new**
2. Pick a commander: a new deck starts on the commanders you own (**Rate my
   commanders** shows which you can build best); hit **★** on one
3. Hit **Suggest cards** and review the picks (or **Quick suggest**), then add or
   swap cards by hand
4. **Analyze Deck**, then **Save**, **Copy Decklist** or **View full report**

**To compare a decklist**

1. On the **Decks** page, paste a Moxfield or Archidekt deck URL
2. Hit **Compare**

Both tools use a local card database (prices, legality, Game Changers, Oracle
Tags). The first run downloads about 325MB from MTGJSON, which takes under a
minute, and then it's refreshed weekly or from the Setup page's **Refresh
Database** button. Each deck is remembered as a "project": a brew picks up where
you left off, and a compared deck recalls its saved overrides and options the
next time you paste the same URL.

## Usage: Docker

A `Dockerfile` and `docker-compose.yml` are included for the web app (the
CLI isn't containerized). There's no published image — Docker builds it
from source, so you still need the repo on your machine first:

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
   (macOS/Windows) or Docker Engine + the Compose plugin (Linux), and make
   sure it's running.
2. Clone the repo (same as the [Install](#install) step above — no need
   for the `pip3 install` part, the container handles its own
   dependencies):
   ```bash
   git clone https://github.com/sevensixtwox51/brewlist.git
   cd brewlist
   ```
3. Build and start it:
   ```bash
   docker compose up -d
   ```

Then open `http://localhost:5050`. Your ManaBox collection, saved
decks/brews, and the local MTGJSON price index live in a named Docker
volume (`brewlist-data`), so they survive `docker compose down` and
rebuilds — remove that volume if you ever want a clean slate.

To use a different host port, edit the `ports:` line in
`docker-compose.yml` (e.g. `"6000:5050"`), or without Compose:

```bash
docker build -t brewlist .
docker run -p 5050:5050 -v brewlist-data:/app/data brewlist
```

The in-app **Check for Updates** button can't pull updates from inside the
container itself (there's no git checkout in there to pull), so clicking
it just tells you to update from the host instead — same two commands
either way:

```bash
git pull
docker compose up -d --build
```

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
database (see above) — is stored locally under `data/`, which is
excluded from version control (`.gitignore`). Nothing leaves your
machine except the calls to Moxfield's, Archidekt's, Scryfall's,
MTGJSON's, EDHREC's, and (for Commander decks) Commander Spellbook's
public APIs/pages needed to fetch decklists, prices, combos, and
commander popularity/synergy data.

## Without internet

Brewlist keeps working from what it has stored: the builder, Suggest and
Optimize, Analyze, the goldfish sims and Collection insights all run
locally, using the card database, prices and EDHREC data from the last
time you were online. Card images are saved under `data/image_cache/` the
first time you see them, so cards you've already looked at still show;
ones you haven't are blank until you're back online. Things that need the
internet say so instead of failing quietly: pasting a Moxfield or Archidekt
link, Check for Updates, and refreshing the card database. Analyze shows
"combo lookup unavailable" rather than "0 combos" when Commander
Spellbook can't be reached. The image cache grows as you browse (roughly
15 KB per small card image, 100 KB per full-size one); delete the
`data/image_cache/` folder any time to clear it.

## Uninstalling

**Non-Docker:** stop the app (the **Shut Down** button, or close the
terminal window), then delete the folder you cloned into. Everything
Brewlist stores — your collection, saved decks, the price index — lives
inside that same folder under `data/`, so deleting it removes everything
in one step. The `flask`/`rich`/`pyfiglet` packages `pip3 install`d
earlier are small, shared Python libraries other tools may also use, so
there's no need to remove them separately unless you want to
(`pip3 uninstall flask rich pyfiglet`).

**Docker:**

```bash
docker compose down --rmi all -v
```

Stops and removes the container, the image Docker built, and the
`brewlist-data` volume (your collection, price index, saved decks) in one
step. Then delete the cloned folder the same way as above.

## Support

Brewlist is a free, personal project — if it's saved you time or money
building your next deck, consider [buying me a coffee on
Ko-fi](https://ko-fi.com/imtotallymeh). Not required, but always
appreciated!

## Project layout

| File | Purpose |
| --- | --- |
| `brewlist_core.py` | Shared logic: Moxfield/Archidekt/Scryfall/MTGJSON fetching, ManaBox parsing, price comparison, HTML report rendering. No UI dependencies — imported by both entry points below. |
| `deck_builder.py` | Deck Builder logic: owned-collection browsing data, the fill-the-gaps Suggest heuristic (now synergy-weighted, see `edhrec_data.py`), the Optimize combo-completion pass, brew-to-report conversion. No UI dependencies — imported by `app.py` only (the Deck Builder is web-app only). |
| `edhrec_data.py` | EDHREC commander-popularity ranks and per-commander card synergy scores — the real, non-AI data source behind the Deck Builder picker's popularity badges and Suggest/Optimize's synergy weighting. No UI dependencies — imported by `deck_builder.py`/`app.py`. |
| `synergy.py` | Deck-plan recognition: what an engine commander wants (sacrifice, tokens, counters, ...), used by the autobuilder and the plan guard. |
| `tag_synergy.py` | Scryfall Oracle Tags from the local card database: tag search, "Works with…" and similar-card suggestions. |
| `mana_sim.py`, `turn_clock.py` | Goldfish simulations behind the Analyze panel's Mana Sim and Turn Clock. |
| `index_worker.py` | Runs Refresh Database in its own process so the web app stays responsive while it downloads and rebuilds. |
| `collection_insights.py`, `collection_page.py` | The Collection insights page: the calculations (read-only, nothing from the builder or compare code) and its page. |
| `matchups.py` | The Matchups section of Analyze: what a deck is weak to and strong against (read-only, local data). |
| `research.py`, `research_page.py` | The Commander research page: the calculations (read-only, built on EDHREC's saved data and the Matchups analysis) and its page. |
| `matchups_ui.py` | The Matchups panel's look and HTML, shared by Analyze and Commander research. |
| `app.py` | Flask web app, including the Deck Builder (`/builder`). Its `static/` folder holds the mana-symbol SVGs. |
| `brewlist_cli.py` | Terminal CLI (interactive menu or scriptable flags). |
| `Brewlist.command` | macOS double-click launcher for the web app. |
| `Brewlist.bat` | Windows double-click launcher for the web app. |
| `brewlist.sh` | Linux/other-POSIX launcher for the web app (run from a terminal). |

## AI disclosure

This project is built almost entirely by AI (Claude Code) — see
[AI-DECLARATION.md](AI-DECLARATION.md) for what that means in practice.
That's about the *code*, though — every feature the app itself runs
(pricing, legality, budget alternatives, Suggest, Optimize, commander
popularity/synergy) is a deterministic heuristic over real data
(Scryfall Oracle Tags, MTGJSON, Commander Spellbook, EDHREC), explicitly
*not* an AI guess. The app makes no AI calls at runtime.

<p align="center">
  <a href="AI-DECLARATION.md"><img src="https://img.shields.io/badge/%E4%B7%BC%20AI--DECLARATION-auto-ede9fe?labelColor=ede9fe" alt="AI-DECLARATION: auto" height="28"></a>
  <a href="https://www.realgoodai.org/real-rating"><img src="https://images.squarespace-cdn.com/content/v1/677c1269fe60517a0976d6fc/fbf2122d-5b3f-474d-8466-bc18298c15e2/5+REAL+rating%404x.png" alt="REAL Rating: Level 5, Full AI" height="28"></a>
</p>
