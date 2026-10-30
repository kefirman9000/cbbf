<p align="center"><img src="docs/banner.png" alt="CBBF Bot Farm" width="100%"></p>

# CBBF — Bot Farm

Raise a whole farm of little dragon bots. They hatch, compete, learn, breed and evolve on their own while you
watch, and the best ones end up in your collection.

<p align="center"><img src="docs/screenshots/main-farm.png" alt="The Bot Farm" width="100%"></p>

## Getting started
- **Windows:** Code > Download ZIP, extract the whole folder, double-click **Run CBBF.bat**
- **Mac / Linux:** `python3 main.py` (Python 3.8 or newer with Tkinter)

Updates arrive inside the app: when a new version is here, an **Update** button appears, shows what's new,
and installs only when you accept. **Tools > Undo last update** always takes you back.

---

## The farm
Every dragon is a bot with its own personality: a strategy, its own settings, its own pace (from every
15 seconds to every few hours) and a set of evolvable **traits**. They all work side by side, and every one
of them is scored all the time.

- **Working** dragons flap their wings and look around. **Resting** ones curl up and snooze, and a dragon
  breathes fire when it makes a move.
- Each card shows the dragon's **level, XP and health**, its status (Winning, Shaky, Standby, Resting,
  Learning) and its stats. Filter the farm by All, Working, Winning, Sleeping or Caught.
- Double-click a dragon for its full card: track record, best conditions, traits, family and a chart of every
  move it made.

<p align="center"><img src="docs/screenshots/bot-card.png" alt="A dragon's card" width="70%"></p>

## How they learn
The farm runs like a tournament that never ends:

- **Scoring:** dragons are judged on recent results, on how steady they are, on how far they've fallen from
  their best, and on whether they actually beat doing nothing.
- **Conditions:** the farm recognises different conditions (trending up, trending down, choppy, quiet) and
  remembers which dragons shine in which.
- **The winners lead.** Steady winners get more say; shaky ones get less; failing ones rest.
- **Retirement:** dragons that keep failing, or never win for hours, retire to make room.

## Breeding and evolution
- **Mutation:** winners lay eggs, giving tweaked copies with slightly different settings, pace and traits.
- **Crossbreeding:** two winners of the same kind produce a child that mixes both parents' genes.
- **Fresh blood:** brand-new random dragons keep the gene pool from going stale.
- **Traits** every dragon can evolve, switch on or off, and pass to its children:
  - its own safety exit and its own target
  - a trailing exit that follows the high
  - a maximum time to hold
  - a cooldown after each exit
  - a trend filter
  - a minimum-activity filter
  - an "overheated" guard
  - signal confirmation
  - how many times it may add to a position
  - exit style
  - which conditions it is allowed to work in
- **Family tree:** see every dragon's ancestry, which bloodlines thrived and which died out.

<p align="center"><img src="docs/screenshots/family-tree.png" alt="Family tree" width="80%"></p>

## Fourteen breeds
Trend followers, quick trend, momentum, momentum shift, breakout, volume breakout, dip buyers, range bounce,
band traders, snap-back, VWAP snap-back, range channel, dips-in-an-uptrend and a steady buyer. Some of them
read more than one kind of data (highs, lows and activity), not just a single line.

## Bot Lab
Train a fresh farm on a long stretch of the past, freeze the best dragons, then send them into a later stretch
they have never seen. Only the ones that still win there **pass**. Run several rounds with different seeds to
tell real talent from luck.

<p align="center"><img src="docs/screenshots/bot-lab.png" alt="Bot Lab" width="85%"></p>

## Collect them all
Press **Catch!** on any dragon to keep it forever.

- **Names:** every caught dragon gets a name, like Silent Bison or Nimble Walrus.
- **Rarity:** from **Common, Uncommon, Rare and Epic** up to **Legendary**, earned by how well it has really done.
  The occasional one is a ✨ **shiny**.
- **Levels:** catching a dragon again as it keeps proving itself levels it up and can raise its rarity.
- **Hall of Fame:** long-term champions join it and are caught automatically.
- **Team:** pick a **team of up to 6** and run the farm with just your team, either locked as picked or free
  to breed.
- **Sharing:** export dragons to a small file to share; imported dragons have to earn their place on your farm.

<p align="center"><img src="docs/screenshots/collection.png" alt="Bot Collection" width="85%"></p>

## Charts and screens
- Line, area or candle charts, from the last 30 ticks to a whole day
- Split view: two charts side by side
- Markers for every move the selected dragon made, plus a hover readout
- Portfolio view with badges, allocation bar and details per holding
- Dark and light themes, a resizable chart, and keyboard shortcuts (Help > Keyboard shortcuts)
- Optional phone alerts (Discord / Telegram) when a dragon joins the Hall of Fame

<p align="center"><img src="docs/screenshots/chart-split-candles.png" alt="Split chart" width="100%"></p>
<p align="center"><img src="docs/screenshots/main-light.png" alt="Light theme" width="100%"></p>

## Your data
Everything stays on your computer in `~/.cbbf/` (Windows: `C:\Users\<you>\.cbbf`): settings, saved farms,
Hall of Fame, collection and logs. **File > Open data folder** opens it.

---

Past results never guarantee future ones, even for a Legendary dragon. Use at your own risk.
