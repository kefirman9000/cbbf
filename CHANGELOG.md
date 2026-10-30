# Changelog

## 1.9.1
- **Design an egg** (Bot Farm tab or Ctrl+E): write your own bot. Pick a strategy and its numbers (for example RSI:
  buy below 28, sell above 72), how often it decides, its own safety stop and profit target, and habits like a
  trailing stop, minimum hold or which markets it may trade. Hatch it on the farm as a royal egg, save it to your
  Library, or both. Right-click any saved bot and choose "Use as a template" to start from it.
- **Pick up where you left off**: if the bot was running when CBBF closed (or the computer restarted), the session
  resumes on the next start. Practice resumes straight away; Live asks first and you still type LIVE.
  (Settings > Library & Hall of Fame; stopping the bot yourself turns it off for next time.)
- **Lighter**: hidden tabs no longer redraw on every decision (Trades and the Bot Farm catch up when you open them).

## 1.9.0
Smarter learning
- **Prove it on new prices**: a bot can only become Winning after it has also made money on live prices that
  came in after it was created (new bots are first judged partly on replayed history, where luck looks like skill).
  Until then it shows "Proving itself".
- **Risk-adjusted ranking**: winners are ranked by steady gains (gain per unit of downside swing), so smooth
  gainers beat wild lucky ones.
- **Diversity**: parents are picked with fitness sharing, so one lucky strategy family can't crowd out the rest.
- **Adaptive evolution**: bolder mutations when nothing is winning, fine-tuning when several bots are.

Your own bloodlines
- **Breed from your own bots**: drop Library or Hall of Fame bots onto the farm ("Add to farm") to compete and breed,
  or select two in the Library and press **Breed pair**. Everything descended from your saved bots hatches from a
  **royal egg** (gold, with a crown mark), and the royal line carries on through their children.
- **Coloured eggs** by dragon type (fire red, ice blue, lightning yellow, space purple), or all purple
  (Settings > Timing).

More strategies
- Four new strategy families the farm can mutate and breed: **Supertrend**, **Cloud cross (Ichimoku)**,
  **Speed shift (rate of change)** and **Trend ribbon** (three moving averages in line).

Exports other programs (and AIs) can understand
- **Export bots** now asks what to export: your selection, all bots, BEASTS, Rising, Eggs, everything not resting,
  your Hall of Fame or your Library. The JSON spells out every bot's rules in plain English, explains each setting
  and includes the formula for each strategy, so another app or an AI can rebuild it. Optional plain-text summary.

Smoother
- **Motion: Full / Light / Off** right on the Bot Farm tab.
- Fixed eggs flickering now and then (redrawn cards briefly covered the animation).
- Clicking a dragon is snappier: only the highlight moves; charts redraw once, a moment later.
- Settings > Basics now includes the trailing stop and minimum hold, with clearer wording.

Faster and bigger farms
- **5-second and 10-second decision speeds** (Settings > Timing), for a livelier farm.
- **Farm size up to 200 bots**, adjustable right on the Bot Farm tab, and **+ Add eggs** to hatch new random bots
  whenever you like.
- Much faster: bots read price history through zero-copy views, so loading history, replays, backtests and the Bot
  Lab take a fraction of the time and memory, even with lots of bots.
- The farm shows its goal plainly: make money after fees, and more than just holding. That is what every bot is
  scored on.

Longer holds
- Bot paces now go up to 60x your bar length (e.g. one decision an hour at 1-minute bars). Starter bots include slow
  copies, and 2,000 bars of history are loaded so slow bots can start right away.
- New trait the bots can evolve: **minimum hold** (keeps a position for a while before its strategy may sell; its
  stops still work).
- New account setting **Minimum hold** (Settings > Orders): minutes before a strategy signal may sell a coin.

## 1.8.2
- Fix: clicking column headings to sort a list could pop up "pick a strategy first". Double-clicks on headings or empty space are ignored now.
- Themed dragons replace the animal characters (fire, ice, lightning, space, and skeletons for resting bots).
- **Watchlist** tab: add any coin (type it or Browse coins). Shows live price, change since you added it, 24h change
  and volume, how much you own, and price alerts ("tell me when it goes above / below ..."). Right-click or the
  buttons to buy, sell, chart it, or let the bots trade it.
