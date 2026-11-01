# Changelog

## 1.20.0
- **Real costs**: after 5 LIVE market orders, CBBF compares each fill with the price when the trade was decided. If
  your orders typically lose more to the spread and price movement than the Slippage setting assumes, Practice and the
  bots switch to the real figure, so what they learn carries over to LIVE. Settings > "Learn real costs".
- **Crash drills**: the Ready for LIVE? checklist has a "Run crash drills" button. It replays the top bot on 7 of the
  worst stretches since 2020 (Covid, May 2021, LUNA, FTX, August 2024 and more), on whichever of your coins existed
  then. To pass, it must lose less than holding on average and in most of them, and never lose more than 25% in one.
  The first run downloads those stretches once.
- **Coins that move together**: the account never puts more than 50% of your money (adjustable) into a group of coins
  whose prices have been moving in step, so one bad day for BTC doesn't hit everything at once.
  Settings > Basics > "Coins that move together".

## 1.19.1
- **Start with Windows** is now also in the File menu (it was only on the icon). It runs CBBF as the icon by the clock,
  training on practice prices only. It sets up the icon add-on first if needed.
- **Even lighter in the background**: after Windows starts, CBBF waits 2 minutes before training so it never slows your
  start-up, and the start-up warm-up uses one processor core instead of several while running as the icon.

## 1.19.0
- **Update alerts on the icon**: while CBBF runs in the background it still checks for updates and shows a small note
  when one is out. Nothing installs until you open CBBF and say yes.
- **Daily recap**: once a day, a short note on how the bots did vs just holding, plus new BEASTS (on the icon and in
  the log). Settings > "Daily recap".
- **Learning from its own losses**: when one never-rule would have stopped at least 3 of a bot's losing buys and
  clearly fewer of its winners, the bot takes that rule on itself during its lifetime. It's graded afterwards like
  every other rule. Settings > Learning > "Learn from its own losses".
- **Ready for LIVE? checklist** (Account menu): plain evidence that the practice farm is ready: time spent learning,
  Winning bots, beating holding, enough trades, twin test passed, no deep slump, and the account itself ahead.
- **Weekly backup**: once a week a copy of your bots, farms, sessions and settings goes into your data folder under
  auto-backups (the last 4 are kept, no keys, price history left out to keep them small). Settings > "Weekly backup".
- Fixed: the backup message said "Alpaca" instead of Coinbase.

## 1.18.1
- When Windows signs out or shuts down, CBBF now saves the whole farm and session first, so the bots pick up
  from that exact point next time (before, up to 5 minutes of learning could be lost).

## 1.18.0
- **Background mode (icon by the clock)**: File > "Run in the background" hides CBBF to a small icon next to the
  clock. It keeps training the bots on practice prices at a lower priority (anything you're doing comes first), skips
  all drawing, and never keeps the computer awake. Click the icon to open the window; right-click for Pause/Resume,
  Go LIVE, "Start when Windows starts" and Quit. The first time, CBBF installs two small add-ons for the icon.
- **Start with Windows**: tick it on the icon's menu and CBBF starts as the icon whenever you log in, training on
  practice prices. LIVE never starts by itself: choose Go LIVE on the icon and type LIVE, once after each restart.
- While CBBF is in the background, the window's X hides it back to the icon; Quit is in the File menu and on the icon.
- Opening CBBF a second time brings up the copy that's already running instead of starting another one.

## 1.17.2
- **Luck check**: a bot's score now counts for less while it rests on only a few trades (3 trades counts about a
  third, 40 trades almost fully). This applies to which bots steer your account and which ones breed, so a few lucky
  trades can't fill the farm with copies. Settings > Learning > "Luck check".
- **Fee-aware buying**: bots can learn a new never-rule, "No penny moves": skip coins whose recent swing is too small
  to beat the round-trip fees. Like the other never-rules, each skipped buy is graded afterwards, so it only spreads if
  it actually saves money. Part of the Fee-aware setting.

## 1.17.1
- **Snappier window**: background work (Free roam, warm-up, history loading) no longer makes the window stutter.
  The longest freezes while Free roam runs dropped from about 1.5 seconds to under 0.2.
- **Reset can keep bots**: choose to reset the account, the farm, or both, and keep the BEASTS, Rising, Eggs or
  Hall of Fame bots - they rejoin the next farm when you press Start.
- **Saved farms**: Reset saves a copy of the whole farm first (on by default), and "Bring back a saved farm..."
  restores one later.
- Fixed: starting the farm a second time could get stuck while warming up islands on several processor cores.

## 1.17.0
**Three new things the bots can learn**

