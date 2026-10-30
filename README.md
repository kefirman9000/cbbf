# CBBF — Bot Farm

A desktop app that grows, tests and trades a whole farm of crypto trading bots. Instead of betting on one
strategy, CBBF runs dozens of bots side by side, scores every one of them continuously, follows the ones that
are really working, and breeds new bots from the winners. Python 3.8+ with Tkinter, no other setup needed for
Practice mode.

**Windows:** download the zip (Code > Download ZIP), extract the whole folder and double-click **Run CBBF.bat**.
**Mac / Linux:** `python3 main.py`

Practice mode uses pretend money on live market prices, so you can watch the farm learn without risking anything.

---

## How the bot farm works

### 1. Every bot is its own little trader
Each bot is a strategy with its own settings, its own decision speed and (optionally) its own stop-loss and
take-profit. Every bot trades a private pretend portfolio on the same live prices, paying fees and spread, so
they can all be compared fairly. Fourteen strategy families are included:

| Family | What it looks for |
|---|---|
| Trend follower (SMA) / Quick trend (EMA) | Short-term average crossing the long-term average |
| Momentum shift (MACD) | Momentum turning up or down |
| Ride the rise (Momentum) | Coins moving fast in one direction |
| Breakout chaser | New highs or lows over a lookback window |
| Volume breakout | Breakouts backed by unusually heavy trading volume |
| Buy dips / sell peaks (RSI), Range bounce (Stochastic) | Oversold / overbought swings |
| Buy low / sell high (Bollinger), Snap-back (Mean reversion) | Price stretched far from its average |
| VWAP snap-back | Price far from the volume-weighted average price |
| ATR channel | Pushes beyond the normal bar-to-bar range (uses highs and lows) |
| Dips in an uptrend | Short dips, but only while the bigger trend points up |
| Steady buyer (DCA) | Regular fixed buys (manual mode) |

### 2. The tournament (Wildcard mode)
Up to 50 bots compete at once. After every decision each bot is rated on:
- **Recent gain** over the scoring window
- **Beating just holding**: a bot that made 1% while the coins went up 3% is *not* a winner
- **Consistency**: how many recent stretches it made money in
- **Drawdown**: how far it has fallen from its recent peak
- **Market fit**: how it has done in the current kind of market (trending up, trending down, choppy or quiet)

Bots are then sorted into groups:
- **Winning**: the account copies their trades, with bigger size for the steadiest winners
- **Shaky**: copied only with small trades and a cooldown
- **Standby**: good, but not in the top group right now
- **Stopped**: losing or falling fast, ignored

### 3. Evolution
The farm is never static:
- **Mutation**: winners get tweaked copies (settings, decision speed, own stop/target nudged a little)
- **Crossbreeding**: two winners from the same family produce a child that mixes their settings
- **Retirement**: bots that keep losing, or never win for hours, are replaced
- **Fresh blood**: brand-new random bots keep the gene pool from getting stale
- **Different speeds**: bots decide anywhere from every 15 seconds to every few hours, and the farm finds out
  which pace works for each market

A **family tree** shows every bot's ancestry: which ones were bred from which, which lines thrived and which
died out.

### 4. Hall of Fame
Bots that keep winning for hours (and beat holding over their whole life) are saved automatically, with their
full track record. The best of them come back automatically next time and have to prove themselves again on
fresh prices.

### 5. Bot Lab: test on data the bots have never seen
The Lab downloads weeks or months of history and runs the whole evolution on the older part. Then it takes the
best bots, freezes them, and replays them on the newer part they never saw. A bot only **passes** if it made money
there *and* beat holding. Running several rounds with different random seeds separates real skill from luck.
Passed bots can be exported, added to the Hall of Fame or dropped into a live run.

### 6. Share bots
Export any bots (one, a selection, the Hall of Fame or Lab passes) to a small `.json` file with their settings
and track record. Import a file and the bots join your farm, but they still have to earn their place on your prices.
Bot files are data only, are validated on import, and never contain keys or balances.

---

## Trading features
- **Practice and Live modes.** Live uses an exchange API key with *View* and *Trade* permission only (never
  transfer/withdraw), stored in your computer's keychain
- **Market or limit orders.** Limit orders sit on the order book for the lower maker fee, with a timeout and
  optional market fallback. Safety exits always use market orders
- **Risk controls**: stop-loss, take-profit, trailing stop, daily loss limit, max share in one coin,
  max trades per hour, sell-everything button
- **Realistic practice**: fees, spread and slippage are charged in Practice mode
- **Any coin**: browse every tradable coin with 24h change and volume, or let the bot scout busy, moving coins itself
- **Alerts** to Discord or Telegram for trades, Hall of Fame entries and problems
- **Backtest** strategies and the whole farm against buy & hold

## The screen
- Live equity chart vs buy & hold, from the last 30 ticks to the whole session, $ or %, with hover readouts
- Coin price charts with your account's buys and sells and the selected bot's entries and exits
- Scoreboard with recent gain, vs holding, win rate, lifetime return and status, colour-coded
- Bot detail cards with full stats and a chart of that bot's trades
- Live prices with market mood, trade history, activity log
- Dark and light themes, resizable chart, scrolling side panel, keyboard shortcuts (Help > Keyboard shortcuts)
- Connection light, auto-reconnect, keeps the computer awake while running

## Updates
The app checks this repository for new versions (at most twice a day) and shows an **Update** button when one is
available. You see what's new first and nothing installs until you press **Accept**. The current version is backed
up, and **Tools > Undo last update** puts it back. Your settings, sessions, trades, bots and login are never touched.

## Your data
Everything lives in `~/.cbbf/` (Windows: `C:\Users\<you>\.cbbf`): settings, saved sessions, `trades.csv`,
`hall-of-fame.json`, saved lineups, logs. **File > Open data folder** opens it.

## Honest caveats
- These are simple indicator strategies with no proven edge. Short-term crypto moves are mostly noise, and
  recent winners often stop winning. The beat-holding rule, the Bot Lab and the Hall of Fame exist to make
  that visible, not to make it go away.
- Fees matter a lot, especially at fast decision speeds. A round trip can cost over 1%, so every trade has to beat that.
- Always compare against buy & hold (Backtest, Bot Lab) before trusting anything with real money, and start small.
- Trading involves risk. This is not financial advice. Use at your own risk.