- **Prices stay live while the bot is stopped**: a light background check (one request for all your watched and owned
  coins every 5 seconds) keeps Total value and the watchlist up to date.
- **Buy... / Sell...** buttons in the side panel. With no session open, they offer to open one (the bot stays off),
  so you can trade by hand any time. Any watched coin can be bought.

## 1.8.1
- **Chart dashboard**: show 1, 2, 3, 4 (2x2) or 6 (3x2) charts at once ("Charts" menu above them). Each chart has its
  own picker for what it shows and its own Line / Area / Candles style, and remembers it:
  - Account value (vs just holding)
  - Profit / loss % (account and holding)
  - Drawdown (how far below your best point)
  - Cash vs invested
  - Selected bot (its equity and its trades)
  - Any coin's price, with your trades and the selected bot's entries and exits
  Time range, %, markers and grid are shared by all charts. Clicking a coin's name in the Portfolio or "Show on chart"
  in a bot's menu puts it on a chart.
- **Fee-aware trading** (on by default, Settings > Orders): bots and the account won't sell for a profit too small to
  cover the round-trip fees. Losses, stops and Sell everything still sell. Bots were already scored after fees and
  spread; this stops them churning out tiny "wins" that are really losses.
- **Fees paid** on the Portfolio tab and on every bot's card.
- **Themed dragons** by strategy family: fire/devil dragons (trend followers), ice dragons (dip buyers), lightning
  dragons (breakouts), space dragons (dips in an uptrend), and resting bots turn into **skeleton dragons**. Their status
  glows on the ground under them. Settings > Timing switches to classic dragons coloured by status.
- **Candle timeframe** picker for the charts (Auto, 5 s up to 4 hr) and a 1-week range.
- A line under the money tiles now shows how many practice trades the bots made versus your account's own trades,
  so it's clear why Cash left / Total value only change when the account itself trades.

## 1.8.0
- **The Bot Farm has four sections:**
  - ◒ **Eggs**: new bots still learning. Eggs wobble and crack as they get close to hatching.
  - ▲ **Rising**: hatched bots doing OK (shaky, standby, or winning but not dominant yet).
  - ♛ **BEASTS**: the dominant ones, winning steadily and ahead of holding. Big crowned dragons that
    breathe fire. The log (and a fanfare) announces every new BEAST.
  - ★ **Hall of Fame**: your all-time champions, marked when they're back on the farm.
  Plus All and Caught. Each section shows how many dragons it has.
- **Bot Library and Hall of Fame are separate.** The Library (old Collection, Ctrl+B) keeps every bot you save plus
  long-term winners the farm saves for you. The **Hall of Fame now holds only the bots you pick** (nothing goes in
  on its own unless you turn that on in Settings > Library & Hall of Fame).
- **Bots trading: Whole farm / My team / Hall of Fame only** (Strategy panel). Practice and Live remember their own
  choice, and the Live confirmation spells out exactly which bots can trade your real money. Hall of Fame only is
  always locked: nothing new is bred.
- **Right-click menus** on the farm cards, the scoreboard, the dragon strip, the Library and the Hall of Fame:
  details, show on chart, save to Library, add to / remove from Hall of Fame, add to team, trade size, export.
- **View: Dragons, Simple cards or List**, for when you'd rather have a plain table.
- **Lighter and in sync**: one animation clock for everything (all dragons move together), only the visible view is
  drawn, nothing is drawn while minimised, cached look-ups, and Settings > Timing > Animations: Full, Light or Off.
- **Update button always in the top bar**: "Check for updates", turning green when a new version is out.
- Symbols that display everywhere (some emoji showed as boxes on Windows).

## 1.7.4
- **CBBF has its own sounds**, so you can tell them apart from Windows: a soft blip for trades (only if
  "...also every trade" is on), a two-note chime for notices, a little fanfare when a bot joins the Hall of Fame,
  and a low double tone for problems. Volume slider and a play button to hear them in Settings (bottom). The
  mute bell in the top bar silences everything.