- **Time habits**: bots can grow "quiet hours" (never buys during a 6-hour stretch of the day, shown in your own time)
  and "no weekend buying". Like every never-rule, each trade they skip is graded afterwards, so useful habits spread
  to children and costly ones fade out.
- **Sell in parts**: bots can evolve "sell half at +X% and let the rest run" (2% to 40%), keeping the second half on
  their stops and trailing stop. Children inherit it, and it competes like any other gene.
- **Crowd confidence**: your account trades bigger when several *different* strategies agree on a buy (x0.85 for one,
  up to x1.3 for four or more) and smaller when the farm is split. Each trade's "why" says how many agreed and the
  size it used.
- All three can be switched off in Settings > Learning.

## 1.16.1
**Free roam checks history first, test on any coin, and a smoother app**

- **Free roam only studies history that exists**: before picking a stretch it checks each coin's daily history on
  Coinbase, so it never tries stretches before a coin was listed (HYPE before 2025) or while it was suspended (XRP
  from early 2021 to mid 2023). No more "Not enough history" loops.
- **Backtest and Training camp say exactly what's there**: "HYPE: Jan 2025 to now. Pick a stretch inside that, or
  untick HYPE." A stretch that crosses a hole in a coin's history is trimmed to its longest unbroken part.
- **Test on any coin**: Backtest and Training camp have a coins box (SOL, ADA, AVAX, LINK...), up to 8, separate from
  the coins your farm trades.
- **Smoother**: the chart redraws at most about once a second while prices tick (clicks still redraw at once), and
  bots on the same pace share one prepared candle summary. In a test with about 50 bots, each price update now takes
  57 ms instead of 133 ms.
- Diagnostics no longer copies itself to the clipboard when opened.

## 1.16.0
**Everything new from the options farm, made for crypto**

- **Watched coins (market context)**: coins the bots watch but don't trade (BTC, ETH and SOL by default). Bots can
  evolve up to 3 rules on them ("BTC dropping fast votes against buying"), each graded by what the trades it held back
  would have done. Card on the left, and Insights > Market context shows what they learned.
- **Economic calendar**: CPI, jobs reports, Fed decisions and more, from FRED (free key). Bots can grow news-day
  never-rules ("quiet before big news", "quiet before the Fed", "let the news settle"), graded everywhere they learn.
- **History back to 2015**: Backtest and Training camp take any stretch, from 1 day to "Since 2015", most recent or
  starting on a date you pick (try 2020-03-01, 2022-05-01 or 2022-11-01). Coinbase history downloads once, stored by
  year, and downloads pace themselves under Coinbase's limits.
- **Backtest screen, cleaned up**: result tiles, a verdict, a fixed-size chart with dates, shaded walk-forward parts,
  news days marked, a hover readout that doesn't move anything, and the details in their own scrolling box.
- **Free roam you can watch**: it roams anywhere back to 2015, with an IN SESSION banner, a live trades list, problems
  shown on screen, and results sorted best to worst with a "what the results do" explanation. It only studies while
  it's switched on, stops at once when switched off, and always starts off. Nothing else learns in the background
  either (the nightly Hall of Fame retest is off unless you turn it on).
- **Safety**: a sell never asks for more of a coin than Coinbase shows as available, so coins you hold yourself are
  never touched. A thin-market filter skips LIVE buys when a coin's bid/ask gap is too wide (default 1%).
- **Security**: Telegram, Discord and FRED keys move to the system keychain, logs blank out anything that looks like a
  key, the data folder is private, backups never contain keys, restoring a backup only restores CBBF's data files, the
  Telegram remote only obeys fresh messages from your own private chat. Tools > System > Security shows it all.
- **Streamlined**: one Learning window (Backtest, Hall of Fame test, Training camp, Free roam; Bot Lab is retired),
  Insights, System (security, disk use, diagnostics), Family (tree and bloodlines) and Help windows with tabs; one
  Reset window; five main tabs instead of eight (Markets = watchlist + live prices, Trades = trades + what the bot did +
  scoreboard); shorter menus; Settings open on an "Everyday" page with the rest under "Show advanced settings".
- **Disk use** (Tools > System): what CBBF stores and has written; routine saves are spaced out.

## 1.15.0
**The Slow farm, and smooth window resizing**

