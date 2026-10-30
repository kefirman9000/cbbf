# Changelog

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