- **Turn individual sounds on or off** (trades, notices, Hall of Fame, problems), each with its own play button.
- GitHub now builds a downloadable release zip automatically for every new version.

## 1.7.3
- **Quieter**: no more Windows "ding" on every trade. Sound is now off unless you turn it on (Settings, bottom),
  routine trades no longer notify unless you tick "...also every trade", and there's a mute button (bell) in the
  top bar. On Windows, notifications are a small silent pop-up in the corner instead of the system sound.

## 1.7.2
- **Bot Farm grid** (the new first tab): a live card for every bot with an animated dragon, level, XP and health
  bars, a status badge and stats. Filter by All / Working / Winning / Sleeping / Caught. Click to select (Ctrl-click
  for several), double-click for the full card, **Catch!** to collect.
- **Traits the bots evolve themselves**: trailing stop, max hold time, cooldown after selling, trend filter,
  minimum volatility, overbought guard, signal confirmation, max entries, stops-only exit and a market-mood
  gate. They mutate, crossbreed and get selected like everything else. The Bot Lab tests bots with their traits
  exactly as they'd run live.
- **Charts**: Line, Area or Candles; Split view (account and a coin side by side); ranges up to 1 day (long ranges
  switch to bar history); grid on/off; coin lines in the coin's colour.
- Bot cards show traits; exported, collected and Hall of Fame bots keep them.

## 1.7.1
- **Fix: 1.7.0 crashed on start.** Fixed.
- **Bot Collection** (Ctrl+B): press **Catch!** on any bot to collect it. Each gets a name, a rarity (Common to
  Legendary, rare shinies) and a level that rises as it keeps proving itself. Pick a **team of up to 6** and tick
  "Trade with my team". Hall of Fame bots are caught automatically.
- **Your bots are little dragons** now: working ones flap and look around, sleeping ones snooze, and they breathe fire
  when they buy. Hover for details, click to select, double-click for the full card. New dragon app icon.
- **Portfolio tab**: coin badges, value, profit/loss, average cost vs price, 24h change, share of the portfolio,
  and an allocation bar.
- **Buy and Sell by hand** (Portfolio tab, or the buttons on each coin), in Practice and Live mode.
- **Staking info** for each coin: whether it can be staked, plus estimated earnings at the rate you're offered.
- **Bots set the stops**: in Wildcard, the account's Safety stop and Lock in profit can follow what the winning bots
  learned (Settings > Orders). Your own numbers are the fallback.
- Clearer Settings > Basics, with examples.
- Fixed a possible crash when the coin list or a test alert came back from the internet.

## 1.7.0
**Renamed to CBBF.** Your data, saved login and bot files carry over automatically on first start.

Bots and learning
- **Beat-holding rule**: a strategy only counts as Winning if it also beats just holding the same coins.
- **Lifetime track record** for every bot: return, return vs holding, win rate, worst drop, time spent winning, best kind of market.
- **Hall of Fame**: long-term winners are saved automatically (Settings > Hall of Fame) and the best come back when Wildcard starts.
- **Bot Lab** (Ctrl+L): evolve bots on past prices, test them on newer prices they never saw, over several rounds. Export or keep the ones that pass.
- **Crossbreeding**: new bots can mix the settings of two winners.
- **Bots evolve their own stop-loss and take-profit.**
- **Market mood**: trending up / down, choppy or quiet, per coin. Bots that do well in the current mood get more weight.
- **Family tree** of which bot came from which.
- **Six new strategies**: Quick trend (EMA), Range bounce (Stochastic), Dips in an uptrend, plus three that use highs, lows and volume: Volume breakout, VWAP snap-back, ATR channel.

Trading
- **15-second and 30-second decision speeds**, alongside 1, 5, 15 and 60 minutes.
- **Limit (maker) orders** with a wait time and optional market fallback (Settings > Orders).
- **Trailing stop**, and a **max trades per hour** brake.
- Practice mode now charges **spread and slippage** on market orders.
- **Coin browser** (Ctrl+K): any coin with 24h change and volume, plus **Bot picks** to let the bot choose busy, moving coins.
- **Phone alerts** via Discord or Telegram.
- One price request for all coins (faster, fewer rate limits) and a **connection light** in the top bar.