- **Slow farm** (new tab next to Bot Farm): a second, separate farm of slow dragons that decide on hourly candles,
  from every hour up to every two and a half days. They hold for days to weeks and can evolve much wider targets
  and stops (targets up to 100%, stops up to 30%), so fees matter far less. They learn from about four months of
  hourly history first (on several cores), then carry on with each new hourly candle, and they keep going whether
  or not the fast bot is running.
  - Its own pretend wallet, its own scoring window (3 days) and its own evolution: it never mixes with the fast farm.
  - **Train it on the past**: two more passes through its history, a final exam on the latest weeks, graduates join it.
  - Save its bots to the Library or the Hall of Fame. The list shows how long each one typically holds.
  - **Let it trade my account too** (off by default): when its winners agree on a new hourly candle, your account follows.
  - It remembers everything between runs and catches up on the hours it missed while CBBF was closed.
- **Smooth resizing**: dragging or resizing the window no longer redraws the farm, charts and tables over and over;
  they wait until you let go and draw once. A burst of 60 resize events now causes 2 farm redraws.
- The price history cache keeps up to 6,000 candles per coin and candle size.

## 1.14.0
**Training camp, Free roam, and a snappier app**

- **Training camp** (Train button on the farm, or Tools): run a whole farm through 1 day to 180 days of history
  (6,000 candles at most), 1 to 5 times over. Start from fresh eggs, your current farm, the Hall of Fame or the
  Library. The most recent part is held back as a final exam on prices the bots never saw. Bring the graduates to your
  farm, teach your farm what the camp learned, or save them to the Library.
- **Free roam mode** (Free roam button): switch it on and the farm goes back in time by itself, choosing its own
  candle sizes (5 min to 6 hours) and the stretches of the past it has studied least. Each study either lets a few of
  its bots test themselves there, or sends a small practice farm through it. Every lesson is saved, and the best
  practice-farm graduates join your farm. The Free roam screen shows the stretch being studied right now on a
  timeline of the past, how much of the past has been covered, what the farm is learning, and a log of every study.
  With the mode off, it can still study quietly while the farm is idle (Settings > Learning > Free roam).
- **Snappier**:
  - The farm only draws the cards you can see, and draws the rest as you scroll. With 120 bots the farm redraws in
    under a third of the time it used to take with 60.
  - The scoreboard table only fills in its details when you open it.
  - The session saves at most every few seconds while running (trades still save at once).
  - The farm buttons are now on two rows, so nothing gets cut off.
- Promotion animations stay inside the dragon's own card.
- New header graphic and screenshots on the project page.

## 1.13.1
- Fixed: "KeyError" every minute (for example 'XRP') after the bot picked its own coins and you then continued a
  saved session. The saved session now keeps its own coins (scouting only happens for a brand-new session), and if
  the farm and the session ever disagree about coins again, the farm quietly lines itself up instead of erroring.

## 1.13.0
**A Learning page, real backtesting with memory, islands, and safety for real money**

Settings > Learning (new)
- One organised page for everything the bots can learn from, in four groups (how bots are judged, breeding and
  evolution, rules a bot can grow, memory), each with an on/off switch, plus All on / All off / Recommended.

Backtesting
- **Backtest any bot** (right-click a dragon, or Tools > Backtest a bot): pick a farm or Library bot, 1 day to 30 days
  of history and the candle size. You get return vs just holding, worst drop, win rate, fees, a **walk-forward** check
  (did it beat holding in every quarter of the history, or just one lucky stretch), a **bad-luck stress test** (the
  same trades in random order), a **double-fees test**, and results per coin, per market mood and per rule.
- **Save to its memory**: the bot keeps what the test learned (how each study and never-rule did, its weak coins, how
  it does in each market mood, and the result) and its children inherit it. **Save + teach the farm** also feeds the
  post-trade review and makes a winning bot an elder.
- **Backtest the Hall of Fame**: every champion tested and ranked at once, with "Save all to their memories".

Bots learn more
- **Bigger-trend studies**: a slower timeframe (3 to 12 times the bot's own) must agree before buying.
- **Bitcoin leads**: BTC's recent move votes for or against buying other coins.
- **Islands**: the farm is split into sub-farms that breed separately and swap a champion now and then (more variety).
- **Post-trade review**: losing trades note which never-rule would have stopped them; new rules favour those.
- **Adaptive stops**: a trait that widens stops in wild markets and tightens them in calm ones.
- **Mutation memory**: the farm does more of the kinds of change that have produced winners.
- **Multi-core warm-up**: islands learn on several processor cores at once at the start.

Trust before real money
- **Shadow Live**: a pretend account that follows only your Hall of Fame bots in real time ("Hall of Fame shadow"
  chart, and on the farm pulse).
- **Nightly retest**: once a day the Hall of Fame is re-backtested on the latest prices; stale ones get a RUSTY badge.
- **Graduation**: before LIVE with your Hall of Fame or team, each bot is checked (hours winning, profit, ahead of
  holding, trades, a passed twin test or backtest, not rusty). Go with only the graduates, all, or cancel.
- **Kill switch** (red button at the top): stop, cancel waiting orders, sell everything, lock LIVE until you type UNLOCK.
- Declining LIVE now always keeps you in Practice, and CBBF always opens in Practice.

Look and feel
- **Farm pulse** strip: births and retirements this hour, the season, the best bot of the hour, the shadow result.
- **Badges** (First 10%, Beat the market, Crash survivor, Sharpshooter, Marathon, Dynasty, Twin-proven, Backtested,
  Powerhouse) on cards and the bot card.
- **Bloodlines**: a drawn family tree with portraits; strong lines glow gold.
- **Compare two bots** side by side (differences highlighted).
- **Farm replay**: play back the farm's eggs, rising and BEASTS over time, with its big moments.

Convenience
- **See / edit a bot's rules**: right-click a dragon (or its card) to see every learned rule with its evidence, change
  anything (strategy numbers, studies, bigger-trend and Bitcoin studies, never-rules, stops, habits) and hatch the edit
  as its child or replace it. Its learned study settings are kept unless you change them.
- **Export** button right on the farm (whole farm, a den, the Hall of Fame, the Library or your selection).
- **Reset** button under Start: reset the practice account, the farm, or both (Library and Hall of Fame are kept).
- **Phone remote** (Telegram): /status, /pause, /resume, /report, /kill confirm.
- **Trading hours** (and weekdays only) for the account; the bots keep learning outside them.
- **Settings profiles** (File menu): save and switch whole setups.
- **Back up everything / Restore** (File menu): one zip with your Library, Hall of Fame, farms and settings.

Snappier
- Animations and chart redraws pause while you drag or resize the window, and redraw once when you stop.
- Resizing redraws once instead of on every pixel; bot identities are cached; the Trades list only rebuilds when
  you're looking at it.

## 1.12.0
**Smarter breeding, safer live trading, a fresher farm**

Bots learn more
- **Seasons:** the farm keeps a champion for each market mood (trending up, down, choppy, quiet). When the mood
  changes and holds, that season's champion is woken up again from its genes if it had left the farm.
- **Elders teach:** good bots that leave the farm are remembered; new eggs of the same family are sometimes nudged
  toward their settings instead of mutating purely at random.
- **Twin test:** each winner's exact twin is run on an older stretch of prices it was never scored on. If the twin
  loses to just holding, the winner was probably lucky and counts for less. Shown on the bot's card.
- **Favourite coins:** bots learn which coins they keep losing on and skip them. Every skip is graded in hindsight,
  and bad records fade, so a bot re-tries a coin one day.
- **Confidence sizing:** when a bot's studies clearly agree it buys bigger; on a close vote, smaller.

Live safety
- **Allowance per bot** (Settings > Basics): the most of your starting money one bot's votes may have tied up.
- **Why did it trade?** Every account trade records which bots voted and what each one saw. Double-click a trade on
  the Trades tab, and LIVE trades pop up a short note (can be switched off).
- **Daily loss limit meter** on the TODAY tile.
- **Weekly report** of what the farm learned and how the account did, saved to the data folder and sent to
  Discord / Telegram if set up (Tools > Weekly report now).

Look and feel
- Den colours: green eggs, blue Rising, orange BEASTS, gold Hall of Fame, with matching glows and tabs.
- Promotion moments: a hatch burst when an egg becomes Rising, a fiery ROAR when a bot becomes a BEAST.
- The bot card shows its **family**: parents and children as little clickable portraits.
- **Search** the farm by name, strategy, trait, never-rule or study.
- **Drag a card** onto the Hall of Fame or Library tab to promote or save it.
- **Undo** after releasing bots or removing them from the Hall of Fame.
- **Farm presets:** Calm, Balanced or Wild in one click.
- **Compact top bar** (File menu) for more farm room, and a short **tour** for new users (Help > Take the tour).

Faster
- **Background mode:** while minimized, screens skip their redraws and catch up when you come back.
- **History cache:** price history is kept on disk, so restarts only download what's new.
- Dark-mode number boxes (like Farm size) are readable again.

## 1.11.0
**Studies: several reasons to buy, several reasons not to**
- Each bot can now weigh **several positive studies** (its main strategy plus up to 4 more strategies from any
  family) and **several negative studies** (up to 3). Every study's most recent signal is its vote. The bot buys
  when enough positive studies say buy, minus one for each negative study currently saying sell. "Enough" is its own
  evolvable number (for example "2 of 3"). For example: Breakout + MACD + RSI as positives, needing 2 of 3, with
  Supertrend and Momentum as negatives.
- **Exit studies** (up to 3): the position is sold when any of them says sell, instead of the main strategy.
- **Sizing**: trades can be sized to how wild the market is.
- **Everything is graded**: buys held back by too little agreement and buys blocked by negative studies are
  followed to see whether holding back saved or cost money, and that evidence decides whether the studies are passed
  on. Studies are pooled from both parents when breeding, then kept, tweaked, swapped, dropped or newly added.
- Each bot's card shows its full **rule stack** in five layers (market filter, positive studies, negative studies
  and never-rules, sizing, exits); farm cards show its study counts; exports describe them; **Design an egg** lets you
  pick several positive, negative and exit studies and how many must agree.