Screen
- New chart: ranges from 30 ticks to All, $ or %, hover crosshair, holding line toggle, coin price views with
  your buys/sells and the selected bot's entries/exits. Drag the divider to make it taller.
- Scoreboard: vs holding, win rate and lifetime columns, colour-coded rows, star for Hall of Fame bots.
  Double-click a bot for its detail card with its own entry/exit chart.
- Scrolling side panel; the manual strategy list hides in Wildcard mode.
- Settings have dropdowns and checkboxes, plus new Orders, Hall of Fame and Alerts tabs.
- Keyboard shortcuts (Help > Keyboard shortcuts).
- **Updates**: an Update button appears when a new version is on GitHub, shows what's new, installs only when you
  accept, backs up the old version, and Tools > Undo last update goes back.

## 1.6.4

- Click any column heading on the Trades tab, scoreboard or other lists to sort (click again to flip).
- Best/worst bot line above the scoreboard.
- Export trades CSV can include the bots' test trades.
- Window title shows profit/loss and the countdown to the next decision.
- **Up to 50 bots, and they rotate**: Settings > Wildcard has "Most strategies competing at once" (14-50, default 30) and "Replace a strategy that hasn't been winning for this many hours" (default 12, 0 = never). Bots that lose or never trade for that long are replaced by tweaked copies of winners or random new ones. The field starts larger and keeps filling up to the limit.
- **Three new strategies**: Breakout, MACD and Mean reversion (also selectable in manual mode).
- Lists now have scrollbars.
- **More live**: default bar is now 1 minute (was 5) and prices refresh every 2 seconds (was 5). A countdown shows when the next decision happens. Wildcard loads 600 bars of history. Bot paces are now 1, 2, 3, 5, 10 or 15 bars. Existing installs switch to the new defaults once; change them anytime in Settings > Timing. A saved session you resume keeps the bar length it started with.
- **Trades tab lists every bot's test trades** (grey rows, mode BOT, with the strategy and pace), alongside your practice or live account's own trades. A checkbox hides them. They never change Total Value or Cash Left. The scoreboard column is now called "Recent trades".
- **Bots choose their own pace** (Wildcard): each bot now decides on its own timeframe (1, 2, 3, 6 or 12 x your Bar length, e.g. 5/10/15/30/60 min) and reads candles of that size. Starter bots include a slower copy of each strategy, and tweaked copies sometimes speed up or slow down, so the tournament finds which pace works. The scoreboard shows "every N min". Saved lineups and exported bots keep the pace; older files load at the base pace.
- Trade logging hardened: balances and the Trades tab update independently and an error in one no longer hides the other.
- Red error badge in the top bar and Help > Diagnostics (copies version, balances and recent logs).

## 1.6.3
- Hardened the Trades tab against malformed/legacy CSV rows and non-numeric values.
- Added a manual Refresh control and paper/live trade counts.
- Loads up to 1,000 combined trades from the durable ledger and both saved sessions.


## 1.6.2
- Trades tab now shows a combined, newest-first history from both Paper and Live sessions, including saved-session trades.

## 1.6.1
- Fix Practice/Paper dashboard balances refreshing immediately after trades and when resuming a saved session
- Preserve a position's cost-basis valuation until its first fresh market quote arrives, preventing total value from temporarily dropping on resume


## 1.6.0
- **Export / Import bots**: share selected strategies as a small file; imports are validated, skip duplicates, and compete on your own prices
- **Stays connected**: request retries, supervised price feed with automatic restart, stale-price warning and notification, keeps the computer awake while running, one bad message can no longer freeze the screen
- **Trades tab**: coins moved, the strategy behind each trade, green buys and red sells (CSV gets a "who" column; an older trades.csv is kept as trades-before-<date>.csv)
- **Dark mode by default** with an instant Light/Dark switch in the top bar
- Window opens centered and tall enough for the whole left panel; remembers its size
- "Better than holding" tile (green when ahead), profit/loss summary when you stop, title bar shows status
- Activity log saved to ~/.desk-utils/activity.log, File > Open data folder, confirm before quitting while running
- Launcher closes its command window on a normal exit (stays open only on errors)