## 1.10.0
**Never-rules, health and power**
- Bots can now evolve **never-rules**: things they refuse to do. "No falling knives" (don't buy after a big drop),
  "no spike buying", "no chasing" (don't buy far above the average), "no revenge trades" (don't jump back into a
  coin right after losing on it), "no thin markets", "no buying into a sell-off" (BTC falling) and "no panic
  selling". Up to 3 per bot.
- **Every rule is graded by what would have happened**: when a rule blocks a trade, the farm follows that skipped
  trade for a while and records whether skipping it saved money or cost money (fees included). Each rule builds a
  score like "saved 4.2% over 12 vetoes".
- **Good rules spread, bad rules die out**: children very likely keep their parents' proven rules, usually drop
  rules that keep blocking winners, and inherit half of the evidence. New rules are tried now and then.
- **Health** (HP bar) now reflects how the bot is doing right now plus how well its rules are working. A bot whose
  health runs out rests.
- **Power** (new PWR bar, 0 to 10) grows slowly, and only from real money made on fresh prices while beating just
  holding, never from rules merely firing. Power raises a bot's trade-size ceiling, its say in the vote and its
  chance to breed. It fades if the bot slips.
- Never-rules and their scores show on each bot's detail card and in exported bot files (in plain English), and you
  can pick never-rules in **Design an egg**. Settings > Wildcard switches the whole feature off.

## 1.9.6
- **Picks up right where it left off.** The whole farm is now saved (every bot, its money and positions, its history,
  levels, traits, eggs and family tree), not just your account. When you open CBBF, your last session and its farm
  are back on screen straight away, paused; press Continue to carry on with the same bots. If it was running when
  you closed it, it carries on by itself (Live still asks first). The farm is saved every 20 decisions and whenever
  you stop, switch mode or close. "Start a new session" starts a fresh farm.
- **More memory**: twice as many trades, four times as many bot practice trades and a much longer account chart are
  kept between sessions.

## 1.9.5
- No more "Resume session?" pop-up. When a saved session exists, the main button says **Continue practice session**
  (or Continue LIVE session) with its cash and trade count, and a **Start a new ... session** button appears under it.

## 1.9.4
- **Famous strategies join the farm** (Wildcard): golden cross (50/200), Turtle breakout, Wilder's RSI 30/70,
  Connors RSI-2, MACD 12/26/9, Bollinger 20/2, the 9/21 EMA, a Fibonacci ribbon, Supertrend 10/3, Ichimoku 9/26,
  Stochastic 14, "buy the 5% dip" and time-series momentum, plus three controversial ones marked \u26a0: averaging
  down (martingale-style), pump chasing and "diamond hands". A few start on the farm, and about a quarter of new
  eggs are a famous strategy that isn't there yet, mixed in with the random ones. They hatch from **silver eggs**
  (\u272a), show "Classic: ..." on their cards and their story on their detail card, compete like everyone else,
  and can be bred. Settings > Wildcard switches them off or changes how often they appear.
- Moving-average and RSI settings can now go further (averages up to 200 bars, RSI down to 2) so the classics fit.

## 1.9.3
- Live mode is now **blue** (Coinbase blue) and Practice stays **green**: switch, stripe, banner, border and Start
  button follow the mode. Red is only used for Stop.

## 1.9.2
- **Practice and Live are now clearly separate parts of the app.**
  - A big **PRACTICE / LIVE** switch at the top left replaces the small radio buttons.
  - **Practice** is green throughout: green switch, green stripe and banner, "Start practice bot".
  - **Live** is red throughout: a red border around the whole app, a red stripe, a dark-red "LIVE" banner, a red
    "Start LIVE bot (real money)" button, and "CBBF LIVE (REAL MONEY)" in the window title.
  - Each mode keeps its own session. Switching saves and closes the current one and clears the screen, so Practice
    numbers never show in Live or the other way round. The other mode's saved session is waiting: press Start to
    resume it. Switching while the bot runs asks first.

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
