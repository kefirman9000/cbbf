"""CBBF (Coinbase Bot Farm) - trading bot GUI.

Paper mode (default) simulates trades on live public prices, no keys needed.
Live mode places real market orders via the Coinbase Advanced Trade API.
"""

import sys


def _stay_open(msg):
    """Print a problem and keep the window open so it can be read (Windows closes it otherwise)."""
    print("\n" + "=" * 60 + "\nCBBF could not start\n" + "=" * 60 + "\n" + msg + "\n")
    try:
        input("Press Enter to close this window...")
    except Exception:
        pass
    sys.exit(1)


if sys.version_info < (3, 8):
    _stay_open("CBBF needs Python 3.8 or newer. You have " + sys.version.split()[0] +
               ".\nGet the latest from https://www.python.org/downloads/ (tick 'Add Python to PATH').")
try:
    import tkinter  # noqa: F401
except ImportError:
    _stay_open("Your Python has no Tkinter (the window toolkit).\nRe-run the Python installer, choose Modify, "
               "and make sure 'tcl/tk and IDLE' is ticked.")
try:
    import engine  # noqa: F401
except ImportError as _e:
    _stay_open(f"Could not load engine.py ({_e}).\nExtract the whole zip first (right-click > Extract All), "
               "then run main.py from the extracted folder. main.py and engine.py must sit side by side.")

import csv
import io
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
import uuid
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from tkinter import filedialog, messagebox, simpledialog, ttk

import engine as E

# ---- Update settings: change to your GitHub "user/repo" after publishing ----
UPDATE_REPO = "kefirman9000/cbbf"
UPDATE_BRANCH = "main"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(E.HOME_DIR, "config.json")

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT"]
MIN_LIVE_ORDER = 2.0   # USD; smaller live buys are skipped
MAX_FAILS = 3          # consecutive order failures before auto-stop
BAR_CHOICES = E.BAR_CHOICES
BAR_LABELS = {b: E.fmt_bar(b) for b in BAR_CHOICES}
LAB_CANDLES = {"1 min": 60, "5 min": 300, "15 min": 900, "1 hr": 3600, "6 hr": 21600}
BG, CARD, TEXT, MUTED = "#f3f4f6", "#ffffff", "#111827", "#6b7280"
ACCENT, GREEN, RED, LINE = "#2563eb", "#16a34a", "#dc2626", "#e5e7eb"
LIGHT_P = dict(BG="#f3f4f6", CARD="#ffffff", TEXT="#111827", MUTED="#6b7280",
               ACCENT="#2563eb", GREEN="#16a34a", RED="#dc2626", LINE="#e5e7eb")
DARK_P = dict(BG="#111827", CARD="#1f2937", TEXT="#f3f4f6", MUTED="#9ca3af",
              ACCENT="#3b82f6", GREEN="#22c55e", RED="#ef4444", LINE="#374151")
EXTRA_TO_DARK = {"#eff6ff": "#1e3a5f", "#1e3a8a": "#bfdbfe", "#b45309": "#fbbf24"}  # headline bar, warning text
DARK = True
LIVE_BLUE = "#0052ff"  # Live mode colour (Coinbase blue); Practice mode is green


def apply_theme(root, dark):
    """Sets the colour palette (module-level colours used by every widget built or restyled afterwards)."""
    global BG, CARD, TEXT, MUTED, ACCENT, GREEN, RED, LINE, DARK
    DARK = dark
    p = DARK_P if dark else LIGHT_P
    BG, CARD, TEXT, MUTED = p["BG"], p["CARD"], p["TEXT"], p["MUTED"]
    ACCENT, GREEN, RED, LINE = p["ACCENT"], p["GREEN"], p["RED"], p["LINE"]
    root.configure(bg=BG)
    for opt, val in (("*Toplevel.background", BG), ("*Text.background", CARD), ("*Text.foreground", TEXT),
                     ("*Text.insertBackground", TEXT), ("*TCombobox*Listbox.background", CARD),
                     ("*TCombobox*Listbox.foreground", TEXT)):
        root.option_add(opt, val)


def recolor(widget, mapping):
    """Walks every widget and swaps old palette colours for the new ones."""
    for opt in ("bg", "fg", "background", "foreground", "highlightbackground", "highlightcolor",
                "insertbackground", "activebackground", "activeforeground", "selectbackground"):
        try:
            new = mapping.get(str(widget.cget(opt)).lower())
            if new:
                widget.configure(**{opt: new})
        except Exception:
            pass
    for ch in widget.winfo_children():
        recolor(ch, mapping)
SETTING_GROUPS = [
    ("Basics", "Your account's money and safety rules, used for the trades your account makes (the bots' practice "
               "trades follow their own rules). All % are of the money you started this session with. Not sure? Pick "
               "a Risk style on the main screen (Cautious, Balanced, Adventurous): it fills in the first five for you. "
               "In Wildcard, 'Let the winning bots set stops' (Orders tab) lets the winners' learned Safety stop and "
               "Lock in profit take over; these numbers are then the fallback. Fees are always counted.", [
        ("trade_pct", "Trade size: each buy spends this % of your starting money. Example: 10 with $1,000 = $100 per buy "
                      "(winning bots can scale this up to their trade-size multiplier)", float),
        ("max_coin_pct", "Limit per coin: never have more than this % of your starting money in any single coin. "
                         "Example: 50 with $1,000 = at most $500 in BTC", float),
        ("stop_loss", "Safety stop: automatically sell a coin if it falls this % below the price you paid. "
                      "Example: 5 = sell if a $100 position drops to $95 (0 = off)", float),
        ("take_profit", "Lock in profit: automatically sell a coin once it is up this % on what you paid. "
                        "Example: 10 = sell when a $100 position reaches $110 (0 = off)", float),
        ("trail_stop", "Trailing stop: sell a coin if it falls this % from its highest price since you bought it. "
                       "Example: 4 = a coin that rose to $120 is sold at $115.20 (0 = off)", float),
        ("min_hold_min", "Minimum hold: keep a coin at least this many minutes before a strategy may sell it. "
                         "Safety stops still sell at any time (0 = off)", float),
        ("daily_loss", "Daily loss limit: stop trading for the rest of the day if the account falls this % since "
                       "midnight. Open coins are kept, not sold (0 = off). The TODAY tile shows how close you are", float),
        ("bot_allowance", "Allowance per bot: the most of your starting money that one bot's buy votes may have tied up "
                          "at once. Stops one bad dragon draining the account. Example: 25 with $1,000 = $250 per bot (0 = no limit)", float),
    ]),
    ("Wildcard", "How the bot treats strategies that are winning, shaky or failing.", [
        ("winner_boost", "Biggest trade size a top winner can earn (1 = normal, 2.5 = 2.5 times)", float),
        ("add_every", "While a winner is holding a coin, add more every N decisions", int),
        ("slow_every", "Shaky strategies may only trade every N decisions", int),
        ("fail_drop", "Stop a strategy at once if it falls this % from its peak (0 = off)", float),
        ("evolve_every", "Review and replace strategies every N decisions", int),
        ("max_bots", "Farm size: most bots competing at once (14 to 200)", int),
        ("retire_hours", "Replace a strategy that hasn't been winning for this many hours (0 = never)", float),
        ("auto_coins", "Let the bot pick this many coins when Wildcard starts (0 = use the ticked coins)", int),
    ]),
    ("Learning", "What the bots are allowed to learn from. Everything is on by default; untick anything you'd rather "
                 "they didn't use. Changes apply to new eggs right away, and to the whole farm as it breeds.", [
        (None, "How bots are judged", "head"),
        ("beat_hold", "Must beat just holding the coins to count as Winning", bool),
        ("risk_adjusted", "Rank by steady gains (gain per unit of downside), not the biggest gain", bool),
        ("prove_live", "Must also win on fresh prices it hadn't seen (guards against luck)", bool),
        ("twin_test", "Twin test: a copy re-run on older prices; lucky winners count for less", bool),
        ("regime_aware", "Favour bots that do well in today's market mood", bool),
        (None, "Breeding and evolution", "head"),
        ("crossbreed", "Crossbreeding: children can mix two winners' genes", bool),
        ("islands", "Islands: separate sub-farms that only swap a champion now and then (1 = one big farm, up to 6)", int),
        ("elders", "Elders teach: good bots that leave nudge future eggs of their family", bool),
        ("mut_memory", "Mutation memory: do more of the kinds of change that have made winners", bool),
        ("classics", "Famous strategies hatch from silver eggs (golden cross, turtle, RSI-2...)", bool),
        ("classic_rate", "...% of new eggs that are a famous strategy", float),
        ("long_holds", "Slow, longer-holding bots from the start", bool),
        (None, "Rules a bot can grow", "head"),
        ("layers", "Studies: several reasons to buy, several reasons not to, exit studies and sizing", bool),
        ("htf_studies", "Bigger-trend study: a slower timeframe must agree before buying", bool),
        ("lead_study", "Bitcoin leads: BTC's recent move votes on other coins", bool),
        ("vetoes", "Never-rules: things a bot refuses to do, each graded in hindsight", bool),
        ("adapt_exits", "Adaptive stops: wider in wild markets, tighter in calm ones", bool),
        ("confidence_sizing", "Confidence sizing: bigger when its studies clearly agree", bool),
        (None, "Memory", "head"),
        ("seasons", "Seasons: a champion per market mood, woken when that mood returns", bool),
        ("coin_prefs", "Favourite coins: skip coins it keeps losing on (re-checked in hindsight)", bool),
        ("post_review", "Post-trade review: losing trades teach which never-rules to try next", bool),
        ("multicore", "Learn on several processor cores at once at the start (faster warm-up)", bool),
        (None, "Free roam (self-study)", "head"),
        ("free_roam", "Free roam: while idle, the farm backtests its own bots on random stretches of history and runs "
                      "small training camps, saving every lesson", bool),
        ("free_roam_running", "...also while the bot is running (uses a bit more computer power)", bool),
    ]),
    ("Library & Hall of Fame", "The Bot Library keeps every bot you save, plus long-term winners the farm saves for you. "
                               "The Hall of Fame holds only the bots you promote yourself (right-click a bot).", [
        ("hof_hours", "Hours a strategy must spend Winning (in total) to count as a long-term winner", float),
        ("hof_min_trades", "...and at least this many finished (sold) trades", int),
        ("auto_catch", "Save long-term winners to the Bot Library automatically", bool),
        ("auto_hof", "Also put long-term winners straight into the Hall of Fame (off = you choose who goes in)", bool),
        ("auto_resume", "Pick up where you left off: if the bot was running when CBBF closed, resume that session on the "
                        "next start (Live asks first)", bool),
        ("hof_load", "When the whole farm trades, bring the 10 best Hall of Fame bots back to compete", bool),
        ("team_locked", "Trading with my team: keep exactly those bots (untick to let the team breed new bots)", bool),
        ("shadow_live", "Shadow Live: follow your Hall of Fame bots with pretend money in real time (see the "
                        "'Hall of Fame shadow' chart)", bool),
        ("retest_nightly", "Nightly retest: once a day, re-backtest the Hall of Fame on the latest prices; ones that "
                           "stopped working get a RUSTY badge", bool),
        ("graduation", "Graduation: before real money, check each Hall of Fame / team bot has earned it", bool),
        ("grad_hours", "...hours a bot must have spent Winning to graduate", float),
    ]),
    ("Orders", "How orders are placed, and the extra costs Practice mode pretends to pay.", [
        ("order_type", "Order type", "order"),
        ("limit_wait", "Limit orders: seconds to wait for a fill", int),
        ("limit_fallback", "Limit orders: if nothing fills in time, use a market order instead", bool),
        ("max_trades_hour", "Brake: at most this many strategy trades per hour (0 = no limit). Safety stops always run", int),
        ("trade_hours", "Trading hours: the account only makes strategy trades in these hours, like 9-17 or 22-6 "
                        "(empty = any time). The bots keep learning and safety stops always run", str),
        ("weekdays_only", "...and only Monday to Friday", bool),
        ("fee_aware", "Fee-aware: bots and the account don't sell for a profit too small to cover the round-trip fees "
                      "(losses, stops and Sell everything still sell)", bool),
        ("bot_stops", "Wildcard: let the winning bots set the Safety stop and Lock-in-profit levels they learned "
                      "(your Basics numbers are used when they don't)", bool),
        ("fee_pct", "Market order fee used in Practice mode (%)", float),
        ("maker_fee_pct", "Limit order (maker) fee used in Practice mode (%)", float),
        ("slippage", "Spread and slippage on market orders in Practice mode (%)", float),
    ]),
    ("Timing", "How often the bot looks at prices.", [
        ("bar_minutes", "How often strategies decide (15 sec is the fastest)", "bar"),
        ("refresh", "Check prices every N seconds (1 or more)", int),
        ("animations", "Animations (Light uses less computer power)", "anim"),
        ("egg_colors", "Egg colours: by dragon type, or all purple (royal eggs from your own bots are always gold, "
                       "famous strategies silver)", "eggs"),
        ("bg_mode", "Background mode: while CBBF is minimized, skip all drawing and check watchlist prices less often", bool),
        ("characters", "Dragons: themed by strategy (fire, ice, lightning, space; resting bots are skeletons) or classic (coloured by status)", "chars"),
    ]),
    ("Alerts", "Get a message on your phone when something happens. Leave blank to turn off.", [
        ("alert_discord", "Discord webhook link (Server settings > Integrations > Webhooks)", str),
        ("alert_tg_token", "Telegram bot token (from @BotFather)", str),
        ("alert_tg_chat", "Telegram chat ID", str),
        ("alert_trades", "Alert on every trade the account makes", bool),
        ("alert_hof", "Alert when a bot joins the Hall of Fame", bool),
        ("alert_problems", "Alert on problems (daily limit hit, order failures, prices down)", bool),
        ("why_popup", "LIVE: show a small 'why did it trade?' note for every real trade (also on the Trades tab: double-click a trade)", bool),
        ("weekly_report", "Weekly report: once a week, write what the farm learned and how the account did (sent here too if set up)", bool),
        ("tg_remote", "Phone remote (Telegram): send /status, /pause, /resume, /report or /kill to your bot from the chat "
                      "above. Only that chat is obeyed", bool),
    ]),
]
SETTING_FIELDS = [f for _, _, fields in SETTING_GROUPS for f in fields if f[2] != "head"]


def parse_hours(txt):
    """'9-17' -> (9, 17); '22-6' wraps past midnight. None if it isn't valid."""
    try:
        a, b = (int(x) for x in str(txt).replace(" ", "").split("-"))
        return (a, b) if 0 <= a <= 24 and 0 <= b <= 24 and a != b else None
    except (TypeError, ValueError):
        return None


def in_hours(txt, weekdays_only=False, now=None):
    """True if now is inside the trading hours (empty = always)."""
    now = now or datetime.now()
    if weekdays_only and now.weekday() >= 5:
        return False
    h = parse_hours(txt) if txt else None
    if not h:
        return True
    a, b = h
    return a <= now.hour < b if a < b else (now.hour >= a or now.hour < b)
STATUS_TEXT = {"active": "Winning (following)", "slowed": "Shaky (slowed)", "standby": "Good (on standby)",
               "benched": "Stopped", "warming up": "Learning..."}
STATUS_TAG = {"active": "win", "slowed": "shaky", "standby": "standby", "benched": "stopped", "warming up": "learning"}
ORDER_LABELS = {"market": "Market (fills now, full fee)", "limit": "Limit / maker (lower fee, may not fill)"}
STRAT_LABELS = {
    "SMA crossover": "Trend follower", "RSI": "Buy dips / sell peaks", "Momentum": "Ride the rise",
    "Bollinger bands": "Buy low / sell high", "Dollar-cost averaging": "Steady buyer (DCA)",
    "Breakout": "Breakout chaser", "MACD": "Momentum shift", "Mean reversion": "Snap-back",
    "EMA crossover": "Quick trend (EMA)", "Stochastic": "Range bounce", "Trend + dip": "Dips in an uptrend",
    "Supertrend": "Supertrend", "Ichimoku cross": "Cloud cross (Ichimoku)", "Rate of change": "Speed shift (ROC)",
    "MA ribbon": "Trend ribbon",
    "Volume breakout": "Volume breakout", "VWAP snap-back": "VWAP snap-back", "ATR channel": "Range channel (ATR)",
}
STRAT_TIPS = {
    "SMA crossover": "Buys when the short-term price trend crosses above the long-term trend, sells when it crosses below.",
    "RSI": "Buys after a coin has dropped a lot (looks oversold), sells after it has jumped a lot (looks overbought).",
    "Momentum": "Buys coins that are rising quickly and sells coins that are falling quickly.",
    "Bollinger bands": "Buys when the price is unusually low compared to recent prices, sells when unusually high.",
    "Dollar-cost averaging": "Buys a small fixed amount on a regular schedule. Never sells by itself.",
    "Breakout": "Buys when the price breaks above its recent high, sells when it breaks below its recent low.",
    "MACD": "Buys when short-term momentum turns up through its smoothed line, sells when it turns down.",
    "Mean reversion": "Buys when the price is well below its recent average, sells when well above, expecting a snap back.",
    "EMA crossover": "A faster trend follower: reacts to short-term turns quickly. Ignores tiny crossings that "
                     "wouldn't cover fees. Suited to short decision speeds.",
    "Stochastic": "Buys when the price climbs back up out of the bottom of its recent range, sells when it falls "
                  "back from the top.",
    "Trend + dip": "Only buys short dips while the bigger trend is up, and gets out when the uptrend breaks.",
    "Volume breakout": "Buys a breakout above the recent high only when trading volume is well above normal, "
                       "sells when the price breaks below the recent low.",
    "VWAP snap-back": "Buys when the price is well below its volume-weighted average price, sells when well above.",
    "Supertrend": "Rides a trailing band a few typical bar-ranges away from the price; buys when the trend flips up, sells "
                  "when it flips down.",
    "Ichimoku cross": "Buys when the short-term midpoint of highs and lows crosses above the long-term one (with the price "
                      "above it), sells on the opposite cross.",
    "Rate of change": "Watches how fast the price is moving; buys when the speed turns up while rising, sells when it turns "
                      "down while falling.",
    "MA ribbon": "Buys when a fast, middle and slow average line up in order (a healthy uptrend), sells when the fast one "
                 "drops below the middle one.",
    "ATR channel": "Buys a push above the average plus a few typical bar ranges (from highs and lows), sells a drop "
                   "below it.",
}
PARAM_NAMES = {"fast": "short average", "slow": "long average", "period": "lookback", "lo": "buy below",
               "hi": "sell above", "n": "lookback", "thr": "move needed %", "k": "band width", "every": "buy every N decisions",
               "signal": "signal smoothing", "min_gap": "smallest gap %", "trend": "trend lookback",
               "vmult": "volume x normal", "m": "range multiple", "mid": "middle average", "smooth": "smoothing"}
REASON_TEXT = {"bot-test": "bot's own test trade", "signal": "strategy signal", "stop-loss": "safety stop", "take-profit": "locked in profit",
               "manual": "you sold by hand", "add-on": "adding to a winning strategy's position",
               "trailing-stop": "trailing stop (fell from its high)", "manual buy": "you bought by hand"}
MOOD_TEXT = {"trending up": "Trending up", "trending down": "Trending down", "choppy": "Choppy (back and forth)",
             "quiet": "Quiet", "unknown": "-"}


def pct_txt(x, digits=1):
    return "-" if x is None else f"{x * 100:+.{digits}f}%"


class ScrollFrame(tk.Frame):
    """A frame with a vertical scrollbar that only appears when the content is taller than the space."""

    def __init__(self, parent, bg, width=270):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, width=width)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", self._update)
        self.canvas.bind("<Configure>", self._update)
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", lambda e: self._wheel(True))
            w.bind("<Leave>", lambda e: self._wheel(False))

    def _update(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.itemconfigure(self.win, width=self.canvas.winfo_width())
        need = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 2
        if need and not self.sb.winfo_ismapped():
            self.sb.pack(side="right", fill="y")
        elif not need and self.sb.winfo_ismapped():
            self.sb.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, on):
        if on:
            self.canvas.bind_all("<MouseWheel>", self._scroll)
            self.canvas.bind_all("<Button-4>", lambda e: self._scroll(e, -1))
            self.canvas.bind_all("<Button-5>", lambda e: self._scroll(e, 1))
        else:
            for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.unbind_all(ev)

    def _scroll(self, e, d=None):
        if not self.sb.winfo_ismapped():
            return
        if d is None:
            d = -1 if e.delta > 0 else 1
        self.canvas.yview_scroll(d, "units")
STYLE_TIPS = {
    "Cautious": "Small trades and tight safety stops. Slower and steadier.",
    "Balanced": "Medium trades and sensible stops. A good starting point.",
    "Adventurous": "Bigger trades and wider stops. More upside, more risk.",
}


def friendly_text(t):
    for k, v in STRAT_LABELS.items():
        t = t.replace(k, v)
    return t


def friendly_label(name, params, tf=1, bar=None):
    """Strategy name and settings, plus how often it decides (e.g. 'checks every 15 min')."""
    pace = ""
    if tf and tf > 1:
        pace = f", every {E.fmt_bar(tf * bar)}" if bar else f", every {tf} bars"
    return f"{STRAT_LABELS.get(name, name)} ({'/'.join(str(v) for v in params.values())}{pace})"


def _sort_key(v):
    s = str(v).replace(",", "").replace("$", "").replace("%", "").replace("x", "").replace("+", "").strip()
    try:
        return (0, float(s), "")
    except ValueError:
        return (1, 0.0, str(v).lower())


def sort_tree(t, col, reverse=None):
    """Sorts a list by a column (click again to flip). The choice sticks across refreshes."""
    cur = getattr(t, "_sort", None)
    if reverse is None:
        reverse = (not cur[1]) if cur and cur[0] == col else False
    t._sort = (col, reverse)
    items = sorted(((_sort_key(t.set(i, col)), i) for i in t.get_children("")), reverse=reverse)
    for idx, (_, i) in enumerate(items):
        t.move(i, "", idx)


def resort(t):
    cur = getattr(t, "_sort", None)
    if cur:
        try:
            sort_tree(t, cur[0], cur[1])
        except Exception:
            pass



COIN_COLORS = {"BTC": "#f7931a", "ETH": "#627eea", "SOL": "#9945ff", "XRP": "#4b5563", "DOGE": "#c2a633",
               "ADA": "#0033ad", "AVAX": "#e84142", "LINK": "#2a5ada", "LTC": "#345d9d", "DOT": "#e6007a",
               "ATOM": "#2e3148", "XTZ": "#2c7df7", "NEAR": "#00c08b", "ALGO": "#6b7280", "SUI": "#4da2ff",
               "SHIB": "#f00500", "PEPE": "#3d8c40", "USDC": "#2775ca", "BCH": "#8dc351", "ETC": "#328332"}
STAKING = {  # whether the coin itself can be staked (how its network works); reward rates are up to you
    "BTC": ("No", "Bitcoin runs on proof of work (mining), so there is no native staking."),
    "ETH": ("Yes", "Ethereum runs on proof of stake. Many exchanges offer ETH staking; unstaking can take a while."),
    "SOL": ("Yes", "Solana runs on proof of stake. Staked SOL earns rewards each epoch (about every 2 days)."),
    "XRP": ("No", "The XRP Ledger has no staking. Some platforms offer lending products instead, which carry other risks."),
    "DOGE": ("No", "Dogecoin runs on proof of work (mining), so there is no native staking."),
    "ADA": ("Yes", "Cardano runs on proof of stake. Delegated ADA stays in your wallet."),
    "AVAX": ("Yes", "Avalanche runs on proof of stake. Native staking locks coins for a set period."),
    "LINK": ("Limited", "Chainlink has staking pools with limited capacity; availability depends on the platform."),
    "LTC": ("No", "Litecoin runs on proof of work (mining), so there is no native staking."),
    "DOT": ("Yes", "Polkadot runs on proof of stake. Unstaking has a waiting (unbonding) period."),
    "ATOM": ("Yes", "Cosmos runs on proof of stake. Unstaking has a waiting (unbonding) period."),
    "XTZ": ("Yes", "Tezos runs on proof of stake (baking/delegation)."),
    "NEAR": ("Yes", "NEAR runs on proof of stake."),
    "ALGO": ("Yes", "Algorand runs on pure proof of stake."),
    "SUI": ("Yes", "Sui runs on proof of stake."),
    "APT": ("Yes", "Aptos runs on proof of stake."),
    "POL": ("Yes", "Polygon (POL) can be staked to validators."),
    "BCH": ("No", "Bitcoin Cash runs on proof of work (mining)."),
    "ETC": ("No", "Ethereum Classic runs on proof of work (mining)."),
    "SHIB": ("No", "SHIB is a token with no native staking of its own."),
    "PEPE": ("No", "PEPE is a token with no staking of its own."),
    "USDC": ("No", "A stablecoin. It isn't staked, though some platforms pay rewards for holding it."),
}


def coin_color(c):
    if c in COIN_COLORS:
        return COIN_COLORS[c]
    h = sum(ord(ch) * (i + 7) for i, ch in enumerate(c))
    return "#%02x%02x%02x" % (80 + h % 150, 80 + (h // 7) % 150, 80 + (h // 49) % 150)


def draw_coin_badge(cv, x, y, size, c):
    """A round coin badge in the coin's colour with its ticker (not an official logo)."""
    col = coin_color(c)
    cv.create_oval(x, y, x + size, y + size, fill=col, outline="")
    cv.create_oval(x + 3, y + 3, x + size - 3, y + size - 3, outline="#ffffff", width=1)
    label = c if len(c) <= 3 else c[:3]
    cv.create_text(x + size / 2, y + size / 2, text=label, fill="white", font=("", max(7, int(size / (2.6 + len(label) * 0.35))), "bold"))


def fmt_px(px):
    if not px:
        return "-"
    return f"${px:,.2f}" if px >= 1 else f"${px:,.6f}"


def draw_egg(c, x, y, size, color, progress=0.0, phase=0, tags=(), royal=False, classic=False):
    """A speckled dragon egg. It wobbles more and cracks open as it gets closer to hatching (progress 0..1)."""
    k = size / 40.0
    wob = math.sin(phase / 1.7) * (1 + 3 * progress) if phase else 0
    cx = x + 20 * k + wob

    def P(*pts):
        return [v * k + (x + wob if i % 2 == 0 else y) for i, v in enumerate(pts)]
    c.create_oval(x + 6 * k, y + 50 * k, x + 34 * k, y + 56 * k, fill="#111827", outline="", tags=tags)  # shadow
    c.create_oval(*P(4, 4, 36, 52), fill="#f5f0e1", outline=color, width=2, tags=tags)
    for sx, sy, r in ((12, 18, 3), (25, 12, 2.5), (27, 30, 3.5), (14, 38, 2.5), (22, 44, 2)):
        c.create_oval(*P(sx - r, sy - r, sx + r, sy + r), fill=color, outline="", tags=tags)
    if progress > 0.3:
        c.create_line(*P(8, 26, 13, 22, 17, 27, 21, 23), fill="#57534e", width=max(1, 1.5 * k), tags=tags)
    if progress > 0.6:
        c.create_line(*P(21, 23, 25, 28, 29, 24, 33, 27), fill="#57534e", width=max(1, 1.5 * k), tags=tags)
    if classic and not royal:  # silver egg: a famous strategy
        c.create_line(*P(5, 30, 35, 30), fill="#e2e8f0", width=max(1, 3 * k), tags=tags)
        c.create_text(*P(20, 22), text="\u272a", fill="#475569", font=("", max(7, int(9 * k)), "bold"), tags=tags)
    if royal:  # royal egg: gold band and a little crown mark
        c.create_line(*P(5, 30, 35, 30), fill="#facc15", width=max(1, 3 * k), tags=tags)
        c.create_text(*P(20, 22), text="\u265b", fill="#a16207", font=("", max(7, int(9 * k)), "bold"), tags=tags)
        if phase and phase % 10 < 3:
            c.create_text(*P(33, 6), text="\u2726", fill="#fde047", font=("", max(6, int(7 * k))), tags=tags)
    if progress > 0.85:
        c.create_oval(*P(15, 14, 18, 17), fill="#111827", outline="", tags=tags)  # someone peeking out
        c.create_oval(*P(22, 14, 25, 17), fill="#111827", outline="", tags=tags)
    _ = cx


def draw_dragon(c, x, y, size, color, awake=True, phase=0, mood="plain", fire=False, flash=False, gold=False, tags=(),
                crown=False, pal=None):
    """A small dragon on a Tk canvas, inside a size x size box at (x, y). Awake dragons flap their wings
    and look around; sleeping ones curl up with their eyes shut. fire = a little puff of flame."""
    k = size / 26.0

    def mk(kind, *a, **kw):
        return getattr(c, "create_" + kind)(*a, tags=tags, **kw)

    def P(*pts):
        return [v * k + (x if i % 2 == 0 else y) for i, v in enumerate(pts)]
    pal = pal or {}
    belly = "#fde68a" if gold else pal.get("belly", "#f5f5f4")
    edge = "white" if flash else pal.get("outline", "")
    wing = pal.get("wing", color)
    horn = pal.get("horn", belly)
    eye = pal.get("eye", "#111827")
    flap = (math.sin(phase / 1.6) * 5) if awake else 0
    # tail
    mk('line', *P(6, 20, 1, 22, -1, 17, 1, 14), fill=color, width=max(1, 2 * k), smooth=True)
    mk('polygon', *P(1, 14, -2, 12, 2, 11), fill=color, outline="")
    # far wing (flaps)
    if awake:
        mk('polygon', *P(9, 12, 3, 2 - flap, 8, 5 - flap / 2, 14, 10), fill=wing, outline=edge, stipple="gray75")
    else:
        mk('polygon', *P(8, 13, 6, 9, 14, 11), fill=color, outline="")
    # body and belly
    mk('oval', *P(4, 11, 21, 25), fill=color, outline=edge, width=1)
    mk('oval', *P(9, 16, 19, 24), fill=belly, outline="")
    # legs
    mk('line', *P(8, 23, 7, 26), fill=color, width=max(1, 2 * k))
    mk('line', *P(16, 23, 17, 26), fill=color, width=max(1, 2 * k))
    hy = 0 if awake else 6  # sleeping dragons rest their head low
    look = math.sin(phase / 5.0) * 1.2 if awake else 0
    # neck and head
    mk('polygon', *P(15, 14, 18, 8 + hy, 21, 9 + hy, 19, 16), fill=color, outline="")
    mk('oval', *P(16 + look, 3 + hy, 26 + look, 11 + hy), fill=color, outline=edge)
    # horns
    mk('line', *P(18 + look, 4 + hy, 17 + look, 0 + hy), fill=horn, width=max(1, 1.5 * k))
    mk('line', *P(21 + look, 3 + hy, 21 + look, -1 + hy), fill=horn, width=max(1, 1.5 * k))
    # eye
    if awake:
        mk('oval', *P(21 + look, 5 + hy, 23.5 + look, 7.5 + hy), fill="white", outline="")
        mk('oval', *P(22 + look, 5.6 + hy, 23.3 + look, 7 + hy), fill=eye, outline="")
    else:
        mk('line', *P(20.5, 6.5 + hy, 23.5, 6.5 + hy), fill="#111827", width=max(1, 1.2 * k))
    if mood == "happy" and awake:
        mk('line', *P(23 + look, 9 + hy, 25 + look, 8.5 + hy), fill="#111827", width=1)
    # near wing
    if awake:
        mk('polygon', *P(10, 13, 6, 3 + flap, 12, 6 + flap / 2, 16, 12), fill=wing, outline=pal.get("outline", "#111827"), width=1)
    if crown:
        mk('polygon', *P(17 + look, 2 + hy, 17.5 + look, -3 + hy, 19.5 + look, -0.5 + hy, 21.5 + look, -4 + hy,
                         23.5 + look, -0.5 + hy, 25 + look, -3 + hy, 25 + look, 2 + hy), fill="#facc15", outline="#a16207")
    if fire:
        f = (phase % 3)
        mk('polygon', *P(26 + look, 7 + hy, 31 + f, 5 + hy, 30 + f, 7 + hy, 33 + f, 8 + hy, 26 + look, 9 + hy),
                         fill="#f97316", outline="#fde047")



THEMES = {  # themed dragons: palette per type
    "fire": dict(body="#dc2626", belly="#fbbf24", horn="#111827", wing="#7f1d1d", eye="#fde047"),
    "ice": dict(body="#7dd3fc", belly="#f0f9ff", horn="#e0f2fe", wing="#0ea5e9", eye="#0c4a6e"),
    "lightning": dict(body="#facc15", belly="#fef9c3", horn="#1e3a8a", wing="#2563eb", eye="#1e3a8a"),
    "space": dict(body="#3730a3", belly="#a78bfa", horn="#e9d5ff", wing="#1e1b4b", eye="#f0abfc"),
    "skeleton": dict(body="#1f2937", belly="#1f2937", horn="#e7e5e4", wing="#374151", eye="#4ade80", outline="#e7e5e4"),
}
FAMILY_THEME = {  # strategy family -> dragon type
    "SMA crossover": "fire", "EMA crossover": "fire", "MACD": "fire", "Momentum": "fire",
    "RSI": "ice", "Stochastic": "ice", "Bollinger bands": "ice", "Mean reversion": "ice", "VWAP snap-back": "ice",
    "Breakout": "lightning", "Volume breakout": "lightning", "ATR channel": "lightning",
    "Trend + dip": "space", "Dollar-cost averaging": "space",
    "Supertrend": "lightning", "Ichimoku cross": "space", "Rate of change": "fire", "MA ribbon": "fire",
}
THEME_NAMES = {"fire": "Fire dragon (trend follower)", "ice": "Ice dragon (buys dips)",
               "lightning": "Lightning dragon (breakouts)", "space": "Space dragon (dips in an uptrend)",
               "skeleton": "Skeleton dragon (resting: not winning right now)"}
CHARACTER_MODE = {"mode": "Themed dragons"}  # or "Classic dragons" (coloured by status)


def theme_for(strat, status=None):
    if CHARACTER_MODE["mode"] != "Themed dragons":
        return None
    if status == "benched":
        return "skeleton"
    return FAMILY_THEME.get(strat, "fire")


def draw_creature(c, strat, x, y, size, color, status=None, **kw):
    """The bot's character. Themed mode: the dragon type comes from the strategy family (resting bots are
    skeletons) and its status colour glows on the ground under it. Classic mode: a dragon in the status colour."""
    th = theme_for(strat, status)
    tags = kw.get("tags", ())
    k = size / 26.0
    if th is None:
        return draw_dragon(c, x, y, size, color, **kw)
    if color:
        c.create_oval(x + 1 * k, y + 24 * k, x + 24 * k, y + 29 * k, fill=color, outline="", stipple="gray50", tags=tags)
    pal = THEMES[th]
    awake, phase = kw.get("awake", True), kw.get("phase", 0)
    draw_dragon(c, x, y, size, pal["body"], pal=pal, **kw)

    def P(*pts):
        return [v * k + (x if i % 2 == 0 else y) for i, v in enumerate(pts)]
    if th == "fire":  # devil tail tip and rising embers
        c.create_polygon(*P(-3, 12, 0, 15, 2, 11), fill="#111827", outline="", tags=tags)
        if awake:
            for n in range(3):
                t = (phase + n * 4) % 12
                c.create_oval(*P(6 + n * 6, 10 - t, 7.5 + n * 6, 11.5 - t), fill="#f97316" if t < 8 else "#fde047",
                              outline="", tags=tags)
    elif th == "ice":  # drifting snowflakes
        for n in range(2 if awake else 1):
            t = (phase + n * 7) % 14
            c.create_text(*P(3 + n * 15, -2 + t), text="\u2744", fill="#e0f2fe", font=("", max(6, int(6 * k))), tags=tags)
    elif th == "lightning":  # a crackle of lightning now and then
        if awake and phase % 6 < 2:
            c.create_line(*P(4, -4, 1, 2, 5, 2, 2, 8), fill="#fde047", width=max(1, 1.5 * k), tags=tags)
        if awake and phase % 6 == 3:
            c.create_line(*P(27, -2, 24, 3, 28, 3, 25, 9), fill="#93c5fd", width=max(1, 1.5 * k), tags=tags)
    elif th == "space":  # twinkling stars and a tiny orbiting moon
        for n, (sx, sy) in enumerate(((2, 2), (25, 18), (3, 24))):
            if (phase + n * 3) % 8 < 5:
                c.create_text(*P(sx, sy), text="\u2726", fill="#f0abfc", font=("", max(5, int(5 * k))), tags=tags)
        if awake:
            a = phase / 4.0
            c.create_oval(*P(13 + 13 * math.cos(a) - 1.5, 15 + 5 * math.sin(a) - 1.5,
                             13 + 13 * math.cos(a) + 1.5, 15 + 5 * math.sin(a) + 1.5), fill="#e9d5ff", outline="", tags=tags)
    elif th == "skeleton":  # ribs
        for n in range(3):
            c.create_line(*P(10, 16 + n * 2.5, 18, 16 + n * 2.5), fill="#e7e5e4", width=1, tags=tags)


class Tip:
    """Small hover tooltip."""

    def __init__(self, widget, text):
        self.w, self.text, self.tw = widget, text, None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _=None):
        if self.tw:
            return
        self.tw = tk.Toplevel(self.w)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{self.w.winfo_rootx() + 20}+{self.w.winfo_rooty() + 24}")
        tk.Label(self.tw, text=self.text, bg=LINE if DARK else "#111827", fg="white", padx=8, pady=4,
                 wraplength=280, justify="left").pack()

    def hide(self, _=None):
        if self.tw:
            self.tw.destroy()
            self.tw = None



def read_version():
    try:
        with open(os.path.join(HERE, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


class App:
    def __init__(self, root):
        self.root = root
        self.version = read_version()
        root.title("CBBF")
        root.withdraw()  # stay hidden until the window is sized and centred
        try:  # the little dragon in the title bar and taskbar
            import icon_data
            self._icons = [tk.PhotoImage(data="".join(icon_data.ICON_64)), tk.PhotoImage(data="".join(icon_data.ICON_32))]
            root.iconphoto(True, *self._icons)
        except Exception:
            pass
        root.geometry("1050x720")
        self.q = queue.Queue()
        self.running = False
        self.client = None
        self.usd_avail = 0.0
        self.balances = {}
        self.last_bal = 0.0
        self.live = False
        self.session = False
        self.logging_in = False
        self.migrated = E.migrate_home()  # first start after the rename: bring the old data folder across
        self.cfg = E.load_json(CONFIG_PATH, {})
        apply_theme(root, bool(self.cfg.get("dark", True)))
        self.dark_var = tk.BooleanVar(value=DARK)
        self.settings = E.merge_settings(self.cfg.get("settings"))
        CHARACTER_MODE["mode"] = self.settings.get("characters", "Themed dragons")
        if not self.cfg.get("live_feel_v1"):  # one-time move to the faster defaults (1-minute bars, 2-second prices)
            self.settings["bar_minutes"], self.settings["refresh"] = 1, 2
            self.cfg["settings"], self.cfg["live_feel_v1"] = self.settings, True
            try:
                E.save_json(CONFIG_PATH, self.cfg)
            except OSError:
                pass
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.awake = E.AwakeLock()
        self.worker_gen, self.worker_thread, self.worker_args = 0, None, None
        self._err_seen, self._gap_ref, self._stale_sent = {}, None, False
        root.report_callback_exception = self.on_cb_error
        self.reset_state(1000.0)
        self.build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self.drain)
        root.after(5000, self.watchdog)
        root.after(400, self.auto_login)
        root.after(700, self.maybe_welcome)
        root.after(4000, self.start_tg_remote)
        root.after(20000, self.start_free_roam)
        root.after(1500, self.maybe_setup)
        root.after(4000, self.auto_check_update)
        root.after(1200, self.restore_last_session)
        root.after(2500, self.auto_resume)
        if self.migrated:
            root.after(900, lambda: self.write("Welcome to CBBF. Your data from the old Desk Utils folder was copied over.", "good"))
        self.fit_and_center()

    # ---------------- state ----------------
    def reset_state(self, cash):
        self.start_cash = cash
        self.cash = cash
        self.pos, self.cost = {}, {}
        self.hist, self.last = {}, {}
        self.trades, self.curve = [], []
        self.shadow_trades = []  # every bot's own pretend trades (Wildcard), shown in the Trades tab
        self.bench_units = {}
        self.pending = set()
        self.pending_who = {}
        self.pending_detail = {}   # coin -> "why did it trade?" text for the order on its way
        self.pending_voters = {}   # coin -> uids of the bots whose votes made the buy
        self.bot_alloc = {}        # coin -> {bot uid: $ of the open position its votes bought}
        self._trade_rows = {}      # Trades tab row id -> trade
        self.shadow_live = None    # Shadow Live: pretend account that follows only your Hall of Fame bots
        self.shadow_curve = []     # (time, its value)
        self.farm_hist = deque(maxlen=6000)  # farm replay: (time, eggs, rising, beasts, events) per decision
        self.inc = {}
        self.fails = 0
        self.bar_n = 0
        self.last_bar = 0.0
        self.tourn = None
        self.seeded = False
        self.wild = False
        self.active = []
        self.session_coins = []
        self.session_bar = self.settings["bar_minutes"]
        self.day, self.day_eq, self.halted_day = None, None, None
        self.wild_msg, self.last_wild_msg = "", ""
        self.quote = "USD"
        self.start_px, self.last_tick = {}, None
        self.high = {}            # highest price since buying, per coin (trailing stop)
        self.trade_marks = []     # (chart point number, side) for the buy/sell markers
        self.curve_n = 0          # chart points added so far this session
        self.inducted = set()     # Hall of Fame keys added this session
        self.fees_paid = 0.0      # fees paid by the account this session
        self.bought_at = {}       # coin -> time the current position was first bought (minimum hold)
        self.hx = {}              # coin -> {'h','l','v'} lined up with self.hist (highs, lows, volumes per bar)
        self.bar_hl = {}          # coin -> [high, low] of the ticks inside the bar being built
        self.cur_vol = {}         # coin -> latest volume per bar from Coinbase candles
        self.tick_px = {}         # coin -> deque of (time, price) for the tick chart
        self.brake_note = 0.0

    def save_state(self, force=True):
        if not self.session:
            return
        if not force:
            if time.time() - getattr(self, "_saved_at", 0) < 5:
                if not getattr(self, "_save_pending", False):
                    self._save_pending = True
                    self.root.after(5000, lambda: (setattr(self, "_save_pending", False), self.save_state()))
                return
        self._saved_at = time.time()
        E.save_json(E.state_path(self.live), dict(
            start_cash=self.start_cash, cash=self.cash, pos=self.pos, cost=self.cost,
            trades=self.trades[-10000:], shadow=self.shadow_trades[-4000:], curve=self.curve[-8000:], bench_units=self.bench_units,
            coins=self.session_coins, bar_n=self.bar_n, bar=self.session_bar, quote=self.quote, high=self.high,
            fees_paid=self.fees_paid, bought_at=self.bought_at,
            bot_alloc={c: {str(u): x for u, x in a.items()} for c, a in self.bot_alloc.items()},
            shadow_live=None if not self.shadow_live else dict(cash=self.shadow_live.cash, pos=self.shadow_live.pos,
                                                               cost=self.shadow_live.cost, fees=self.shadow_live.fees),
            shadow_curve=self.shadow_curve[-4000:], farm_hist=list(self.farm_hist)[-3000:]))

    def save_farm(self, force=False):
        """Saves the whole farm (every bot, its history and family tree) next to the session, so it carries on
        where it left off. Done every 20 decisions and whenever the bot stops, switches mode or the app closes."""
        if not (self.session and self.tourn and self.seeded):
            return
        if not force and self.bar_n % 20:
            return
        try:
            E.save_json(E.farm_path(self.live), dict(coins=self.session_coins, bar=self.session_bar,
                                                      saved_at=time.time(), farm=self.tourn.to_state()))
        except Exception:
            note = self._record_error("saving the farm")
            if note:
                self.write(note, "bad")

    def load_farm(self):
        """The saved farm for this session, ready to carry on (or None)."""
        d = E.load_json(E.farm_path(self.live))
        if not isinstance(d, dict) or d.get("coins") != self.session_coins or float(d.get("bar", -1)) != float(self.session_bar):
            return None
        t = E.Tournament(self.session_coins, self.start_cash, *E.cost_model(self.settings)[:1],
                         self.settings["trade_pct"] / 100, self.settings["evolve_every"], params=self.settings["params"],
                         opts=E.tournament_opts(self.settings), cap=int(self.settings["max_bots"]),
                         retire_steps=self.retire_steps(self.session_bar))
        return t if t.restore(d.get("farm")) else None

    def restore_last_session(self):
        """On start-up: put the last session (and its farm) back on screen, paused, exactly where it left off."""
        if self.session:
            return
        mode = self.cfg.get("last_mode", "paper")
        if mode == "live":  # CBBF always opens in Practice; the saved LIVE session waits behind the LIVE tab
            if self.saved_session(True):
                self.write("Your LIVE session is saved. CBBF opens in Practice; click LIVE at the top if you want to go back to it.")
            mode = "paper"
        saved = self.saved_session(False)
        if not saved:
            return
        self._auto_resume = True
        try:
            ok = self.begin_session(list(saved.get("coins") or []), quiet=True)
        finally:
            self._auto_resume = False
        if not ok:
            return
        self.wild_var.set(bool(self.cfg.get("last_wild", True)))
        self.toggle_wild()
        if self.wild_var.get():
            t = self.load_farm()
            if t:
                self.tourn, self.seeded, self._restored_farm = t, False, t
                self.refresh_board()
        self.update_status()
        self.refresh_view()
        self.write(f"Welcome back: your {'LIVE' if mode == 'live' else 'Practice'} session is just as you left it"
                   + (f", with all {len(self.tourn.vars)} bots" if self.tourn else "") + ". Press Continue to carry on.", "good")

    def load_state(self, s):
        self.start_cash, self.cash = s["start_cash"], s["cash"]
        self.pos, self.cost = s["pos"], s["cost"]
        self.trades, self.curve = s["trades"], [tuple(x) for x in s["curve"]]
        self.shadow_trades = [r for r in s.get("shadow", []) if isinstance(r, dict)]
        self.bench_units, self.session_coins = s["bench_units"], s["coins"]
        self.bar_n, self.session_bar = s["bar_n"], s.get("bar", 5)
        self.quote = s.get("quote", "USD")
        self.high = {c: float(v) for c, v in (s.get("high") or {}).items() if isinstance(v, (int, float))}
        self.fees_paid = float(s.get("fees_paid", 0) or 0)
        self.bought_at = {c: float(t) for c, t in (s.get("bought_at") or {}).items() if isinstance(t, (int, float))}
        try:
            self.bot_alloc = {c: {int(u): float(x) for u, x in a.items()} for c, a in (s.get("bot_alloc") or {}).items()}
        except (TypeError, ValueError, AttributeError):
            self.bot_alloc = {}
        try:
            sl = s.get("shadow_live")
            if sl:
                self.shadow_live = E.Sim(self.start_cash, 0.006, self.settings["trade_pct"] / 100)
                self.shadow_live.cash, self.shadow_live.fees = float(sl["cash"]), float(sl.get("fees", 0))
                self.shadow_live.pos = {c: float(q) for c, q in sl["pos"].items()}
                self.shadow_live.cost = {c: float(q) for c, q in sl["cost"].items()}
            self.shadow_curve = [tuple(x) for x in s.get("shadow_curve") or []]
            self.farm_hist = deque([tuple(x) for x in s.get("farm_hist") or []], maxlen=6000)
        except (TypeError, ValueError, KeyError, AttributeError):
            self.shadow_live, self.shadow_curve = None, []

    def on_close(self):
        if self.running and not messagebox.askyesno(
                "Quit", "The bot is running. Quitting stops it (open positions stay open).\n\nQuit anyway?"):
            return
        self.cfg["was_running"] = bool(self.running)  # closed while running: pick it up next time
        self.save_farm(force=True)
        try:
            self.slow_save()
        except Exception:
            pass
        self.running = False
        self.awake.off()
        try:
            self.cfg["size"] = f"{self.root.winfo_width()}x{self.root.winfo_height()}"
            E.save_json(CONFIG_PATH, self.cfg)
        except Exception:
            pass
        self.save_state()
        self.root.destroy()

    # ---------------- UI ----------------
    def build_ui(self):
        root = self.root
        root.configure(bg=BG)
        root.minsize(980, 640)
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        self.setup_styles()

        self.build_menu()
        root.bind("<Map>", self.on_restore, add="+")
        root.bind("<Configure>", self._on_root_configure, add="+")

        header = tk.Frame(root, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        header.pack(fill="x")
        tk.Label(header, text="CBBF", bg=CARD, fg=TEXT, font=("", 14, "bold")).pack(side="left", padx=14, pady=10)
        self.mode_var = tk.StringVar(value="paper")
        segs = tk.Frame(header, bg=CARD)
        segs.pack(side="left")
        self.seg_paper = tk.Label(segs, text="PRACTICE", font=("", 10, "bold"), padx=14, pady=4, cursor="hand2")
        self.seg_live = tk.Label(segs, text="LIVE", font=("", 10, "bold"), padx=14, pady=4, cursor="hand2")
        self.seg_paper.pack(side="left")
        self.seg_live.pack(side="left", padx=(2, 0))
        self.seg_paper.bind("<Button-1>", lambda e: self.switch_mode("paper"))
        self.seg_live.bind("<Button-1>", lambda e: self.switch_mode("live"))
        Tip(self.seg_paper, "PRACTICE: pretend money on real prices. Nothing real happens. Has its own session, separate from Live.")
        Tip(self.seg_live, "LIVE: REAL money on your Coinbase account. Has its own session, separate from Practice.")
        self.mode_pill = tk.Label(header, text="", bg=CARD, fg=GREEN, font=("", 9, "bold"), padx=10)
        self.mode_pill.pack(side="left")
        self.kill_btn = tk.Button(header, text="\u25a0 KILL", command=self.kill_switch, relief="flat", bd=0, bg="#b91c1c",
                                  fg="white", activebackground="#7f1d1d", activeforeground="white", padx=10, pady=3,
                                  cursor="hand2", font=("", 9, "bold"))
        self.kill_btn.pack(side="left", padx=(6, 0))
        Tip(self.kill_btn, "Kill switch: stops the bot, cancels waiting orders, sells everything at market and locks LIVE "
                           "mode until you unlock it.")
        self.theme_btn = tk.Button(header, text="Light mode" if DARK else "Dark mode", command=lambda: self.set_dark(not DARK),
                                   relief="flat", bd=0, bg=LINE, fg=TEXT, activebackground=LINE, activeforeground=TEXT,
                                   padx=10, pady=3, cursor="hand2")
        self.theme_btn.pack(side="right", padx=(0, 14))
        self.mute_btn = tk.Button(header, text="\u266a off" if self.cfg.get("muted") else "\u266a", command=self.toggle_mute,
                                  relief="flat", bd=0, bg=CARD, fg=TEXT, activebackground=CARD, cursor="hand2", font=("", 11))
        self.mute_btn.pack(side="right", padx=(0, 8))
        Tip(self.mute_btn, "Mute or unmute all notifications. Sounds are off unless you turn them on in Settings.")
        self.upd_btn = tk.Button(header, text="\u27f3 Check for updates", command=self.update_clicked, relief="flat", bd=0,
                                 bg=LINE, fg=TEXT, activebackground=LINE, activeforeground=TEXT, padx=10, pady=3,
                                 cursor="hand2", font=("", 9, "bold"))
        self.upd_btn.pack(side="right", padx=(0, 8))
        Tip(self.upd_btn, f"You have version {self.version}. Checks GitHub for a newer one; nothing installs until you accept.")
        self.conn_lbl = tk.Label(header, text="Not logged in", bg=CARD, fg=MUTED)
        self.conn_lbl.pack(side="right", padx=14)
        self.net_lbl = tk.Label(header, text="\u25cf", bg=CARD, fg=MUTED, font=("", 11))
        self.net_lbl.pack(side="right")
        self.net_tip = Tip(self.net_lbl, "Price connection: not started yet.")
        self.tick_lbl = tk.Label(header, text="", bg=CARD, fg=MUTED)
        self.tick_lbl.pack(side="right", padx=14)
        self.err_lbl = tk.Label(header, text="", bg=CARD, fg=RED, cursor="hand2", font=("", 9, "bold"))
        self.err_lbl.bind("<Button-1>", lambda e: self.show_diagnostics())

        self.mode_stripe = tk.Frame(root, height=4, bg=GREEN)
        self.mode_stripe.pack(fill="x")
        self.headline_lbl = tk.Label(root, text="", bg="#1e3a5f" if DARK else "#eff6ff", fg="#bfdbfe" if DARK else "#1e3a8a", anchor="w", justify="left",
                                     padx=14, pady=7, wraplength=1000, font=("", 10))
        self.headline_lbl.pack(fill="x")

        body = self.body = tk.Frame(root, bg=BG, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        self.left_scroll = ScrollFrame(body, BG, width=262)
        self.left_scroll.pack(side="left", fill="y", padx=(0, 10))
        left = self.left_scroll.inner
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # --- account card
        f, c = self.card(left, "ACCOUNT")
        f.pack(fill="x", pady=(0, 8))
        self.mode_desc = tk.Label(c, text="", bg=CARD, fg=TEXT, font=("", 10, "bold"), anchor="w", justify="left",
                                  wraplength=230)
        self.mode_desc.pack(fill="x")
        self.budget_lbl = tk.Label(c, text="Practice money (pretend, USD)", bg=CARD, fg=MUTED)
        self.budget_lbl.pack(anchor="w", pady=(8, 0))
        self.cash_var = tk.StringVar(value="1000")
        ttk.Entry(c, textvariable=self.cash_var).pack(fill="x")
        tk.Label(c, text="Risk style", bg=CARD, fg=MUTED).pack(anchor="w", pady=(8, 0))
        self.style_var = tk.StringVar(value=self.settings.get("style", "Balanced"))
        cb = ttk.Combobox(c, textvariable=self.style_var, values=list(E.STYLES) + ["Custom"], state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", self.on_style)
        Tip(cb, "Sets trade size and safety limits in one go. Cautious = small and steady, Adventurous = big and bold.")
        tk.Label(c, text="Trade with (cash currency)", bg=CARD, fg=MUTED).pack(anchor="w", pady=(8, 0))
        self.quote_var = tk.StringVar(value=self.cfg.get("quote", "USD"))
        qb = ttk.Combobox(c, textvariable=self.quote_var, values=["USD", "USDC"], state="readonly")
        qb.pack(fill="x")
        qb.bind("<<ComboboxSelected>>", self.on_quote)
        Tip(qb, "USD or USDC. Decides which balance Live mode spends and which Coinbase market (for example BTC-USDC) "
                "is used. Takes effect on a new session.")
        self.login_btn = ttk.Button(c, text="Log in to Coinbase", style="Plain.TButton", command=self.account_action)
        self.login_btn.pack(fill="x", pady=(8, 0))
        tk.Label(c, text="Practice mode needs no login.", bg=CARD, fg=MUTED, font=("", 8)).pack(anchor="w")

        # --- coins card
        f, c = self.card(left, "COINS")
        f.pack(fill="x", pady=(0, 8))
        self.coin_grid = tk.Frame(c, bg=CARD)
        self.coin_grid.pack(anchor="w", fill="x")
        self.coin_vars = {}
        ticked = self.cfg.get("ticked_coins") or ["BTC", "ETH"]
        for coin in self.all_coins():
            self.coin_vars[coin] = tk.BooleanVar(value=coin in ticked)
        self.build_coin_grid()
        crow = tk.Frame(c, bg=CARD)
        crow.pack(fill="x", pady=(6, 0))
        mc = ttk.Button(crow, text="More coins...", style="Plain.TButton", command=self.open_coin_browser)
        mc.pack(side="left")
        Tip(mc, "Browse every coin Coinbase trades, with 24-hour change and volume, and add any of them. (Ctrl+K)")
        self.auto_coin_var = tk.BooleanVar(value=int(self.settings.get("auto_coins", 0)) > 0)
        ab = ttk.Checkbutton(crow, text="Bot picks", variable=self.auto_coin_var, style="Card.TCheckbutton",
                             command=self.toggle_auto_coins)
        ab.pack(side="left", padx=(8, 0))
        Tip(ab, "Wildcard only: when you press Start, the bot scouts Coinbase and trades the busiest coins that are "
                "actually moving (no stablecoins). How many: Settings > Wildcard.")

        # --- strategy card
        f, c = self.card(left, "STRATEGY")
        f.pack(fill="x", pady=(0, 8))
        self.wild_var = tk.BooleanVar(value=False)
        wb = ttk.Checkbutton(c, text="Let the bot choose (Wildcard)", variable=self.wild_var,
                             style="Card.TCheckbutton", command=self.toggle_wild)
        wb.pack(anchor="w")
        Tip(wb, "The bot tests every strategy in the background, trades bigger with the ones that are winning "
                "consistently, and benches or retires the ones that aren't. Recommended if you're not sure.")
        self.more_var = tk.BooleanVar(value=bool(self.settings.get("winners_trade_more", True)))
        mb = ttk.Checkbutton(c, text="Winners trade more", variable=self.more_var, style="Card.TCheckbutton",
                             command=self.toggle_more)
        mb.pack(anchor="w", padx=(18, 0))
        rrow = tk.Frame(c, bg=CARD)
        rrow.pack(fill="x", padx=(18, 0), pady=(2, 0))
        tk.Label(rrow, text="Bots trading:", bg=CARD, fg=MUTED, font=("", 8)).pack(side="left")
        if "roster_paper" not in self.cfg:  # first start after this feature: keep an old "trade with my team" choice
            self.cfg["roster_paper"] = "team" if self.cfg.get("team_mode") else "farm"
        self.roster_var = tk.StringVar()
        self.roster_box = ttk.Combobox(rrow, textvariable=self.roster_var, state="readonly", width=20)
        self.roster_box.pack(side="left", padx=(4, 0))
        self.roster_box.bind("<<ComboboxSelected>>", lambda e: self.set_roster())
        Tip(self.roster_box, "Which bots may trade (Wildcard). Whole farm: everyone competes and the winners are followed. "
                             "My team: only your team from the Bot Library. Hall of Fame only: only the champions you "
                             "picked, nothing new is bred. Practice and Live each remember their own choice.")
        self.update_team_label()
        Tip(mb, "Strategies that keep winning get bigger trades and keep adding while they hold a coin. "
                "Shaky ones are slowed down and failing ones are stopped. Turn off for calmer, less frequent trading. "
                "More trading means more fees.")
        self.strat_box = tk.Frame(c, bg=CARD)
        self.strat_box.pack(fill="x")
        self.wild_note = tk.Label(c, text="The bot runs every strategy and follows the winners. Untick Wildcard to pick your own.",
                                  bg=CARD, fg=MUTED, font=("", 8), justify="left", wraplength=220)
        tk.Label(self.strat_box, text="Or pick your own:", bg=CARD, fg=MUTED, font=("", 8)).pack(anchor="w", pady=(4, 0))
        self.strat_vars, self.strat_btns = {}, []
        self.size_lbls = {}
        for name in E.STRATS:
            v = tk.BooleanVar(value=name == "SMA crossover")
            row = tk.Frame(self.strat_box, bg=CARD)
            row.pack(fill="x")
            b = ttk.Checkbutton(row, text=STRAT_LABELS[name], variable=v, style="Card.TCheckbutton")
            b.pack(side="left")
            Tip(b, STRAT_TIPS[name])
            ttk.Button(row, text="+", width=2, command=lambda n=name: self.bump_size(n, 0.25)).pack(side="right")
            lbl = tk.Label(row, text="", bg=CARD, fg=MUTED, font=("", 8), width=5)
            lbl.pack(side="right")
            Tip(lbl, "Trade size for this strategy when you pick strategies yourself. 1.00x = normal.")
            ttk.Button(row, text="-", width=2, command=lambda n=name: self.bump_size(n, -0.25)).pack(side="right")
            self.size_lbls[name] = lbl
            self.strat_vars[name] = v
            self.strat_btns.append(b)

        self.btn = ttk.Button(left, text="Start the bot", style="Accent.TButton", command=lambda: self.start_choice("continue"))
        self.btn.pack(fill="x", pady=(2, 4))
        self.new_btn = ttk.Button(left, text="Start new", style="Plain.TButton", command=lambda: self.start_choice("new"))
        self.new_btn.pack(fill="x", pady=(0, 4))
        self.new_btn.pack_forget()
        self._new_after = self.btn
        Tip(self.new_btn, "Start a fresh session instead of continuing the saved one. Your trade history stays in the "
                          "Trades tab and trades.csv.")
        self.reset_btn = ttk.Button(left, text="Reset \u25be", style="Plain.TButton")
        self.reset_btn.configure(command=lambda: self.reset_menu(self.reset_btn))
        self.reset_btn.pack(fill="x", pady=(0, 4))
        Tip(self.reset_btn, "Start over: reset the practice account (cash and trades), the farm (fresh eggs), or both. "
                            "Your Library and Hall of Fame are always kept.")
        ttk.Button(left, text="Sell everything now", style="Plain.TButton", command=self.sell_all).pack(fill="x")
        trow = tk.Frame(left, bg=BG)
        trow.pack(fill="x", pady=(4, 0))
        ttk.Button(trow, text="Buy...", style="Accent.TButton", command=lambda: self.trade_dialog("buy")).pack(side="left", expand=True, fill="x")
        ttk.Button(trow, text="Sell...", style="Plain.TButton", command=lambda: self.trade_dialog("sell")).pack(side="left", expand=True, fill="x", padx=(4, 0))
        tk.Label(left, text=f"v{self.version}", bg=BG, fg=MUTED, font=("", 8)).pack(anchor="w", pady=(6, 0))

        # --- right side: stat tiles, chart, tabs
        tiles = tk.Frame(right, bg=BG)
        tiles.pack(fill="x")
        self.tiles = {}
        for i, (key, title) in enumerate((("equity", "TOTAL VALUE"), ("pl", "PROFIT / LOSS"), ("day", "TODAY"), ("cash", "CASH LEFT"), ("hold", "IF YOU JUST HELD"))):
            f, c = self.card(tiles, title)
            f.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            tiles.columnconfigure(i, weight=1)
            if key == "hold":
                self.hold_title = c.winfo_children()[0]
            self.__dict__.setdefault("tile_titles", {})[key] = c.winfo_children()[0]
            lbl = tk.Label(c, text="-", bg=CARD, fg=TEXT, font=("", 15, "bold"))
            lbl.pack(anchor="w")
            self.tiles[key] = lbl
            if key == "day":
                self.day_sub = tk.Label(c, text="", bg=CARD, fg=MUTED, font=("", 7))
                self.day_sub.pack(anchor="w")
                Tip(self.day_sub, "How much of today's loss limit is used up. At 100% the bot stops trading for the rest "
                                  "of the day (Settings > Basics > Daily loss limit).")
            Tip(lbl, {"equity": "Cash plus the current value of every coin you own.",
                      "pl": "How much you've made or lost since you started.",
                      "cash": "Money not currently invested in a coin.",
                      "hold": "How far the bot is ahead of (or behind) what you'd have made by buying once and doing nothing.",
                      "day": "How much your total value has changed since today began."}[key])
        self.root.after(50, self.apply_compact)
        self.verdict_lbl = tk.Label(right, text="", bg=BG, fg=MUTED, anchor="w", font=("", 10, "bold"))
        self.verdict_lbl.pack(fill="x", pady=(6, 0))
        self.activity_lbl = tk.Label(right, text="", bg=BG, fg=MUTED, anchor="w", font=("", 8))
        self.activity_lbl.pack(fill="x")
        Tip(self.activity_lbl, "The bots trade with pretend money inside the farm to find out what works; those trades "
                               "never change your balance. Your account (the tiles above) only trades when it copies "
                               "the winners, so it trades less often than the bots.")

        split = tk.PanedWindow(right, orient="vertical", bg=BG, sashwidth=6, bd=0, sashrelief="flat")
        split.pack(fill="both", expand=True, pady=(8, 0))
        f, c = self.card(split, None)
        self.build_chart_controls(c)
        self.charts_frame = tk.Frame(c, bg=CARD)
        self.charts_frame.pack(fill="both", expand=True)
        self.chart_card = f
        self.panels = []
        self.build_panels()
        split.add(f, minsize=150, stretch="never",
                  height={1: 250, 2: 250, 3: 260, 4: 400, 6: 420}[self.LAYOUTS[self.chart_layout.get()][0] * self.LAYOUTS[self.chart_layout.get()][1]])
        nbf = tk.Frame(split, bg=BG)
        split.add(nbf, minsize=180, stretch="always")
        self.split = split
        nb = self.nb = ttk.Notebook(nbf)
        nb.pack(fill="both", expand=True, pady=(8, 0))
        nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_visible())
        t0, t1, t2, t3, t4, t5, t6, t7 = (ttk.Frame(nb) for _ in range(8))
        nb.add(t0, text="  \u25c6 Bot Farm  ")
        nb.add(t7, text="  \u25c7 Slow farm  ")
        nb.add(t1, text="  Portfolio  ")
        nb.add(t6, text="  Watchlist  ")
        nb.add(t4, text="  Live prices  ")
        nb.add(t5, text="  Trades  ")
        nb.add(t2, text="  Strategy scoreboard  ")
        nb.add(t3, text="  What the bot did  ")
        self.build_grid_tab(t0)
        self.build_slow_tab(t7)
        self.build_portfolio(t1)
        self.build_watchlist(t6)
        tk.Label(t2, bg=CARD, fg=MUTED, justify="left", anchor="w", padx=8, pady=4, wraplength=700,
                 text="The bot tests every strategy with pretend trades. Winning = copied with bigger trades. "
                      "Shaky = copied slowly with small trades. Stopped = ignored.").pack(fill="x", side="top")
        bar2 = ttk.Frame(t2)
        bar2.pack(fill="x", padx=4, pady=(0, 2))
        bar3 = ttk.Frame(t2)
        bar3.pack(fill="x", padx=4, pady=(0, 4))
        for i, (txt, cmd, tip) in enumerate((
                ("New random strategy", self.new_random,
                 "Adds a brand new strategy with random settings. It replays recent prices, then competes with the rest."),
                ("Size -", lambda: self.bump_variant(-0.25), "Click a row first, then make that strategy's trades smaller."),
                ("Size +", lambda: self.bump_variant(0.25), "Click a row first, then make that strategy's trades bigger."),
                ("Save lineup", self.save_lineup, "Saves every strategy that isn't stopped, with its settings and size."),
                ("Load saved", self.load_lineup,
                 "Adds your saved strategies to this run. They also load automatically when Wildcard starts."),
                ("Export bots...", self.export_bots,
                 "Saves the strategies you selected (Ctrl/Shift-click for several) to a file to share or keep. "
                 "With none selected it exports everything that isn't stopped."),
                ("Import bots...", self.import_bots,
                 "Loads bots from a file. They replay recent prices, then compete with your own."),
                ("Hall of Fame...", self.open_hof,
                 "Bots that kept winning for a long time are saved here automatically. Export them or bring them back. (Ctrl+H)"),
                ("Catch!", self.catch_selected, "Catch the selected bots for your Bot Collection. They get a name, a rarity "
                 "and a level, and you can trade with your favourite team later."),
                ("Library...", self.open_collection, "Your saved bots (the Bot Library): rarities, levels, your team, and who is in the Hall of Fame. (Ctrl+B)"),
                ("Family tree...", self.open_family, "Which bot was bred or tweaked from which, including ones that died out."),
                ("Bot Lab...", self.open_lab,
                 "Evolve bots on months of past prices, then test them on prices they never saw. Only the ones that "
                 "pass are worth keeping. (Ctrl+L)"))):
            sb = ttk.Button(bar2 if i < 6 else bar3, text=txt, style="Accent.TButton" if txt == "Catch!" else "Plain.TButton",
                            command=cmd)
            sb.pack(side="left", padx=(0, 4))
            Tip(sb, tip)
        self.best_lbl = tk.Label(t2, text="", bg=BG, fg=MUTED, anchor="w", padx=6)
        self.best_lbl.pack(fill="x")
        self.build_farm_strip(t2)
        self.board = self.make_tree(t2, ("strategy", "recent", "edge", "consistent", "winrate", "life", "size", "status", "trades"),
                                    (230, 70, 80, 70, 65, 70, 55, 170, 55),
                                    ("Strategy", "Recent gain", "vs holding", "Wins lately", "Win rate", "Lifetime",
                                     "Size", "What the bot does", "Trades"))
        self.board.bind("<Double-1>", lambda e: self.bot_detail(quiet=True) if self.on_row(self.board, e) else None)
        for btn in ("<Button-3>", "<Button-2>"):
            self.board.bind(btn, lambda e: (lambda iid: self.bot_menu(e, next((v for v in self.tourn.vars if str(id(v)) == iid), None))
                                            if iid and self.tourn else None)(self.board.identify_row(e.y)))
        Tip(self.board, "Double-click a strategy for its full track record.")
        self.style_board_tags()
        self.empty_board = tk.Label(t2, text="Tick 'Let the bot choose (Wildcard)' and press Start.\nStrategies will compete here.",
                                    bg=CARD, fg=MUTED, pady=18)
        self.log = tk.Text(t3, height=10, state="disabled", bg=CARD, fg=TEXT, relief="flat", font=("", 10), wrap="word", padx=6, pady=4)
        for tag, color in (("good", GREEN), ("bad", RED), ("warn", "#fbbf24" if DARK else "#b45309"), ("info", TEXT)):
            self.log.tag_configure(tag, foreground=color)
        self.log.pack(fill="both", expand=True)
        self.prices_tree = self.make_tree(
            t4, ("coin", "price", "since", "hr", "day", "mood", "age"), (70, 120, 120, 90, 100, 170, 80),
            ("Coin", "Price now", "Since this run began", "Last hour", "Last 24 hours", "Market mood", "Updated"))
        trade_bar = ttk.Frame(t5)
        trade_bar.pack(fill="x", padx=4, pady=4)
        self.trade_count_lbl = ttk.Label(trade_bar, text="All paper + live trades")
        self.trade_count_lbl.pack(side="left")
        ttk.Button(trade_bar, text="Refresh", style="Plain.TButton",
                   command=self.refresh_trades).pack(side="right")
        self.show_bots_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(trade_bar, text="Show every bot's test trades", variable=self.show_bots_var,
                        command=self.refresh_trades).pack(side="right", padx=8)
        self.board.bind("<<TreeviewSelect>>", lambda e: self.soon(self._after_select))
        self.trade_tree = self.make_tree(
            t5, ("time", "mode", "side", "coin", "qty", "usd", "price", "pl", "who", "why"),
            (125, 55, 55, 55, 105, 80, 90, 80, 200, 170),
            ("Time", "Mode", "Action", "Coin", "Coins moved", "Value ($)", "Price", "Profit / loss", "Strategy behind it", "Why"))
        self.trade_tree.tag_configure("buy", foreground=GREEN)
        self.trade_tree.tag_configure("sell", foreground=RED)
        self.trade_tree.tag_configure("bot", foreground=MUTED)
        self.show_sizes()
        self.trade_tree.bind("<Double-1>", lambda e: self.show_trade_why(self._trade_rows[self.trade_tree.focus()])
                             if self.trade_tree.focus() in self._trade_rows else None)
        Tip(self.trade_tree, "Double-click a trade to see why it happened: which bots voted and what each one saw.")
        self.toggle_wild()
        self.refresh_trades()
        self.update_status()
        self.refresh_view()
        for seq, fn in (("<Control-Return>", self.toggle), ("<Control-l>", self.open_lab), ("<Control-h>", self.open_hof),
                        ("<Control-k>", self.open_coin_browser), ("<Control-b>", self.open_collection), ("<Control-e>", self.open_designer), ("<Control-comma>", self.open_settings),
                        ("<F5>", self.refresh_trades)):
            root.bind_all(seq, lambda e, f=fn: (f(), "break")[1])
        root.after(60, self.set_farm_style)

    def build_menu(self):
        bar = tk.Menu(self.root)
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label="Export trades CSV...", command=self.export_csv)
        m.add_command(label="Export bots...", command=self.export_bots)
        m.add_command(label="Import bots...", command=self.import_bots)
        m.add_command(label="Open data folder", command=self.open_data_folder)
        m.add_command(label="Back up everything...", command=self.backup_all)
        m.add_command(label="Restore from a backup...", command=self.restore_all)
        pm = tk.Menu(m, tearoff=0, postcommand=lambda: self.fill_profiles(pm))
        m.add_cascade(label="Settings profiles", menu=pm)
        m.add_command(label="Reset session", command=self.reset)
        m.add_separator()
        m.add_checkbutton(label="Dark mode", variable=self.dark_var, command=lambda: self.set_dark(self.dark_var.get()))
        self.compact_var = tk.BooleanVar(value=bool(self.cfg.get("compact", False)))
        m.add_checkbutton(label="Compact top bar", variable=self.compact_var, command=self.toggle_compact)
        m.add_command(label="Quit", command=self.on_close)
        bar.add_cascade(label="File", menu=m)
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label="Log in to Coinbase...", command=self.open_login)
        m.add_command(label="Log out", command=self.logout)
        bar.add_cascade(label="Account", menu=m)
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label="Backtest...", command=self.backtest)
        m.add_command(label="Bot Lab...", command=self.open_lab, accelerator="Ctrl+L")
        m.add_command(label="Hall of Fame...", command=self.open_hof, accelerator="Ctrl+H")
        m.add_command(label="Bot family tree...", command=self.open_family)
        m.add_command(label="Weekly report now...", command=self.show_report)
        m.add_command(label="Training camp (a whole farm through history)...", command=self.open_training)
        m.add_command(label="Free roam (the farm learns from the past by itself)...", command=self.open_roam)
        m.add_command(label="Backtest a bot...", command=lambda: self.open_backtest(None))
        m.add_command(label="Backtest the Hall of Fame...", command=self.backtest_hof)
        m.add_command(label="Compare two bots...", command=lambda: self.compare_bots())
        m.add_command(label="Bloodlines (drawn family tree)...", command=self.open_bloodlines)
        m.add_command(label="Farm replay...", command=self.open_replay)
        m.add_command(label="Design an egg...", command=self.open_designer, accelerator="Ctrl+E")
        m.add_command(label="Bot Library...", command=self.open_collection, accelerator="Ctrl+B")
        m.add_command(label="Browse Coinbase coins...", command=self.open_coin_browser, accelerator="Ctrl+K")
        m.add_command(label="Settings...", command=self.open_settings, accelerator="Ctrl+,")
        m.add_command(label="Install add-ons (setup)...", command=self.open_setup)
        m.add_command(label="Sell everything now", command=self.sell_all)
        m.add_command(label="KILL SWITCH (stop, cancel, sell all, lock Live)", command=self.kill_switch)
        m.add_separator()
        m.add_command(label="Check for updates", command=self.check_update)
        m.add_command(label="Undo last update", command=self.undo_update)
        bar.add_cascade(label="Tools", menu=m)
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label="Welcome screen", command=self.show_welcome)
        m.add_command(label="Take the tour", command=self.start_tour)
        m.add_command(label="How this works", command=self.show_guide)
        m.add_command(label="What do these words mean?", command=self.show_glossary)
        m.add_command(label="Keyboard shortcuts", command=self.show_shortcuts)
        m.add_separator()
        m.add_command(label="Diagnostics (copy for support)...", command=self.show_diagnostics)
        bar.add_cascade(label="Help", menu=m)
        self.root.config(menu=bar)

    def fit_and_center(self):
        """Opens centred on the screen, big enough to show all of the left-hand panel (never bigger than the screen)."""
        r = self.root
        try:
            r.update_idletasks()
            sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
            maxw, maxh = sw - 40, sh - 110  # leave room for the title bar and taskbar
            try:
                want_w, want_h = (int(x) for x in str(self.cfg.get("size", "")).split("x"))
            except ValueError:
                want_w = want_h = 0
            need_h = r.winfo_reqheight() + 12
            w = min(max(want_w, r.winfo_reqwidth(), 1050), maxw)
            h = min(max(want_h, need_h, 720), maxh)
            r.geometry(f"{w}x{h}+{max((sw - w) // 2, 0)}+{max((sh - h) // 2 - 20, 0)}")
            r.minsize(min(980, maxw), min(need_h, maxh))
        except Exception:
            pass
        r.deiconify()

    def setup_styles(self):
        st = ttk.Style()
        st.configure(".", background=BG, foreground=TEXT)
        st.configure("TFrame", background=BG)
        st.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
        st.map("Card.TCheckbutton", background=[("active", CARD)])
        st.configure("Seg.TRadiobutton", background=CARD, padding=(10, 4))
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("Treeview", rowheight=24, background=CARD, fieldbackground=CARD, borderwidth=0)
        st.configure("Treeview.Heading", background=LINE, relief="flat", font=("", 9, "bold"))
        st.configure("Accent.TButton", background=ACCENT, foreground="white", font=("", 11, "bold"), padding=8)
        st.map("Accent.TButton", background=[("active", "#1d4ed8")])
        st.configure("Stop.TButton", background=RED, foreground="white", font=("", 11, "bold"), padding=8)
        st.configure("Live.TButton", background=LIVE_BLUE, foreground="white", font=("", 11, "bold"), padding=8)
        st.map("Live.TButton", background=[("active", "#003ecb")])
        st.configure("Go.TButton", background="#16a34a", foreground="white", font=("", 11, "bold"), padding=8)
        st.map("Go.TButton", background=[("active", "#15803d")])
        st.map("Stop.TButton", background=[("active", "#b91c1c")])
        st.configure("Plain.TButton", padding=5)
        st.configure("Treeview", foreground=TEXT)
        st.configure("Treeview.Heading", foreground=TEXT)
        st.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        st.configure("TNotebook.Tab", background=LINE, foreground=TEXT)
        st.map("TNotebook.Tab", background=[("selected", CARD)])
        st.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT)
        st.configure("TSpinbox", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, background=LINE, arrowcolor=TEXT)
        st.map("TSpinbox", fieldbackground=[("readonly", CARD), ("disabled", CARD)], foreground=[("disabled", MUTED)])
        st.configure("TCombobox", fieldbackground=CARD, foreground=TEXT, background=LINE, arrowcolor=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)])
        st.configure("TButton", background=LINE, foreground=TEXT)
        st.map("TButton", background=[("active", "#4b5563" if DARK else "#d1d5db")])
        st.configure("Seg.TRadiobutton", foreground=TEXT)
        st.configure("TCheckbutton", foreground=TEXT)
        st.configure("TScrollbar", background=LINE, troughcolor=BG)

    def style_board_tags(self):
        amber = "#fbbf24" if DARK else "#b45309"
        for tag, color in (("win", GREEN), ("shaky", amber), ("standby", TEXT), ("stopped", MUTED), ("learning", MUTED)):
            self.board.tag_configure(tag, foreground=color)
        self.board.tag_configure("hof", font=("", 9, "bold"))

    def style_log_tags(self):
        self.style_board_tags()
        for tag, color in (("good", GREEN), ("bad", RED), ("warn", "#fbbf24" if DARK else "#b45309"), ("info", TEXT)):
            self.log.tag_configure(tag, foreground=color)
        self.trade_tree.tag_configure("buy", foreground=GREEN)
        self.trade_tree.tag_configure("sell", foreground=RED)
        self.trade_tree.tag_configure("bot", foreground=MUTED)

    def set_dark(self, dark):
        """Switches between dark and light mode immediately and remembers the choice."""
        if dark == DARK:
            return
        old, new = (DARK_P, LIGHT_P) if DARK else (LIGHT_P, DARK_P)
        mapping = {old[k].lower(): new[k] for k in old}
        for light_c, dark_c in EXTRA_TO_DARK.items():
            if dark:
                mapping[light_c] = dark_c
            else:
                mapping[dark_c] = light_c
        recolor(self.root, mapping)  # first: apply_theme sets the window background to its final colour itself
        apply_theme(self.root, dark)
        self.setup_styles()
        self.style_log_tags()
        self.cfg["dark"] = dark
        E.save_json(CONFIG_PATH, self.cfg)
        self.dark_var.set(dark)
        self.theme_btn.config(text="Light mode" if dark else "Dark mode")
        self.update_status()
        self.refresh_view()
        self.refresh_trades()
        self.grid_refresh()
        self._grid_paint_chips()
        self.paint_range_btns()

    @staticmethod
    def card(parent, title):
        f = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        inner = tk.Frame(f, bg=CARD, padx=10, pady=8)
        inner.pack(fill="both", expand=True)
        if title:
            tk.Label(inner, text=title, bg=CARD, fg=MUTED, font=("", 8, "bold")).pack(anchor="w", pady=(0, 4))
        return f, inner

    @staticmethod
    def make_tree(parent, cols, widths, heads=None):
        t = ttk.Treeview(parent, columns=cols, show="headings", height=8)
        sb = ttk.Scrollbar(parent, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        for i, (c, w) in enumerate(zip(cols, widths)):
            t.heading(c, text=heads[i] if heads else c.title(), command=lambda c=c: sort_tree(t, c))
            t.column(c, width=w, anchor="w" if c in ("strategy", "status", "time", "mode", "side", "why", "who") else "e")
        t.pack(side="left", fill="both", expand=True)
        return t

    # ---------------- cash currency ----------------
    def cur_quote(self):
        return self.quote if self.session else self.quote_var.get()

    def avail(self):
        return self.balances.get(self.cur_quote(), 0.0)

    def on_quote(self, _=None):
        self.cfg["quote"] = self.quote_var.get()
        E.save_json(CONFIG_PATH, self.cfg)
        if self.session:
            messagebox.showinfo("Cash currency", f"{self.quote_var.get()} will be used after you reset the session "
                                                 f"(File > Reset session).")
        else:
            self.usd_avail = self.avail()
        self.update_status()

    @staticmethod
    def read_balances(accts):
        out = {}
        for a in accts:
            try:
                out[a["currency"]] = out.get(a["currency"], 0.0) + float(a["available_balance"]["value"])
            except (KeyError, TypeError, ValueError):
                pass
        return out

    # ---------------- strategy size / random / save ----------------
    def bump_size(self, name, d):
        cur = self.settings["size_mult"].get(name, 1.0)
        self.settings["size_mult"][name] = round(min(max(cur + d, 0.25), 3.0), 2)
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)
        self.show_sizes()

    def show_sizes(self):
        for n, lbl in self.size_lbls.items():
            lbl.config(text=f"{self.settings['size_mult'].get(n, 1.0):.2f}x")

    def selected_variant(self):
        sel = self.board.selection()
        if not self.tourn or not sel:
            return None
        return next((v for v in self.tourn.vars if str(id(v)) == sel[0]), None)

    def bump_variant(self, d):
        v = self.selected_variant()
        if not v:
            return messagebox.showinfo("Strategy size", "Click a strategy in the scoreboard first.")
        v.size_mult = round(min(max(v.size_mult + d, 0.25), 3.0), 2)
        self.tourn._rate()
        self.refresh_board()
        self.write(f"{friendly_label(v.name, v.params)} trade size is now {v.size_mult:.2f}x its normal size.")

    def flush_events(self):
        for e in self.tourn.pop_events():
            self.write("Wildcard: " + friendly_text(e[0].lower() + e[1:]))

    def new_random(self):
        if not self.tourn or not self.seeded:
            return messagebox.showinfo("New strategy", "Start the bot with 'Let the bot choose (Wildcard)' ticked first.")
        self.tourn.spawn_random({c: self.hist[c] for c in self.session_coins})
        self.flush_events()
        self.refresh_board()

    def save_lineup(self):
        if not self.tourn:
            return messagebox.showinfo("Save", "Nothing to save yet. Start the bot with Wildcard ticked.")
        lineup = self.tourn.export_lineup()
        E.save_strategies(lineup)
        self.write(f"Saved {len(lineup)} strategies to {E.strategies_path()}")

    def selected_variants(self):
        ids = set(self.board.selection())
        if not self.tourn or not ids:
            return []
        return [v for v in self.tourn.vars if str(id(v)) in ids]

    def export_bots(self):
        """Export window: choose which bots (all, sections, Hall of Fame, Library or your selection)."""
        w = tk.Toplevel(self.root)
        w.title("Export bots")
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack()
        ttk.Label(f, text="Export bots", font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(f, foreground=MUTED, wraplength=440, text=(
            "The file is plain JSON. Besides each bot's settings and results it spells out its rules in plain English "
            "and explains every setting, so another app or an AI can read and rebuild it. No keys or balances are included.")
        ).pack(anchor="w", pady=(2, 10))
        board = self.tourn.board() if self.tourn else []
        tiers = [(r, self.tier(r)) for r in board]
        sel_ids = {id(v) for v in self.selected_variants()}
        scopes = [
            ("selected", f"The bots I selected ({len(sel_ids)})", [r for r in board if id(r["v"]) in sel_ids]),
            ("all", f"All bots on the farm ({len(board)})", board),
            ("beasts", f"BEASTS only ({sum(1 for _, t in tiers if t == 'beast')})", [r for r, t in tiers if t == "beast"]),
            ("rising", f"Rising ({sum(1 for _, t in tiers if t == 'rising')})", [r for r, t in tiers if t == "rising"]),
            ("eggs", f"Eggs ({sum(1 for _, t in tiers if t == 'egg')})", [r for r, t in tiers if t == "egg"]),
            ("good", f"Everything that isn't resting ({sum(1 for r in board if r['status'] != 'benched')})",
             [r for r in board if r["status"] != "benched"]),
            ("hof", f"My Hall of Fame ({len(self.hof())})", self.hof()),
            ("library", f"My Bot Library ({len(self.library())})", self.library()),
        ]
        first = next((k for k, _, it in scopes if it), "hof")
        choice = tk.StringVar(value=first)
        for key, label, items in scopes:
            ttk.Radiobutton(f, text=label, value=key, variable=choice,
                            state="normal" if items else "disabled").pack(anchor="w")
        also_txt = tk.BooleanVar(value=bool(self.cfg.get("export_txt", False)))
        ttk.Checkbutton(f, text="Also save a plain-text summary (.txt) next to it", variable=also_txt).pack(anchor="w", pady=(10, 0))

        def go():
            items = dict((k, it) for k, _, it in scopes)[choice.get()]
            if not items:
                return messagebox.showinfo("Export bots", "There are no bots in that group yet.", parent=w)
            p = filedialog.asksaveasfilename(title="Export bots", defaultextension=".json", parent=w,
                                             initialfile=f"cbbf-{choice.get()}-bots.json", filetypes=[("Bot file", "*.json")])
            if not p:
                return
            rows = [dict(r, bar_minutes=self.session_bar) if "v" in r else r for r in items]
            payload = E.export_bots(rows, self.version)
            E.save_bots_file(p, payload)
            self.cfg["export_txt"] = also_txt.get()
            if also_txt.get():
                txt = os.path.splitext(p)[0] + ".txt"
                with open(txt, "w", encoding="utf-8") as fh:
                    fh.write(f"CBBF bots, exported {payload['exported_at']}\n\n{payload['how_to_read']}\n\n")
                    for n, b in enumerate(payload["bots"], 1):
                        lt = b.get("lifetime") or {}
                        fh.write(f"{n}. {E.bot_nickname(E.bot_key(b))}\n   {b['rules']}\n")
                        if lt:
                            fh.write(f"   Results: return {lt.get('return_pct')}%, vs holding {lt.get('vs_holding_pct')}%, "
                                     f"win rate {lt.get('win_rate_pct')}%, fees ${lt.get('fees_paid', '-')}\n")
                        fh.write("\n")
            self.write(f"Exported {len(payload['bots'])} bots to {p}" + (" (+ .txt summary)" if also_txt.get() else ""))
            w.destroy()
        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(12, 0))
        ttk.Button(b, text="Cancel", command=w.destroy).pack(side="left", padx=6)
        ttk.Button(b, text="Export...", style="Accent.TButton", command=go).pack(side="left")

    def import_bots(self):
        if not self.tourn or not self.seeded:
            return messagebox.showinfo("Import bots", "Start the bot with Wildcard ticked first, then import.")
        paths = filedialog.askopenfilenames(title="Import bots", filetypes=[("Bot file", "*.json"), ("All files", "*.*")])
        if not paths:
            return
        closes = {c: self.hist[c] for c in self.session_coins}
        added = 0
        for p in paths:
            bots, problems = E.read_bots_file(p)
            for msg in problems:
                self.write(f"{os.path.basename(p)}: {msg}", "warn")
            for d in bots:
                if any(v.name == d["name"] and v.tf == d.get("tf", 1)
                       and all(abs(float(v.params.get(k, 0)) - x) < 1e-9 for k, x in d["params"].items())
                       for v in self.tourn.vars):
                    self.write(f"Skipped {friendly_label(d['name'], d['params'])}: you already have it.", "warn")
                    continue
                if self.tourn.add_variant(d["name"], d["params"], closes, d["size"], note="Imported", tf=d.get("tf", 1),
                                          risk=d.get("risk"), traits=d.get("traits"), vetoes=d.get("vetoes"), layers=d.get("layers"), origin="imported") is None:
                    continue
                added += 1
                s = d.get("stats") or {}
                if s.get("recent_gain_pct") is not None:
                    self.write(f"Imported {friendly_label(d['name'], d['params'])}: it made {s['recent_gain_pct']:+.1f}% "
                               f"on its owner's prices. Now it has to prove itself on yours.")
        self.flush_events()
        self.refresh_board()
        if added:
            self.write(f"Imported {added} bot{'s' if added != 1 else ''}. They compete like any other strategy and only get followed if they do well on your prices.")
        else:
            messagebox.showinfo("Import bots", "No usable bots were found in that file.")

    def load_lineup(self):
        if not self.tourn or not self.seeded:
            return messagebox.showinfo("Load", "Start the bot with Wildcard ticked first.")
        saved = E.load_strategies()
        if not saved:
            return messagebox.showinfo("Load", "No saved strategies yet. Use 'Save lineup' first.")
        closes = {c: self.hist[c] for c in self.session_coins}
        for d in saved:
            self.tourn.add_variant(d["name"], d["params"], closes, d.get("size", 1.0), note="Loaded saved", tf=d.get("tf", 1),
                                   risk=d.get("risk"), traits=d.get("traits"), vetoes=d.get("vetoes"), layers=d.get("layers"), origin="saved")
        self.flush_events()
        self.refresh_board()

    # ---------------- trades list / live prices ----------------
    def refresh_trades(self):
        """Refresh the combined paper/live ledger, tolerating older or partially-written rows."""
        rows, seen = [], set()

        def add_rows(items):
            if not isinstance(items, (list, tuple)):
                return
            for r in items:
                if not isinstance(r, dict):
                    continue
                key = tuple(str(r.get(k, "")) for k in
                            ("time", "mode", "side", "coin", "qty", "price", "usd", "pl", "reason", "who"))
                if key not in seen:
                    seen.add(key)
                    rows.append(r)

        # Durable ledger first, then recover trades from both saved mode sessions.
        ledger = os.path.join(E.HOME_DIR, "trades.csv")
        try:
            with open(ledger, newline="", encoding="utf-8-sig") as f:
                add_rows(csv.DictReader(f))
        except (OSError, csv.Error, UnicodeError):
            pass
        for is_live in (False, True):
            saved = E.load_json(E.state_path(is_live), {})
            if isinstance(saved, dict):
                add_rows(saved.get("trades", []))
        add_rows(self.trades)
        show_bots = True
        try:
            show_bots = bool(self.show_bots_var.get())
        except Exception:
            pass
        if show_bots:
            add_rows(self.shadow_trades)

        def number(value, default=0.0):
            try:
                result = float(value)
                return result if math.isfinite(result) else default
            except (TypeError, ValueError, OverflowError):
                return default

        t = self.trade_tree
        t.delete(*t.get_children())
        self._trade_rows = {}
        rows.sort(key=lambda item: str(item.get("time", "")), reverse=True)
        if hasattr(self, "trade_count_lbl"):
            paper_n = sum(str(r.get("mode", "")).lower() == "paper" for r in rows)
            live_n = sum(str(r.get("mode", "")).lower() == "live" for r in rows)
            bot_n = sum(str(r.get("mode", "")).lower() == "bot" for r in rows)
            self.trade_count_lbl.config(
                text=f"{len(rows)} trades  |  Your practice account: {paper_n}  |  Live: {live_n}  |  Bot tests: {bot_n}")
        for r in rows[:1000]:
            side = str(r.get("side", "")).lower()
            pl = r.get("pl")
            try:
                is_bot = str(r.get("mode", "")).lower() == "bot"
                iid = t.insert("", "end", tags=("bot",) if is_bot else ("buy" if side == "buy" else "sell",), values=(
                    str(r.get("time", "")).replace("T", " "), str(r.get("mode", "")).upper(),
                    side.upper() if side in ("buy", "sell") else str(r.get("side", "")).upper(),
                    r.get("coin", ""),
                    f"{number(r.get('qty')):.6f}".rstrip("0").rstrip("."),
                    f"{number(r.get('usd')):,.2f}", f"{number(r.get('price')):,.2f}",
                    "" if pl in ("", None) else f"{number(pl):+,.2f}", r.get("who", ""),
                    REASON_TEXT.get(r.get("reason"), r.get("reason", ""))))
                self._trade_rows[iid] = r
            except Exception:  # one odd row must not hide all the others
                continue
        resort(t)

    def refresh_prices(self):
        t = self.prices_tree
        t.delete(*t.get_children())
        stamp = self.last_tick.strftime("%H:%M:%S") if self.last_tick else "-"
        nxt = ""
        if self.last_tick and self.running and self.seeded:
            left = max(0, int(self.last_bar + self.session_bar * 60 - time.time()))
            nxt = f"  |  next decision in {left // 60}:{left % 60:02d}"
        self.tick_lbl.config(text=f"Prices updated {stamp}{nxt}" if self.last_tick else "")
        n1, n24 = max(1, int(60 // self.session_bar)), int(1440 // self.session_bar)

        def chg(px, ref):
            return "-" if not ref else f"{(px / ref - 1) * 100:+.2f}%"
        for c in (self.session_coins or [k for k, v in self.coin_vars.items() if v.get()]):
            px = self.last.get(c)
            if px is None:
                continue
            h = self.hist.get(c, [])
            mood, er, _ = E.market_mood(h)
            mood_txt = MOOD_TEXT.get(mood, mood) + (f"  ({er:.2f})" if mood != "unknown" else "")
            t.insert("", "end", values=(
                c, f"{px:,.6f}" if px < 1 else f"{px:,.4f}" if px < 10 else f"{px:,.2f}", chg(px, self.start_px.get(c)),
                chg(px, h[-n1 - 1] if len(h) > n1 else None), chg(px, h[-n24 - 1] if len(h) > n24 else None),
                mood_txt, stamp))

    def toggle_more(self):
        on = self.more_var.get()
        self.settings["winners_trade_more"] = on
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)
        if self.tourn:
            self.tourn.o["trade_more"] = on
        self.write("Winners will now trade more." if on else "Winners will trade at their normal pace.")

    def toggle_wild(self):
        on = self.wild_var.get()
        for b in self.strat_btns:
            b.config(state="disabled" if on else "normal")
        if on:
            self.strat_box.pack_forget()
            self.wild_note.pack(anchor="w", pady=(4, 0))
        else:
            self.wild_note.pack_forget()
            self.strat_box.pack(fill="x")

    # ---------------- coins ----------------
    def all_coins(self):
        out = list(COINS)
        for c in self.settings.get("extra_coins", []):
            if c not in out:
                out.append(c)
        return out

    def build_coin_grid(self):
        for w in self.coin_grid.winfo_children():
            w.destroy()
        for i, coin in enumerate(self.all_coins()):
            v = self.coin_vars.setdefault(coin, tk.BooleanVar(value=False))
            cb = ttk.Checkbutton(self.coin_grid, text=coin, variable=v, style="Card.TCheckbutton", command=self.save_ticked)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 10), pady=1)

    def save_ticked(self):
        self.cfg["ticked_coins"] = [c for c, v in self.coin_vars.items() if v.get()]
        try:
            E.save_json(CONFIG_PATH, self.cfg)
        except OSError:
            pass

    def toggle_auto_coins(self):
        n = int(self.settings.get("auto_coins", 0))
        if self.auto_coin_var.get():
            self.settings["auto_coins"] = n if n > 0 else int(self.cfg.get("last_auto_coins", 4))
            if not self.wild_var.get():
                self.wild_var.set(True)
                self.toggle_wild()
            self.write(f"The bot will pick its own {self.settings['auto_coins']} coins when you press Start (Wildcard).")
        else:
            if n > 0:
                self.cfg["last_auto_coins"] = n
            self.settings["auto_coins"] = 0
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)

    def add_coins(self, coins):
        added = []
        for c in coins:
            c = c.strip().upper()
            if E.valid_coin(c) and c not in self.all_coins():
                self.settings["extra_coins"].append(c)
                added.append(c)
        for c in coins:
            c = c.strip().upper()
            if c in self.all_coins():
                self.coin_vars.setdefault(c, tk.BooleanVar()).set(True)
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)
        self.build_coin_grid()
        self.save_ticked()
        return added

    def open_coin_browser(self):
        if getattr(self, "coin_win", None) is not None and self.coin_win.winfo_exists():
            return self.coin_win.lift()
        w = self.coin_win = tk.Toplevel(self.root)
        w.title("Coinbase coins")
        w.geometry("720x560")
        top = ttk.Frame(w, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Every coin Coinbase trades for " + self.cur_quote(), font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(top, foreground=MUTED, wraplength=680, text=(
            "Busiest first. Tick the ones you want and press Add. Small coins with little volume have wider "
            "spreads and jump around more, which can eat into profits.")).pack(anchor="w", pady=(2, 6))
        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="Search").pack(side="left")
        q = tk.StringVar()
        ttk.Entry(row, textvariable=q, width=16).pack(side="left", padx=6)
        status = ttk.Label(row, text="Loading the coin list...", foreground=MUTED)
        status.pack(side="left", padx=8)
        mid = ttk.Frame(w, padding=(10, 0))
        mid.pack(fill="both", expand=True)
        t = self.make_tree(mid, ("coin", "price", "chg", "vol", "have"), (80, 120, 100, 150, 120),
                           ("Coin", "Price", "24h change", "24h volume ($)", "In your list"))
        t.configure(selectmode="extended")
        data = {"all": []}

        def fill(*_):
            t.delete(*t.get_children())
            needle = q.get().strip().upper()
            mine = set(self.all_coins())
            for d in data["all"]:
                if needle and needle not in d["coin"]:
                    continue
                pr = d["price"]
                t.insert("", "end", iid=d["coin"], values=(
                    d["coin"], "-" if pr is None else (f"{pr:,.6f}" if pr < 1 else f"{pr:,.2f}"),
                    "-" if d["change_24h"] is None else f"{d['change_24h']:+.2f}%",
                    "-" if d["volume_usd"] is None else f"{d['volume_usd']:,.0f}",
                    "yes" if d["coin"] in mine else ""))
            resort(t)
        q.trace_add("write", fill)

        def loaded(items, err=None):
            if not w.winfo_exists():
                return
            if err:
                status.config(text=f"Couldn't load the list: {err}", foreground=RED)
                return
            data["all"] = items
            status.config(text=f"{len(items)} coins")
            fill()

        def work():
            try:
                items = E.list_products(self.cur_quote())
                self.q.put(("call", lambda: loaded(items)))
            except Exception as ex:
                msg = str(ex)
                self.q.put(("call", lambda: loaded([], msg)))
        threading.Thread(target=work, daemon=True).start()

        def add_sel():
            sel = list(t.selection())
            if not sel:
                return messagebox.showinfo("Coins", "Select one or more coins first (Ctrl/Shift-click for several).", parent=w)
            added = self.add_coins(sel)
            self.write(f"Added {', '.join(added)} to your coins." if added else f"Ticked {', '.join(sel)}.")
            fill()

        def remove_sel():
            gone = [c for c in t.selection() if c in self.settings["extra_coins"]]
            if not gone:
                return messagebox.showinfo("Coins", "Select coins you added yourself. The 10 built-in coins stay.", parent=w)
            if self.session and set(gone) & set(self.session_coins):
                return messagebox.showinfo("Coins", "That coin is in the running session. Reset the session first.", parent=w)
            self.settings["extra_coins"] = [c for c in self.settings["extra_coins"] if c not in gone]
            for c in gone:
                self.coin_vars.pop(c, None)
            self.cfg["settings"] = self.settings
            E.save_json(CONFIG_PATH, self.cfg)
            self.build_coin_grid()
            self.save_ticked()
            fill()

        def bot_pick():
            if not data["all"]:
                return
            n = max(2, int(self.settings.get("auto_coins") or self.cfg.get("last_auto_coins", 4)))
            picks = E.scout_coins(data["all"], n)
            if not picks:
                return messagebox.showinfo("Coins", "No coin has enough volume right now.", parent=w)
            for c, v in self.coin_vars.items():
                v.set(False)
            self.add_coins(picks)
            self.write(f"The bot picked {', '.join(picks)} (busy coins that are moving). They are ticked now.")
            t.selection_set([c for c in picks if t.exists(c)])
            fill()
        b = ttk.Frame(w, padding=10)
        b.pack(fill="x")
        ttk.Button(b, text="Add / tick selected", style="Accent.TButton", command=add_sel).pack(side="left")
        ttk.Button(b, text="Remove selected", style="Plain.TButton", command=remove_sel).pack(side="left", padx=6)
        bp = ttk.Button(b, text="Let the bot pick now", style="Plain.TButton", command=bot_pick)
        bp.pack(side="left")
        Tip(bp, "Ticks the busiest coins that are actually moving today (stablecoins skipped).")
        ttk.Button(b, text="Close", command=w.destroy).pack(side="right")
        t.bind("<Double-1>", lambda e: add_sel() if t.identify_region(e.x, e.y) in ("cell", "tree") else None)

    def ui_running(self, on):
        self.root.after(50, self.grid_refresh)
        live = self.live if self.session else self.mode_var.get() == "live"
        self.btn.config(text=("Stop the LIVE bot" if live else "Stop the practice bot") if on else
                        ("Start LIVE bot (real money)" if live else "Start practice bot"),
                        style="Stop.TButton" if on else ("Live.TButton" if live else "Go.TButton"))
        if not on:
            self._gap_ref = None
        self.apply_awake()
        self.update_status()

    def apply_awake(self):
        if self.running and self.settings.get("keep_awake", True):
            self.awake.on()
        else:
            self.awake.off()

    def update_status(self):
        live = self.live if self.session else self.mode_var.get() == "live"
        self.mode_pill.config(text="\u25cf  RUNNING" if self.running else "", fg=LIVE_BLUE if live else GREEN)
        on_p, on_l = not live, live
        self.seg_paper.config(bg=GREEN if on_p else LINE, fg="white" if on_p else MUTED,
                              text="PRACTICE" + ("  \u25cf" if on_p and self.running else ""))
        self.seg_live.config(bg=LIVE_BLUE if on_l else LINE, fg="white" if on_l else MUTED,
                             text="LIVE  \u2022  REAL MONEY" + ("  \u25cf" if on_l and self.running else "") if on_l else "LIVE")
        self.mode_stripe.config(bg=LIVE_BLUE if live else GREEN, height=6 if live else 4)
        self.body.config(highlightthickness=3 if live else 0, highlightbackground=LIVE_BLUE, highlightcolor=LIVE_BLUE)
        if live:
            self.headline_lbl.config(bg="#0a1f4d" if DARK else "#eff6ff", fg="#bfdbfe" if DARK else "#0b3aa8")
        else:
            self.headline_lbl.config(bg="#052e16" if DARK else "#ecfdf5", fg="#bbf7d0" if DARK else "#065f46")
        self.mode_desc.config(text=("LIVE: real money on your Coinbase account." if live else
                                    "PRACTICE: pretend money on real prices."), fg=LIVE_BLUE if live else GREEN)
        if not self.running:
            saved = None if self.session else self.saved_session(live)
            if self.session:
                txt = "Continue LIVE bot (real money)" if live else "Continue practice bot"
            elif saved:
                txt = (f"Continue {'LIVE' if live else 'practice'} session  "
                       f"(${float(saved.get('cash', 0)) + 0:,.0f} cash, {len(saved.get('trades', []))} trades)")
            else:
                txt = "Start LIVE bot (real money)" if live else "Start practice bot"
            self.btn.config(text=txt, style="Live.TButton" if live else "Go.TButton")
            if saved and not self.session:
                self.new_btn.config(text=f"Start a new {'LIVE' if live else 'practice'} session")
                if not self.new_btn.winfo_ismapped():
                    self.new_btn.pack(fill="x", pady=(0, 4), after=self.btn)
            elif self.new_btn.winfo_ismapped():
                self.new_btn.pack_forget()
        elif self.new_btn.winfo_ismapped():
            self.new_btn.pack_forget()
        if self.logging_in:
            self.conn_lbl.config(text="Logging in...", fg=MUTED)
        elif self.client:
            self.conn_lbl.config(text=f"Coinbase connected  |  {self.cur_quote()} available ${self.avail():,.2f}", fg=GREEN)
        else:
            self.conn_lbl.config(text="Not logged in" + ("  (needed for Live)" if live else ""), fg=MUTED)
        self.login_btn.config(text="Log out" if self.client else "Log in to Coinbase")
        self.budget_lbl.config(text=f"Money to trade with ({self.cur_quote()})" if live else f"Practice money (pretend {self.cur_quote()})")

    def saved_session(self, live=None):
        """The saved session for a mode (cached; re-read only when the file changes), or None."""
        if live is None:
            live = self.mode_var.get() == "live"
        path = E.state_path(live)
        return self._cached(path, lambda: E.load_json(path)) if os.path.exists(path) else None

    def start_choice(self, choice):
        """Start button: continue the saved session (or start one), or start a new one. While running it stops."""
        if self.running:
            return self.toggle()
        self._choice = choice
        try:
            self.toggle()
        finally:
            self._choice = "continue"

    def switch_mode(self, target):
        """Practice and Live are separate: each keeps its own session. Switching saves and closes the current
        one, clears the screen, then shows the other mode (press Start to resume its saved session)."""
        cur = "live" if (self.live if self.session else self.mode_var.get() == "live") else "paper"
        if target == cur:
            return
        name = {"paper": "Practice", "live": "LIVE"}
        if target == "live" and not messagebox.askyesno(
                "Switch to LIVE?", "LIVE trades REAL money on your Coinbase account.\n\nSwitch to LIVE mode?\n"
                                   "(No = stay in Practice. Nothing real happens until you also press Start and type LIVE.)"):
            self.write("Stayed in Practice.", "good")
            return
        if self.running and not messagebox.askyesno(
                "Switch mode", f"The {name[cur]} bot is running. Stop it and switch to {name[target]}?\n\n"
                               f"Your {name[cur]} session is saved and can be resumed later. Open positions stay open."):
            return
        if self.running:
            self.running = False
            self.ui_running(False)
        if self.session:
            self.save_state()
            self.save_farm(force=True)
            self.write(f"{name[cur]} session saved.")
        self.session = False
        try:
            cash = float(self.cash_var.get())
        except ValueError:
            cash = 1000.0
        self.reset_state(cash)
        self.live = target == "live"
        self.mode_var.set(target)
        self.refresh_board()
        self.refresh_trades()
        self.refresh_view()
        saved = E.load_json(E.state_path(target == "live"))
        if saved:
            self.write(f"Now in {name[target]}. A saved {name[target]} session is waiting (cash ${saved.get('cash', 0):,.2f}, "
                       f"{len(saved.get('trades', []))} trades): press Start to resume it.", "good" if target == "paper" else "warn")
        else:
            self.write(f"Now in {name[target]}.", "good" if target == "paper" else "warn")
        self.live = False  # no session yet; the mode switch decides what Start opens
        self.on_mode_change()

    def on_mode_change(self):
        self.update_team_label()
        if self.mode_var.get() == "live" and not self.client and not self.session:
            if messagebox.askyesno("Live mode", "Live mode trades with REAL money and needs your Coinbase login.\n\n"
                                                "Log in now? (You can stay in Practice mode instead.)"):
                self.open_login()
        self.update_status()

    def apply_style(self, name):
        if name in E.STYLES:
            self.settings.update(E.STYLES[name])
            self.settings["style"] = name
            self.style_var.set(name)
            self.cfg["settings"] = self.settings
            E.save_json(CONFIG_PATH, self.cfg)

    def on_style(self, _=None):
        name = self.style_var.get()
        if name in E.STYLES:
            self.apply_style(name)
            self.write(f"Risk style set to {name}: {STYLE_TIPS[name]}")

    def headline(self):
        live = self.live if self.session else self.mode_var.get() == "live"
        mode = "LIVE mode (real money)" if live else "Practice mode (pretend money)"
        if not self.running:
            if not self.session:
                return f"Ready. You're in {mode}. Pick your coins, then press Start."
            return f"Paused in {mode}. Press Start to continue."
        if not self.seeded:
            return "Loading recent price history..."
        text = f"{mode}: watching {', '.join(self.session_coins)}."
        if self.wild and self.wild_msg:
            text += " " + self.wild_msg
        if self.trades:
            t = self.trades[-1]
            text += f" Last action: {'bought' if t['side'] == 'buy' else 'sold'} {t['coin']} ({t['time'][11:16]})."
        else:
            text += " No trades yet. The bot only trades when a strategy sees a good moment, so waiting is normal."
        return text

    def write(self, msg, tag=None):
        if tag is None:
            low = msg.lower()
            tag = ("bad" if low.startswith(("halted", "order failed", "login failed", "stopped after")) or "lost $" in low
                   else "good" if "bought" in low or "made $" in low
                   else "warn" if "can't get prices" in low or "retired" in low or "no strategy" in low
                   else "info")
        try:
            path = os.path.join(E.HOME_DIR, "activity.log")
            os.makedirs(E.HOME_DIR, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
                os.replace(path, path + ".old")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
        except Exception:
            pass
        self.log.config(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M}  {msg}\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def notify(self, title, msg, important=True, sound="notify"):
        """Desktop notification. Routine events (every trade) only notify if 'notify_trades' is on, the
        system sound only plays if 'sound' is on, and the mute button in the top bar silences everything."""
        if not self.settings.get("notify") or self.cfg.get("muted"):
            return
        if not important and not self.settings.get("notify_trades", False):
            return
        try:
            if self.settings.get("chime", True) and sound not in (self.settings.get("sounds_off") or []):
                E.play_sound(sound, self.settings.get("chime_volume", 60))
            msg = msg.replace('"', "'")
            if sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", f'display notification "{msg}" with title "{title}"'])
            elif sys.platform.startswith("linux") and shutil.which("notify-send"):
                subprocess.Popen(["notify-send", title, msg])
            elif sys.platform.startswith("win"):
                self.toast(title, msg)
        except Exception:
            pass

    def toast(self, title, msg, ms=6000):
        """A small silent pop-up in the corner of the screen (Windows), gone after 6 seconds."""
        try:
            w = tk.Toplevel(self.root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            f = tk.Frame(w, bg=CARD, highlightthickness=1, highlightbackground=ACCENT, padx=12, pady=8)
            f.pack()
            tk.Label(f, text=title, bg=CARD, fg=TEXT, font=("", 9, "bold")).pack(anchor="w")
            tk.Label(f, text=msg, bg=CARD, fg=MUTED, wraplength=280, justify="left").pack(anchor="w")
            w.update_idletasks()
            sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
            w.geometry(f"+{sw - w.winfo_reqwidth() - 20}+{sh - w.winfo_reqheight() - 70}")
            w.bind("<Button-1>", lambda e: w.destroy())
            w.after(ms, w.destroy)
        except Exception:
            pass

    def toggle_mute(self):
        self.cfg["muted"] = not self.cfg.get("muted", False)
        E.save_json(CONFIG_PATH, self.cfg)
        self.mute_btn.config(text="\u266a off" if self.cfg["muted"] else "\u266a")
        self.write("Notifications muted." if self.cfg["muted"] else "Notifications on.")

    # ---------------- help / welcome ----------------
    def info_window(self, title, text):
        w = tk.Toplevel(self.root)
        w.title(title)
        t = tk.Text(w, width=78, height=24, wrap="word", padx=12, pady=10, relief="flat")
        t.insert("1.0", text)
        t.config(state="disabled")
        t.pack(padx=6, pady=6)
        ttk.Button(w, text="Close", command=w.destroy).pack(pady=(0, 8))

    def show_guide(self):
        self.info_window("How this works", (
            "THE SHORT VERSION\n"
            "The bot watches crypto prices and buys or sells automatically using strategies. "
            "You start in Practice mode, which uses pretend money, so you can't lose anything while you learn.\n\n"
            "GETTING STARTED\n"
            "1. Tick the coins you want to watch (Bitcoin and Ethereum are ticked by default).\n"
            "2. Leave 'Let the bot choose (Wildcard)' ticked, or pick strategies yourself.\n"
            "3. Pick a Risk style. Cautious = small trades, Adventurous = big trades.\n"
            "4. Press Start. The blue line on the chart is your bot, the gray line is what you'd have if you "
            "just bought and held.\n\n"
            "WHAT TO EXPECT\n"
            "Long quiet stretches are normal. Check the blue banner at the top, it always says in plain "
            "English what the bot is doing, and the line under the numbers tells you if you're ahead of just "
            "holding.\n\n"
            "WINNERS TRADE MORE\n"
            "In Wildcard mode, strategies that keep winning get bigger trades and keep adding while they hold "
            "a coin. Shaky ones are slowed down and failing ones are stopped. That can help in steady "
            "markets, but every trade costs a fee, so untick 'Winners trade more' if you want calmer trading.\n\n"
            "GOING LIVE (REAL MONEY)\n"
            "Only after practicing for a while. Choose Live, log in with a Coinbase API key (View + Trade "
            "permissions only), and type LIVE to confirm. Start with a small amount.\n\n"
            "HONEST WARNING\n"
            "No strategy wins reliably, and fees eat into frequent trading. If the blue line stays below the "
            "gray line, the bot isn't beating simply holding. Use Tools > Backtest to check on real history."))

    def show_glossary(self):
        self.info_window("What do these words mean?", (
            "Practice mode: pretend money, nothing real happens.\n"
            "Live mode: real money on your Coinbase account.\n"
            "Wildcard: the bot tests every strategy in the background and mostly follows the winners.\n"
            "Winning: the strategy made money AND did it steadily. The bot copies it with bigger trades, and "
            "with 'Winners trade more' on it keeps adding to the coin while that strategy is still holding it.\n"
            "Shaky: the strategy made money but not steadily. The bot copies it with small trades, and only "
            "every so often.\n"
            "Stopped: the strategy is losing, or dropped fast. The bot ignores it. If it stays stopped it is "
            "retired and replaced by a tweaked copy of a winner.\n"
            "On standby: a good strategy that just didn't make the top group this round.\n"
            "Trade size (e.g. 1.8x): how big the bot's trades are when following that strategy. Steadier "
            "winners get bigger trades, up to 2.5x by default.\n"
            "Consistent: how many recent stretches the strategy made money in. 75% means 3 of the last 4.\n"
            "Safety stop (stop-loss): automatically sells a coin if it drops by a set percent.\n"
            "Lock in profit (take-profit): automatically sells a coin if it rises by a set percent.\n"
            "Daily loss limit: pauses the bot if you lose a set percent in one day.\n"
            "Decision / bar: how often strategies look at prices and decide (default every minute).\n"
            "Buy & hold: the gray line. What you'd have if you bought once and did nothing.\n"
            "Backtest: replays a strategy on past prices to see how it would have done. Past results don't "
            "predict the future.\n"
            "Drawdown: the biggest drop from a peak, a measure of how bumpy the ride was."))

    def maybe_welcome(self):
        if not self.cfg.get("welcomed"):
            self.show_welcome()

    def show_welcome(self):
        w = tk.Toplevel(self.root)
        w.title("Welcome")
        w.bind("<Destroy>", lambda e: self.root.after(700, self.maybe_tour) if e.widget is w else None)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=18)
        f.pack()
        ttk.Label(f, text="Welcome to CBBF, the Coinbase Bot Farm", font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(f, wraplength=440, text=(
            "This app watches crypto prices and trades for you using strategies. You'll start in Practice mode "
            "with pretend money, so nothing real is at risk. Real trading only happens if you switch to Live "
            "and log in.")).pack(anchor="w", pady=(6, 12))
        ttk.Label(f, text="How much pretend money do you want to practice with?").pack(anchor="w")
        cash = tk.StringVar(value="1000")
        ttk.Entry(f, textvariable=cash, width=14).pack(anchor="w", pady=(2, 10))
        ttk.Label(f, text="What kind of trader should it be?").pack(anchor="w")
        style = tk.StringVar(value="Balanced")
        for name, tip in STYLE_TIPS.items():
            ttk.Radiobutton(f, text=f"{name}: {tip}", variable=style, value=name).pack(anchor="w")
        wild = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Let the bot choose its own strategies (recommended)", variable=wild).pack(
            anchor="w", pady=(10, 12))

        def go():
            try:
                assert float(cash.get()) > 0
            except Exception:
                return messagebox.showerror("Welcome", "Please type an amount of money, like 1000.", parent=w)
            self.cash_var.set(cash.get())
            self.apply_style(style.get())
            self.wild_var.set(wild.get())
            self.toggle_wild()
            self.cfg["welcomed"] = True
            E.save_json(CONFIG_PATH, self.cfg)
            w.destroy()
            if not self.running:
                self.toggle()

        def later():
            self.cfg["welcomed"] = True
            E.save_json(CONFIG_PATH, self.cfg)
            w.destroy()
        row = ttk.Frame(f)
        row.pack(anchor="e")
        ttk.Button(row, text="Look around first", command=later).pack(side="left", padx=6)
        ttk.Button(row, text="Start practicing", style="Accent.TButton", command=go).pack(side="left")

    # ---------------- chart / views ----------------
    # ---------------- chart ----------------
    CHART_RANGES = (("30 ticks", -30), ("2 min", 120), ("5 min", 300), ("15 min", 900), ("1 hr", 3600),
                    ("4 hr", 14400), ("1 day", 86400), ("1 wk", 604800), ("All", 0))
    CANDLE_CHOICES = {"Auto": 0, "5 s": 5, "15 s": 15, "1 min": 60, "5 min": 300, "15 min": 900, "1 hr": 3600, "4 hr": 14400}
    CHART_TYPES = ("Line", "Area", "Candles")
    CANDLE_SECS = (5, 10, 15, 30, 60, 120, 300, 900, 1800, 3600, 7200, 14400)

    LAYOUTS = {"1 chart": (1, 1), "2 charts": (1, 2), "3 charts": (1, 3), "4 charts (2x2)": (2, 2), "6 charts (3x2)": (2, 3)}
    PANEL_VIEWS = ("Account value", "Profit / loss %", "Drawdown", "Cash vs invested", "Selected bot", "Hall of Fame shadow")
    PANEL_TIPS = {"Hall of Fame shadow": "Shadow Live: what your Hall of Fame bots alone would have made with pretend "
                                         "money (gold), next to your account (blue).",
                  "Account value": "Your account's value (blue) next to what just holding would be worth (grey).",
                  "Profit / loss %": "Profit or loss since the start, in %, for the account and for just holding.",
                  "Drawdown": "How far the account is below its best point so far. Closer to 0 is better.",
                  "Cash vs invested": "How much is in cash and how much is in coins over time.",
                  "Selected bot": "The equity of the bot selected on the farm or scoreboard (or the best one)."}

    def build_chart_controls(self, parent):
        bar = tk.Frame(parent, bg=CARD)
        bar.pack(fill="x", pady=(0, 4))
        tk.Label(bar, text="Charts", bg=CARD, fg=MUTED, font=("", 8, "bold")).pack(side="left")
        self.chart_layout = tk.StringVar(value=self.cfg.get("chart_layout", "2 charts"))
        if self.chart_layout.get() not in self.LAYOUTS:
            self.chart_layout.set("2 charts")
        lb = ttk.Combobox(bar, textvariable=self.chart_layout, values=list(self.LAYOUTS), state="readonly", width=14)
        lb.pack(side="left", padx=(4, 0))
        lb.bind("<<ComboboxSelected>>", lambda e: self.build_panels())
        Tip(lb, "How many charts to show at once. Pick what each one shows with the menu at its top-left. "
                "Drag the divider under the charts to make them taller.")
        tk.Label(bar, text="Candles", bg=CARD, fg=MUTED, font=("", 8, "bold")).pack(side="left", padx=(10, 0))
        self.candle_sel = tk.StringVar(value=self.cfg.get("candle_sel", "Auto"))
        cs = ttk.Combobox(bar, textvariable=self.candle_sel, values=list(self.CANDLE_CHOICES), state="readonly", width=6)
        cs.pack(side="left", padx=(4, 0))
        cs.bind("<<ComboboxSelected>>", lambda e: (self.cfg.__setitem__("candle_sel", self.candle_sel.get()), self.draw_chart()))
        Tip(cs, "Candle timeframe: how much time each candle covers on Candles charts. Auto picks one to suit the range.")
        tk.Label(bar, text="Range", bg=CARD, fg=MUTED, font=("", 8, "bold")).pack(side="left", padx=(10, 0))
        self.chart_range = tk.StringVar(value=self.cfg.get("chart_range", "All"))
        self.range_btns = {}
        rb = tk.Frame(bar, bg=CARD)
        rb.pack(side="left", padx=(2, 8))
        for label, _ in self.CHART_RANGES:
            b = tk.Label(rb, text=label, bg=CARD, fg=MUTED, padx=5, pady=1, cursor="hand2", font=("", 8))
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, l=label: self.set_range(l))
            self.range_btns[label] = b
        self.chart_pct = tk.BooleanVar(value=bool(self.cfg.get("chart_pct", False)))
        self.chart_hold = tk.BooleanVar(value=bool(self.cfg.get("chart_hold", True)))
        self.chart_marks = tk.BooleanVar(value=bool(self.cfg.get("chart_marks", True)))
        self.chart_bot = tk.BooleanVar(value=bool(self.cfg.get("chart_bot", True)))
        self.chart_grid = tk.BooleanVar(value=bool(self.cfg.get("chart_grid", True)))
        for var, txt, tip in ((self.chart_grid, "Grid", "Background grid lines."),
                              (self.chart_bot, "Bot trades",
                               "On coin charts: the buys (hollow up arrows) and sells (hollow down arrows) of the "
                               "bot selected on the farm or scoreboard, or the best one if none is selected."),
                              (self.chart_marks, "My trades", "Filled arrows where your account bought and sold."),
                              (self.chart_hold, "Holding", "The grey 'just holding' line on account charts."),
                              (self.chart_pct, "%", "Show change in % instead of dollars or prices.")):
            cb = ttk.Checkbutton(bar, text=txt, variable=var, style="Card.TCheckbutton", command=self.chart_opts_changed)
            cb.pack(side="right", padx=(5, 0))
            Tip(cb, tip)
        self.hover = {}
        self.paint_range_btns()

    def default_panels(self):
        coins = list(self.session_coins or [c for c, v in self.coin_vars.items() if v.get()]) or ["BTC"]
        views = ["Account value", coins[0], "Profit / loss %", coins[1] if len(coins) > 1 else "Drawdown",
                 "Cash vs invested", "Selected bot"]
        return [dict(view=v, type="Line") for v in views]

    def build_panels(self):
        """(Re)builds the chart grid for the chosen layout. Each panel picks what it shows and its chart type."""
        lay = self.chart_layout.get()
        self.cfg["chart_layout"] = lay
        rows, cols = self.LAYOUTS[lay]
        saved = self.cfg.get("chart_panels") or []
        specs = (saved + self.default_panels()[len(saved):])[:6] if isinstance(saved, list) else self.default_panels()
        for p in self.panels:
            p["frame"].destroy()
        self.panels = []
        fr = self.charts_frame
        for r in range(2):
            fr.rowconfigure(r, weight=1 if r < rows else 0)
        for cidx in range(3):
            fr.columnconfigure(cidx, weight=1 if cidx < cols else 0)
        for k in range(rows * cols):
            spec = specs[k] if k < len(specs) and isinstance(specs[k], dict) else self.default_panels()[k]
            pf = tk.Frame(fr, bg=CARD, highlightthickness=1 if rows * cols > 1 else 0, highlightbackground=LINE)
            pf.grid(row=k // cols, column=k % cols, sticky="nsew", padx=2, pady=2)
            head = tk.Frame(pf, bg=CARD)
            head.pack(fill="x")
            view = tk.StringVar(value=spec.get("view", "Account value"))
            kind = tk.StringVar(value=spec.get("type", "Line") if spec.get("type") in self.CHART_TYPES else "Line")
            vb = ttk.Combobox(head, textvariable=view, width=15, state="readonly")
            vb.pack(side="left")
            tb = ttk.Combobox(head, textvariable=kind, values=self.CHART_TYPES, width=7, state="readonly")
            tb.pack(side="left", padx=(4, 0))
            cv = tk.Canvas(pf, height=120, bg=CARD, highlightthickness=0)
            cv.pack(fill="both", expand=True)
            cv.bind("<Configure>", lambda e: self.later(self.draw_chart, 180))
            cv.bind("<Motion>", lambda e, cv=cv: self.chart_hover(e, cv))
            cv.bind("<Leave>", lambda e: (self.hover.clear(), self.draw_chart()))
            for box in (vb, tb):
                box.bind("<<ComboboxSelected>>", lambda e: self.panels_changed())
            self.panels.append(dict(frame=pf, view=view, type=kind, canvas=cv, box=vb))
        self.chart = self.panels[0]["canvas"]
        self.chart_view, self.chart_type = self.panels[0]["view"], self.panels[0]["type"]
        if hasattr(self, "split") and hasattr(self, "chart_card"):
            want = {1: 250, 2: 250, 3: 260, 4: 400, 6: 420}[rows * cols]
            try:
                self.split.paneconfigure(self.chart_card, height=max(want, int(self.cfg.get("chart_h", 0) or 0) if rows * cols < 4 else want))
            except tk.TclError:
                pass
        self.update_view_choices()
        self.draw_chart()

    def panels_changed(self):
        specs = [dict(view=p["view"].get(), type=p["type"].get()) for p in self.panels]
        old = self.cfg.get("chart_panels") or []
        self.cfg["chart_panels"] = specs + old[len(specs):6] if isinstance(old, list) else specs
        try:
            E.save_json(CONFIG_PATH, self.cfg)
        except OSError:
            pass
        self.draw_chart()

    def show_on_chart(self, view):
        """Puts a coin (or view) on a chart: one that already shows it, else the second chart (or the only one)."""
        if any(p["view"].get() == view for p in self.panels):
            return self.draw_chart()
        p = self.panels[1] if len(self.panels) > 1 else self.panels[0]
        p["view"].set(view)
        self.panels_changed()

    def set_range(self, label):
        self.chart_range.set(label)
        self.cfg["chart_range"] = label
        self.paint_range_btns()
        self.draw_chart()

    def paint_range_btns(self):
        for l, b in self.range_btns.items():
            on = l == self.chart_range.get()
            b.config(bg=ACCENT if on else CARD, fg="white" if on else MUTED)

    def chart_opts_changed(self):
        for k, v in (("chart_pct", self.chart_pct), ("chart_hold", self.chart_hold), ("chart_marks", self.chart_marks),
                     ("chart_bot", self.chart_bot), ("chart_grid", self.chart_grid)):
            self.cfg[k] = bool(v.get())
        self.draw_chart()

    def update_view_choices(self):
        coins = list(self.session_coins or [c for c, v in self.coin_vars.items() if v.get()])
        vals = list(self.PANEL_VIEWS) + coins
        for p in self.panels:
            if list(p["box"].cget("values")) != vals:
                p["box"].config(values=vals)
            if p["view"].get() not in vals:
                p["view"].set(coins[0] if coins and p is not self.panels[0] else "Account value")

    @staticmethod
    def _ts(iso):
        try:
            return datetime.fromisoformat(str(iso)).timestamp()
        except (TypeError, ValueError):
            return None

    def bar_time(self, n):
        """Approximate clock time of tournament decision n (decisions happen every bar)."""
        if not self.tourn:
            return None
        ref = self.last_bar or time.time()
        return ref - (self.tourn.n - n) * self.session_bar * 60

    def chart_bot_variant(self):
        if not self.tourn:
            return None
        v = self.selected_variant()
        if v:
            return v
        return self.tourn.active[0] if self.tourn.active else None

    def _range_seconds(self):
        return dict(self.CHART_RANGES).get(self.chart_range.get(), 0)

    def chart_data(self, view):
        """-> (series [(name, [(t, y)], color, width)], markers [(t, y, side, filled, label)], unit, coin or None)
        unit: '$' money, '%' already a percentage, 'px' a coin price."""
        series, marks = [], []
        pts = self.curve
        if pts and len(pts[0]) < 3:
            step, now = max(1, int(self.settings["refresh"])), time.time()
            pts = [(p[0], p[1], now - (len(pts) - 1 - i) * step) for i, p in enumerate(pts)]
        if view == "Profit / loss %":
            sc = self.start_cash or 1
            series.append(("account", [(p[2], (p[0] / sc - 1) * 100) for p in pts], ACCENT, 2))
            if self.chart_hold.get():
                series.insert(0, ("holding", [(p[2], (p[1] / sc - 1) * 100) for p in pts], "#9ca3af", 1))
            return series, marks, "%", None
        if view == "Drawdown":
            peak, dd = 0.0, []
            for p in pts:
                peak = max(peak, p[0])
                dd.append((p[2], (p[0] / peak - 1) * 100 if peak else 0.0))
            series.append(("below best", dd, RED, 2))
            return series, marks, "%", None
        if view == "Cash vs invested":
            al = list(getattr(self, "alloc_hist", ()))
            series.append(("cash", [(t, ca) for t, ca, _ in al], "#9ca3af", 1))
            series.append(("in coins", [(t, inv) for t, _, inv in al], ACCENT, 2))
            return series, marks, "$", None
        if view == "Selected bot":
            v = self.chart_bot_variant()
            if v and v.eq and self.tourn:
                n0 = self.tourn.n - (len(v.eq) - 1)
                series.append((E.bot_nickname(v.key()), [(self.bar_time(n0 + i), e) for i, e in enumerate(v.eq)], "#a855f7", 2))
                for n, coin, side, px in v.log:
                    if n >= n0:
                        marks.append((self.bar_time(n), v.eq[n - n0], side, False, coin))
            return series, marks, "$", None
        if view == "Hall of Fame shadow":
            series.append(("account", [(p[2], p[0]) for p in pts] if pts and len(pts[0]) >= 3 else [], ACCENT, 1))
            series.append(("Hall of Fame shadow", list(self.shadow_curve), "#f59e0b", 2))
            return series, marks, "$", None
        if view == "Account value" or view not in (self.session_coins or []):
            if pts and len(pts[0]) < 3:  # older saved sessions have no times: space them by the refresh rate
                step, now = max(1, int(self.settings["refresh"])), time.time()
                pts = [(p[0], p[1], now - (len(pts) - 1 - i) * step) for i, p in enumerate(pts)]
            series.append(("account", [(p[2], p[0]) for p in pts], ACCENT, 2))
            if self.chart_hold.get():
                series.insert(0, ("holding", [(p[2], p[1]) for p in pts], "#9ca3af", 1))
            if self.chart_marks.get() and pts:
                import bisect
                ts_list = [p[2] for p in pts]
                for tr in self.trades[-400:]:
                    t = self._ts(tr.get("time"))
                    if t is None or t < ts_list[0]:
                        continue
                    i = min(bisect.bisect_left(ts_list, t), len(pts) - 1)
                    marks.append((t, pts[i][0], tr.get("side"), True, tr.get("coin", "")))
            return series, marks, "$", None
        c = view
        ticks = list(self.tick_px.get(c, ()))
        rng = self._range_seconds()
        h = self.hist.get(c, [])
        span_ticks = ticks[-1][0] - ticks[0][0] if len(ticks) > 1 else 0
        if (len(ticks) < 2 or (rng > 0 and rng > span_ticks * 1.2) or (rng == 0 and len(h) * self.session_bar * 60 > span_ticks * 1.5)) and h:
            ref = self.last_bar or time.time()  # longer than the live ticks cover: use the bar history
            hh = h[-6000:]
            bars = [(ref - (len(hh) - 1 - i) * self.session_bar * 60, px) for i, px in enumerate(hh)]
            ticks = bars + [t for t in ticks if t[0] > bars[-1][0]]
        series.append((c, ticks, coin_color(c) if self.cfg.get("chart_coin_colors", True) else ACCENT, 2))
        if self.chart_marks.get():
            for tr in self.trades[-400:]:
                if tr.get("coin") == c:
                    t = self._ts(tr.get("time"))
                    if t is not None:
                        marks.append((t, float(tr.get("price") or 0), tr.get("side"), True, ""))
        v = self.chart_bot_variant() if self.chart_bot.get() else None
        if v:
            for n, coin, side, px in v.log:
                if coin == c:
                    t = self.bar_time(n)
                    if t is not None:
                        marks.append((t, px, side, False, ""))
        return series, marks, "px", c

    def draw_chart(self):
        if self.in_bg():
            self._bg_dirty = True
            return
        if self.moving():  # dragging the window: draw once it stops
            return self.later(self.draw_chart, 300)
        try:
            self.update_view_choices()
        except Exception:
            pass
        for p in getattr(self, "panels", []):
            self.draw_one(p["canvas"], p["view"].get(), p["type"].get())

    def draw_one(self, c, view, kind_pick="Line"):
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        try:
            series, marks, unit, coin = self.chart_data(view)
        except Exception:
            series, marks, unit, coin = [], [], "$", None
        money = unit == "$"
        main = series[-1][1] if series else []
        if len(main) < 2 or w < 80:
            tip = self.PANEL_TIPS.get(view, "")
            c.create_text(w // 2, h // 2, width=max(120, w - 30), justify="center", fill=MUTED,
                          text=("The chart fills in once prices start coming in." if view != "Selected bot"
                                else "Select a bot on the farm or scoreboard.") + ("\n" + tip if tip and h > 90 else ""))
            return
        rng = self._range_seconds()
        t1 = main[-1][0]
        if rng < 0:
            t0 = main[max(0, len(main) + rng)][0]
        elif rng > 0:
            t0 = t1 - rng
        else:
            t0 = main[0][0]
        if t1 <= t0:
            t0 = t1 - 1
        pct = self.chart_pct.get() and unit != "%"
        kind = kind_pick if coin else ("Area" if kind_pick == "Area" else "Line")
        vis = []
        for name, pts, color, width in series:
            seg = [(t, y) for t, y in pts if t >= t0]
            if pts and (not seg or seg[0][0] > t0):  # carry the line in from the left edge
                before = [p for p in pts if p[0] < t0]
                if before:
                    seg.insert(0, (t0, before[-1][1]))
            vis.append((name, seg, color, width))
        base = {name: (seg[0][1] if seg and seg[0][1] else 1) for name, seg, _, _ in vis}
        main_name, main_seg = vis[-1][0], vis[-1][1]
        main_base = base[main_name]

        def val(name, y):
            return (y / base[name] - 1) * 100 if pct else y
        candles = []
        if kind == "Candles" and main_seg:
            span = max(t1 - t0, 1)
            step = self.CANDLE_CHOICES.get(self.candle_sel.get(), 0) or min(self.CANDLE_SECS, key=lambda s: abs(s - span / 70))
            step = max(step, self.session_bar * 60 if not self.CANDLE_CHOICES.get(self.candle_sel.get(), 0) and len(main_seg) and (main_seg[-1][0] - main_seg[0][0]) / max(len(main_seg), 1) >= self.session_bar * 50 else 0)
            cur = None
            for t, y in main_seg:
                b = int(t // step) * step
                if cur is None or cur[0] != b:
                    if cur:
                        candles.append(cur)
                    cur = [b, y, y, y, y]  # time, open, high, low, close
                else:
                    cur[2], cur[3], cur[4] = max(cur[2], y), min(cur[3], y), y
            if cur:
                candles.append(cur)
            for k in range(1, len(candles)):  # each candle opens where the last one closed
                candles[k][1] = candles[k - 1][4]
                candles[k][2] = max(candles[k][2], candles[k][1])
                candles[k][3] = min(candles[k][3], candles[k][1])
        thin = []
        for name, seg, color, width in vis:
            if len(seg) > 2 * w:  # thin out very dense lines; keep the first and last points
                kk = len(seg) // (2 * w) + 1
                seg = seg[:-1:kk] + [seg[-1]]
            thin.append((name, seg, color, width))
        vis = thin
        ys = [val(n, y) for n, seg, _, _ in vis for _, y in seg]
        if candles:
            ys += [val(main_name, y) for cd in candles for y in (cd[2], cd[3])]
        vm = [(t, (y / main_base - 1) * 100 if pct else y, side, filled, lab)
              for t, y, side, filled, lab in marks if t0 <= t <= t1 and y]
        ys += [m[1] for m in vm]
        if not ys:
            return
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.08 or (abs(hi) * 0.001 + 0.01)
        lo, hi = lo - pad, hi + pad
        left, right, top, bot = (64, 10, 18, 18) if w > 380 else (52, 6, 16, 16)

        def X(t):
            return left + (t - t0) / (t1 - t0) * (w - left - right)

        def Y(v):
            return h - bot - (v - lo) / (hi - lo) * (h - top - bot)

        def fmt(v):
            if pct or unit == "%":
                return f"{v:+.2f}%"
            a = abs(v)
            return f"{v:,.0f}" if a >= 1000 else f"{v:,.2f}" if a >= 1 else f"{v:,.5f}"
        for k in range(5):
            v = lo + (hi - lo) * k / 4
            y = Y(v)
            if self.chart_grid.get():
                c.create_line(left, y, w - right, y, fill=LINE)
            c.create_text(left - 6, y, text=fmt(v), anchor="e", fill=MUTED, font=("", 8))
        span = t1 - t0
        for k in range(5):
            t = t0 + span * k / 4
            fmt_t = "%H:%M:%S" if span < 900 else "%H:%M" if span < 172800 else "%b %d"
            if self.chart_grid.get() and 0 < k < 4:
                c.create_line(X(t), top, X(t), h - bot, fill=LINE, dash=(1, 4))
            c.create_text(X(t), h - 4, text=datetime.fromtimestamp(t).strftime(fmt_t), anchor="s", fill=MUTED, font=("", 7))
        for name, seg, color, width in vis:
            if name == main_name and candles:
                continue
            if len(seg) >= 2:
                xy = [z for t, y in seg for z in (X(t), Y(val(name, y)))]
                if kind == "Area" and name == main_name:
                    c.create_polygon(X(seg[0][0]), h - bot, *xy, X(seg[-1][0]), h - bot, fill=color, outline="", stipple="gray25")
                c.create_line(*xy, fill=color, width=width)
        if candles:
            cw = max(1.0, (w - left - right) / max(len(candles), 1) * 0.6)
            for t, o, hi_, lo_, cl in candles:
                xm = X(t + (candles[1][0] - candles[0][0]) / 2 if len(candles) > 1 else t)
                col = GREEN if cl >= o else RED
                c.create_line(xm, Y(val(main_name, hi_)), xm, Y(val(main_name, lo_)), fill=col)
                y_o, y_c = Y(val(main_name, o)), Y(val(main_name, cl))
                c.create_rectangle(xm - cw / 2, min(y_o, y_c), xm + cw / 2, max(y_o, y_c) + 1, fill=col, outline=col)
        for t, y, side, filled, lab in vm:
            x, yy = X(t), Y(y)
            col = GREEN if side == "buy" else RED
            if side == "buy":
                pts = (x, yy + 3, x - 6, yy + 13, x + 6, yy + 13)
            else:
                pts = (x, yy - 3, x - 6, yy - 13, x + 6, yy - 13)
            c.create_polygon(*pts, fill=col if filled else CARD, outline=col, width=1 if filled else 2)
        legend = [(n, col) for n, seg, col, _ in vis]
        if candles:
            legend[-1] = (f"{main_name} candles", legend[-1][1])
        xx = w - right
        if any(not m[3] for m in vm):
            v = self.chart_bot_variant()
            if v:
                legend.insert(0, (f"bot: {E.bot_nickname(v.key())}", MUTED))
        for name, col in reversed(legend):
            item = c.create_text(xx, 4, text=name, anchor="ne", fill=col, font=("", 8, "bold"))
            xx = c.bbox(item)[0] - 12
        hx = self.hover.get(str(c))
        if hx is not None and left <= hx <= w - right:
            t = t0 + (hx - left) / (w - left - right) * (t1 - t0)
            c.create_line(hx, top, hx, h - bot, fill=MUTED, dash=(2, 3))
            lines = [datetime.fromtimestamp(t).strftime("%H:%M:%S")]
            for name, seg, col, _ in vis:
                if seg:
                    p = min(seg, key=lambda q: abs(q[0] - t))
                    v = val(name, p[1])
                    lines.append(f"{name}: {fmt(v) if (pct or unit == '%') else '$' + fmt(v)}")
                    c.create_oval(X(p[0]) - 3, Y(v) - 3, X(p[0]) + 3, Y(v) + 3, outline=col, width=2)
            near = [m for m in vm if abs(X(m[0]) - hx) < 6]
            for m in near[:3]:
                lines.append(f"{'you' if m[3] else 'bot'} {m[2]} {m[4]}".rstrip())
            txt = c.create_text(0, 0, text="\n".join(lines), anchor="nw", fill=TEXT, font=("", 8))
            x0, y0, x1, y1 = c.bbox(txt)
            bx = hx + 10 if hx + 10 + (x1 - x0) < w else hx - 14 - (x1 - x0)
            c.move(txt, bx + 4, top + 4)
            bg = c.create_rectangle(bx, top, bx + (x1 - x0) + 8, top + (y1 - y0) + 8, fill=CARD, outline=LINE)
            c.tag_raise(txt, bg)

    def chart_hover(self, e, cv=None):
        self.hover = {str(cv or self.chart): e.x}
        if not getattr(self, "_hover_pending", False):  # redraw at most once per screen refresh
            self._hover_pending = True
            self.root.after(30, self._hover_draw)

    def _hover_draw(self):
        self._hover_pending = False
        self.draw_chart()

    def position_price(self, coin):
        """Use the latest quote, or cost basis until a quote is available (e.g. on resume)."""
        px = self.last.get(coin)
        if px is not None and px > 0:
            return px
        qty = self.pos.get(coin, 0.0)
        basis = self.cost.get(coin, 0.0)
        return basis / qty if qty > 0 and basis > 0 else 0.0

    def equity(self):
        # Total value is always cash plus the marked-to-market value of open positions.
        return self.cash + sum(q * self.position_price(c) for c, q in self.pos.items())

    def bench_equity(self):
        if not self.bench_units:
            return self.equity()
        return sum(u * self.last.get(c, 0) for c, u in self.bench_units.items())

    def refresh_view(self):
        eq = self.equity()
        pl = eq - self.start_cash
        hold = self.bench_equity() - self.start_cash
        nxt = ""
        if self.running and self.seeded and self.last_tick:
            left = max(0, int(self.last_bar + self.session_bar * 60 - time.time()))
            nxt = f" | next {left // 60}:{left % 60:02d}"
        live = self.live if self.session else self.mode_var.get() == "live"
        mode = "LIVE (REAL MONEY)" if live else "PRACTICE"
        self.root.title(f"CBBF {mode} - {'RUNNING ' if self.running else ''}{pl:+,.2f}{nxt}" if self.session else f"CBBF {mode}")
        self.tiles["equity"].config(text=f"${eq:,.2f}")
        pct = pl / self.start_cash * 100 if self.start_cash else 0.0
        self.tiles["pl"].config(text=f"{pl:+,.2f}  ({pct:+.2f}%)", fg=GREEN if pl >= 0 else RED)
        self.tiles["cash"].config(text=f"${self.cash:,.2f}")
        lead = pl - hold
        if not self.session or not self.curve:
            self.hold_title.config(text="VS JUST HOLDING")
            self.tiles["hold"].config(text="-", fg=TEXT)
        elif abs(lead) < 0.005 * self.start_cash:
            self.hold_title.config(text="SAME AS HOLDING")
            self.tiles["hold"].config(text=f"{lead:+,.2f}", fg=MUTED)
        elif lead > 0:
            self.hold_title.config(text="BETTER THAN HOLDING")
            self.tiles["hold"].config(text=f"{lead:+,.2f}", fg=GREEN)
        else:
            self.hold_title.config(text="BEHIND HOLDING")
            self.tiles["hold"].config(text=f"{lead:+,.2f}", fg=RED)
        dd = eq - self.day_eq if self.day_eq else 0.0
        self.tiles["day"].config(text=f"{dd:+,.2f}", fg=GREEN if dd >= 0 else RED)
        lim = float(self.settings.get("daily_loss", 0) or 0)
        if hasattr(self, "day_sub"):
            if lim > 0 and self.day_eq:
                used = max(0.0, -dd) / (self.day_eq * lim / 100)
                self.day_sub.config(text=f"{min(used, 1) * 100:.0f}% of today's loss limit used",
                                    fg=RED if used >= 0.75 else "#f59e0b" if used >= 0.4 else MUTED)
            else:
                self.day_sub.config(text="no daily limit" if lim <= 0 else "")
        try:
            self.refresh_portfolio()
        except Exception:
            note = self._record_error("updating the portfolio")
            if note:
                self.write(note, "bad")
        gap = pl - hold
        if not self.session or not self.curve:
            self.verdict_lbl.config(text="Press 'Start the bot' to begin. Practice mode uses pretend money.", fg=MUTED)
        elif abs(gap) < 0.005 * self.start_cash:
            self.verdict_lbl.config(text="Doing about the same as just holding so far.", fg=MUTED)
        elif gap > 0:
            self.verdict_lbl.config(text=f"The bot is ahead of just holding by ${gap:,.2f}.", fg=GREEN)
        else:
            self.verdict_lbl.config(text=f"The bot is behind just holding by ${-gap:,.2f}. That's common, "
                                         f"check the Backtest in the Tools menu.", fg=RED)
        if self.session:
            last = self.trades[-1] if self.trades else None
            lt = (f" (last: {last['side'].upper()} {last['coin']} at {str(last.get('time', ''))[11:16]})" if last else "")
            self.activity_lbl.config(text=f"Bots made {len(self.shadow_trades)} practice trades inside the farm (they don't "
                                          f"change your balance). Your account made {len(self.trades)} trades{lt}.")
        else:
            self.activity_lbl.config(text="")
        if self.moving():
            return self.later(self.refresh_view, 300)
        self.refresh_prices()
        self.draw_chart()
        self.headline_lbl.config(text=self.headline())

    def refresh_board(self):
        if self.in_bg():
            self._bg_dirty = True
            return
        try:
            on_board = self.nb.tab(self.nb.select(), "text").strip() == "Strategy scoreboard"
        except Exception:
            on_board = True
        if self.tourn and not on_board:
            # hidden: only keep its list of bots in step (it also holds the farm's selection); details fill in when opened
            have = set(self.board.get_children())
            want = {str(id(v)) for v in self.tourn.vars}
            gone = have - want
            if gone:
                self.board.delete(*gone)
            for iid in want - have:
                self.board.insert("", "end", iid=iid, values=())
            self._board_dirty = True
            self.empty_board.place_forget()
            self.grid_refresh()
            return
        sel = self.board.selection()
        self.board.delete(*self.board.get_children())
        if not self.tourn:
            self.grid_refresh()
            self.empty_board.place(relx=0.5, rely=0.6, anchor="center")
            self.empty_board.lift()
            if hasattr(self, "best_lbl"):
                self.best_lbl.config(text="")
            return
        self.empty_board.place_forget()
        hofk = self.hof_keys()
        for r in self.tourn.board():
            sc = "-" if r["score"] is None else f"{r['score'] * 100:+.1f}%"
            cons = "-" if r["cons"] is None else f"{int(r['cons'] * 100)}%"
            size = f"{r['stake']:.2f}x".replace(".00x", "x") if r["status"] in ("active", "slowed") else "-"
            status = STATUS_TEXT.get(r["status"], r["status"])
            if r["status"] == "benched" and r.get("failing"):
                status = "Stopped (dropped fast)"
            elif r["status"] == "slowed" and r.get("behind"):
                status = "Shaky (not beating holding)"
            elif r["status"] == "slowed" and r.get("proving"):
                status = "Proving itself (live prices)"
            lf = r["life"]
            in_hof = r["v"].key() in hofk
            star = "\u2605 " if in_hof else ""
            tags = (STATUS_TAG.get(r["status"], "learning"),) + (("hof",) if in_hof else ())
            self.board.insert("", "end", iid=str(id(r["v"])), tags=tags, values=(
                star + friendly_label(r["name"], r["params"], r.get("tf", 1), self.session_bar), sc, pct_txt(r.get("edge")),
                cons, "-" if lf["winrate"] is None else f"{lf['winrate'] * 100:.0f}%", pct_txt(lf["ret"]),
                size, status, lf["trades"]))
        resort(self.board)
        keep = [i for i in sel if self.board.exists(i)]
        if keep:
            self.board.selection_set(keep)
        self.grid_refresh()
        scored = [r for r in self.tourn.board() if r["score"] is not None]
        if scored and hasattr(self, "best_lbl"):
            best, worst = max(scored, key=lambda r: r["score"]), min(scored, key=lambda r: r["score"])
            lab = lambda r: f"{friendly_label(r['name'], r['params'], r.get('tf', 1), self.session_bar)} {r['score'] * 100:+.1f}%"
            self.best_lbl.config(text=f"Best: {lab(best)}   |   Worst: {lab(worst)}   |   {len(self.tourn.vars)} bots competing")
        elif hasattr(self, "best_lbl"):
            self.best_lbl.config(text=f"{len(self.tourn.vars)} bots competing (still learning)")

    # ---------------- controls ----------------
    def toggle(self):
        if self.running:
            self.running = False
            self.cfg["was_running"] = False  # stopped on purpose: don't auto-resume next time
            self.save_farm(force=True)
            try:
                E.save_json(CONFIG_PATH, self.cfg)
            except OSError:
                pass
            self.ui_running(False)
            self.save_state()
            if self.session:
                pl, hold = self.equity() - self.start_cash, self.bench_equity() - self.start_cash
                self.write(f"Stopped. Profit/loss {pl:+,.2f} (just holding would be {hold:+,.2f}).")
            return
        resuming = bool(E.load_json(E.state_path(self.mode_var.get() == "live"))) and \
            getattr(self, "_choice", "continue") != "new"
        if (not self.session and not resuming and self.wild_var.get() and int(self.settings.get("auto_coins", 0)) > 0
                and not getattr(self, "_scouted", False)):
            return self.scout_then_start()
        self._scouted = False
        if self.session:
            coins = self.session_coins
        else:
            coins = [c for c, v in self.coin_vars.items() if v.get()]
        if not coins:
            return messagebox.showinfo("Pick coins", "Tick at least one coin for the bot to watch.")
        if not self.wild_var.get() and not any(v.get() for v in self.strat_vars.values()):
            return messagebox.showinfo("Pick strategies", "Tick a strategy, or tick 'Let the bot choose (Wildcard)'.")
        if not self.session and not self.begin_session(coins):
            return
        coins = list(self.session_coins) or coins  # a resumed session keeps its own coins
        if self.halted_day == date.today():
            if not messagebox.askyesno("Daily limit", "The daily max-loss limit was hit today. Resume anyway?"):
                return
            self.halted_day, self.day_eq = None, self.equity()
        self.wild = self.wild_var.get()
        self.active = [n for n, v in self.strat_vars.items() if v.get()]
        if self.wild and self.tourn is None:
            self.seeded = False
            if not getattr(self, "_restored_farm", None):
                t = self.load_farm()
                if t:
                    self._restored_farm = t
        if self.session and self.live and not self.client:
            return messagebox.showerror("Live mode", "Log in to Coinbase first.")
        self.running = True
        self.cfg.update(was_running=True, last_mode="live" if self.live else "paper", last_wild=bool(self.wild_var.get()))
        try:
            E.save_json(CONFIG_PATH, self.cfg)
        except OSError:
            pass
        self.ui_running(True)
        self.write(f"Started ({'LIVE' if self.live else 'paper'}, {'wildcard' if self.wild else ', '.join(self.active)}, "
                   f"{E.fmt_bar(self.session_bar)} bars, {self.settings.get('order_type', 'market')} orders).")
        self.worker_args = (list(coins), self.session_bar, self.wild, self.settings["fee_pct"] / 100,
                            self.settings["trade_pct"] / 100, self.settings["evolve_every"])
        self.launch_worker()

    def begin_session(self, coins, quiet=False):
        live = self.mode_var.get() == "live"
        saved = E.load_json(E.state_path(live))
        resume = False
        if saved:  # the Continue / Start new buttons decide (no pop-up)
            resume = getattr(self, "_auto_resume", False) or getattr(self, "_choice", "continue") != "new"
        if not resume:
            self._restored_farm = None
            try:
                os.remove(E.farm_path(live))  # a new session starts a new farm
            except OSError:
                pass
        try:
            budget = float(self.cash_var.get())
            assert budget > 0
        except Exception:
            if not resume:
                messagebox.showerror("Money amount", "Please type how much money to start with, like 1000.")
                return False
        if resume:
            self.reset_state(saved["start_cash"])
            self.load_state(saved)
            self.quote_var.set(self.quote)
        else:
            self.reset_state(budget)
            self.session_coins = list(coins)
            self.quote = self.quote_var.get()
        self.usd_avail = self.balances.get(self.quote, 0.0)
        self.refresh_trades()
        self.live = live
        if live and not self.confirm_live(resume):
            self.reset_state(1000.0)
            self.live = False
            self.write("LIVE was not confirmed, so you're back in Practice. Nothing real happened.", "good")
            self.root.after(10, lambda: self.switch_mode("paper"))
            return False
        self.session = True
        self.save_state()
        # Refresh immediately when starting or resuming, before the first price-feed tick.
        self.refresh_view()
        return True

    def reset_menu(self, widget):
        m = self._menu()
        acct = "LIVE session tracking" if self.live else "practice account"
        m.add_command(label=f"Reset the {acct} (cash, positions, trades)...", command=lambda: self.reset_account())
        m.add_command(label="Reset the farm (fresh eggs; Library and Hall of Fame kept)...", command=lambda: self.reset_farm())
        m.add_command(label="Reset both...", command=lambda: self.reset_account(both=True))
        try:
            m.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            m.grab_release()

    def reset_account(self, both=False):
        what = "the farm AND the " + ("LIVE session tracking" if self.live else "practice account") if both else \
            ("the LIVE session tracking" if self.live else "the practice account")
        msg = f"Reset {what}?"
        if not self.live:
            try:
                msg += f"\n\nYou start again with ${float(self.cash_var.get()):,.2f} of pretend money (change it under ACCOUNT)."
            except ValueError:
                pass
        if self.live and any(q > 0 for q in self.pos.values()):
            msg += "\n\nLive coins the app bought stay on your Coinbase account but won't be tracked."
        msg += "\nYour Library and Hall of Fame are kept."
        if not messagebox.askyesno("Reset", msg):
            return
        if both:
            self.reset_farm(ask=False)
        self.reset(ask=False)
        self.write(f"Reset {what}. Press Start to begin again.", "good")

    def reset_farm(self, ask=True):
        if ask and not messagebox.askyesno("Reset the farm", "Clear every bot on the farm and hatch a fresh one next time you "
                                                             "press Start?\n\nYour Library and Hall of Fame are kept, and the "
                                                             "account's cash and trades are not touched."):
            return
        was = self.running
        if was:
            self.toggle()
        for live in (self.live,):
            try:
                os.remove(E.farm_path(live))
            except OSError:
                pass
        self.tourn, self.seeded, self._restored_farm = None, False, None
        self.shadow_live, self.shadow_curve = None, []
        self.farm_hist.clear()
        self.__dict__.pop("tier_seen", None)
        self.refresh_board()
        self.write("The farm was reset: fresh eggs hatch when you press Start.", "good")

    def reset(self, ask=True):
        if ask and (self.trades or self.pos):
            msg = "Clear this session and its saved state?"
            if self.live and any(q > 0 for q in self.pos.values()):
                msg += "\n\nLive positions the app bought will stay on your Coinbase account but won't be tracked."
            if not messagebox.askyesno("Reset", msg):
                return
        if self.running:
            self.running = False
            self.save_farm(force=True)
        self.ui_running(False)
        try:
            os.remove(E.state_path(self.live))
        except OSError:
            pass
        self.session = False
        try:
            cash = float(self.cash_var.get())
        except ValueError:
            cash = 1000.0
        self.reset_state(cash)
        self.refresh_trades()
        self.update_status()
        self.refresh_view()
        self.refresh_board()
        self.write("Session reset.")

    def launch_worker(self):
        """Starts the price-feed thread. Any older feed notices the new number and exits."""
        self.worker_gen += 1
        t = threading.Thread(target=self.worker, daemon=True, args=tuple(self.worker_args) + (self.worker_gen,))
        self.worker_thread = t
        t.start()

    def retire_steps(self, bar_min):
        return int(float(self.settings["retire_hours"]) * 60 / max(float(bar_min), 0.25))

    def _seed(self, coins, bar_min, wild, fee, pct, evolve):
        gran = E.candle_gran(bar_min)
        if bar_min < 1:
            self.q.put(("log", (f"Loading history. Coinbase has nothing shorter than 1-minute candles, so the bots warm up "
                                f"on those and switch to real {E.fmt_bar(bar_min)} bars as live prices arrive.", "info")))
        else:
            self.q.put(("log", f"Loading {E.fmt_bar(bar_min)} history..."))
        hist, hx = {}, {}
        for c in coins:
            try:
                d = E.fetch_ohlcv(c, gran, 2000, self.quote)  # enough for slow, long-holding bots too
                hist[c], hx[c] = d["c"], dict(h=d["h"], l=d["l"], v=d["v"])
            except Exception as e:
                hist[c], hx[c] = [], dict(h=[], l=[], v=[])
                self.q.put(("log", f"History unavailable for {c}: {e}"))
        L = min(len(v) for v in hist.values())
        hist = {c: v[-L:] if L else [] for c, v in hist.items()}
        hx = {c: {k: v[-L:] if L else [] for k, v in d.items()} for c, d in hx.items()}
        if bar_min < 1:  # scale 1-minute volume to the shorter bar so volume strategies compare like with like
            hx = {c: dict(d, v=[x * bar_min for x in d["v"]]) for c, d in hx.items()}
        tourn = None
        restored = getattr(self, "_restored_farm", None)
        self._restored_farm = None
        if wild and restored is not None and set(restored.coins) == set(coins):
            restored.coins = list(coins)
            restored.o.update(E.tournament_opts(self.settings))
            restored.xfull = None
            self.q.put(("log", (f"Carrying on with your farm of {len(restored.vars)} bots where it left off.", "good")))
            self.q.put(("seed", (hist, restored, hx)))
            return
        if wild:
            opts = E.tournament_opts(self.settings)
            tourn = None
            if self.settings.get("multicore", True) and L > E.WARM and opts.get("islands", 1) > 1:
                self.q.put(("log", f"Warming up {opts['islands']} islands on several processor cores..."))
                tourn = E.warm_islands(coins, self.start_cash, fee, pct, evolve, self.settings["params"], opts,
                                       int(self.settings["max_bots"]), self.retire_steps(bar_min), hist, hx,
                                       log=lambda m: self.q.put(("log", m)))
            if tourn is None:
                tourn = E.Tournament(coins, self.start_cash, fee, pct, evolve, params=self.settings["params"],
                                     opts=opts, cap=int(self.settings["max_bots"]),
                                     retire_steps=self.retire_steps(bar_min))
                if L > E.WARM:
                    tourn.warmup(hist, extra=hx)
            roster = self.roster("live" if self.live else "paper")
            squad = (E.team() if roster == "team" else self.hof()[:max(14, int(self.settings["max_bots"]))]
                     if roster == "hof" else [])
            if self.live and getattr(self, "grad_only", False):  # graduation: only bots that earned real money
                squad = [b for b in squad if self.graduated(b)[0]]
            if squad:
                for v in list(tourn.vars):
                    tourn._remove(v, "benched for your team")
                for b in squad:
                    tourn.add_variant(b["name"], b["params"], hist, b.get("size", 1.0),
                                      note=f"{'Team' if roster == 'team' else 'Hall of Fame'}: {b.get('nickname') or E.bot_nickname(E.bot_key(b))}",
                                      tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), vetoes=b.get("vetoes"), layers=b.get("layers"), origin="team")
                locked = roster == "hof" or self.settings.get("team_locked", True)
                if locked:
                    tourn.retire_steps, tourn.evolve_every = 0, 10 ** 9
                who = ", ".join(b.get("nickname") or E.bot_nickname(E.bot_key(b)) for b in squad)
                self.q.put(("log", (f"Trading with {'your team' if roster == 'team' else 'your Hall of Fame only'}: {who}"
                                    + (" (locked: no new bots)." if locked else " (they may breed)."), "good")))
            saved = [] if squad else E.load_strategies()
            for d in saved:
                tourn.add_variant(d["name"], d["params"], hist, d.get("size", 1.0), note="Loaded saved", tf=d.get("tf", 1),
                                  risk=d.get("risk"), traits=d.get("traits"), vetoes=d.get("vetoes"), layers=d.get("layers"), origin="saved")
            if saved:
                self.q.put(("log", f"Loaded {len(saved)} saved strategies into the tournament."))
            if self.settings.get("hof_load", True) and not squad:
                have = {v.key() for v in tourn.vars}
                hof = [b for b in E.load_hof() if E.bot_key(b) not in have][:10]
                for b in hof:
                    v = tourn.add_variant(b["name"], b["params"], hist, b.get("size", 1.0), note="Hall of Fame:",
                                          tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), vetoes=b.get("vetoes"), layers=b.get("layers"), origin="hall of fame")
                    if v:
                        v.inducted = True
                if hof:
                    self.q.put(("log", (f"Brought back {len(hof)} Hall of Fame bots. They still have to win on today's prices.", "good")))
        self.q.put(("seed", (hist, tourn, hx)))

    def worker(self, coins, bar_min, wild, fee, pct, evolve, gen=0):
        def alive():
            return self.running and self.worker_gen == gen
        if not self.seeded:
            try:
                self._seed(coins, bar_min, wild, fee, pct, evolve)
            except Exception:
                self.q.put(("log", (self._record_error("loading history") + " Starting without history.", "bad")))
                tourn = (E.Tournament(coins, self.start_cash, fee, pct, evolve, params=self.settings["params"],
                                      opts=E.tournament_opts(self.settings), cap=int(self.settings["max_bots"]),
                                      retire_steps=self.retire_steps(bar_min))
                         if wild else None)
                self.q.put(("seed", ({c: [] for c in coins}, tourn, {c: dict(h=[], l=[], v=[]) for c in coins})))
        down_since, last_warn = None, 0.0
        batch_ok, last_vol = True, 0.0
        gran = E.candle_gran(bar_min)
        while alive():
            try:
                prices, fails = {}, 0
                t_req = time.time()
                if batch_ok:  # one request for every coin
                    try:
                        prices = E.spot_many(coins, self.quote)
                    except Exception:
                        prices = {}
                    if not prices:
                        batch_ok = False
                        self.q.put(("log", ("Fast all-coins price request isn't working, using one request per coin.", "warn")))
                missing = [c for c in coins if c not in prices]
                for c in missing:
                    if not alive():
                        return
                    try:
                        prices[c] = E.spot(c, self.quote)
                        fails = 0
                    except Exception:
                        fails += 1
                        if fails >= 2:  # the connection is down, don't wait on every remaining coin
                            break
                now = time.time()
                self.q.put(("net", (len(prices), len(coins), now - t_req)))
                if prices and now - last_vol >= max(60.0, bar_min * 60):  # real trading volume for volume strategies
                    last_vol = now
                    vols = {}
                    for c in coins:
                        if not alive():
                            return
                        try:
                            v = E.latest_volume(c, gran, self.quote)
                            if v is not None:
                                vols[c] = v * (bar_min if bar_min < 1 else 1)
                        except Exception:
                            pass
                    if vols:
                        self.q.put(("vols", vols))
                if len(prices) == len(coins):
                    if down_since is not None:
                        mins = (now - down_since) / 60
                        self.q.put(("log", (f"Coinbase prices are back after {mins:.0f} min." if mins >= 1
                                            else "Coinbase prices are back.", "good")))
                    down_since = None
                elif down_since is None:
                    down_since = last_warn = now
                    self.q.put(("log", ("Can't get prices from Coinbase right now. Check your internet "
                                        "connection. I'll keep trying.", "warn")))
                elif now - last_warn >= 600:
                    last_warn = now
                    self.q.put(("log", (f"Still no prices after {(now - down_since) / 60:.0f} min. Still trying.", "warn")))
                if prices:
                    self.q.put(("prices", prices))
                if self.live and self.client and time.time() - self.last_bal > 30:
                    self.last_bal = time.time()
                    try:
                        self.q.put(("balances", self.read_balances(
                            E.to_dict(self.client.get_accounts(limit=250))["accounts"])))
                    except Exception:
                        pass
            except Exception:  # never let the feed die
                self.q.put(("log", (self._record_error("fetching prices"), "bad")))
                time.sleep(2)
            for _ in range(max(1, int(self.settings["refresh"])) * 5):
                if not alive():
                    return
                time.sleep(0.2)

    def watchdog(self):
        """Every 5 seconds: restart the price feed if it died, and say so if prices have gone stale."""
        try:
            self._show_err_badge()
            if self.running:
                t = self.worker_thread
                if t is not None and not t.is_alive() and self.worker_args:
                    self.write("The price feed stopped unexpectedly. Restarting it.", "warn")
                    self.launch_worker()
                if self.last_tick:
                    age = (datetime.now() - self.last_tick).total_seconds()
                    if age > max(45, 6 * int(self.settings["refresh"])):
                        mins, secs = divmod(int(age), 60)
                        self.tick_lbl.config(fg=RED, text=f"No new prices for {mins}m {secs:02d}s. Reconnecting...")
                        if age > 300 and not self._stale_sent:
                            self._stale_sent = True
                            self.notify("CBBF", "No prices for over 5 minutes. Check your connection.", sound="problem")
                    else:
                        self._stale_sent = False
                        self.tick_lbl.config(fg=MUTED)
            else:
                self.tick_lbl.config(fg=MUTED)
        except Exception:
            self.write(self._record_error("checking the connection"), "bad")
        finally:
            self.root.after(5000, self.watchdog)

    def _record_error(self, where, text=None):
        """Saves a traceback to crash.log and returns a one-line message. Safe to call from any thread."""
        import traceback
        text = text or traceback.format_exc()
        last = (text.strip().splitlines() or ["error"])[-1]
        key, now = (where, last), time.time()
        if now - self._err_seen.get(key, 0) >= 60:  # don't repeat the same problem every few seconds
            self._err_seen[key] = now
            self.err_count = getattr(self, "err_count", 0) + 1
            try:
                os.makedirs(E.HOME_DIR, exist_ok=True)
                with open(os.path.join(E.HOME_DIR, "crash.log"), "a") as f:
                    f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ({where}) ---\n{text}")
            except OSError:
                pass
            return f"Internal error while {where}: {last}. The bot kept running (details in crash.log)."
        return ""

    def _show_err_badge(self):
        n = getattr(self, "err_count", 0)
        if n and hasattr(self, "err_lbl"):
            self.err_lbl.config(text=f"{n} error{'s' if n != 1 else ''} - click for details")
            self.err_lbl.pack(side="right", padx=14)

    def diagnostics_text(self):
        def tail(name, n):
            try:
                with open(os.path.join(E.HOME_DIR, name), encoding="utf-8", errors="replace") as f:
                    return "".join(f.readlines()[-n:]).strip() or "(empty)"
            except OSError:
                return "(not found)"
        try:
            tk_ver = str(tk.TkVersion)
        except Exception:
            tk_ver = "?"
        info = [f"CBBF {self.version}", f"Python {sys.version.split()[0]}  Tk {tk_ver}  {sys.platform}",
                f"Data folder: {E.HOME_DIR}",
                f"Session: {self.session}  live: {self.live}  running: {self.running}",
                f"Cash: {self.cash:.2f}  start: {self.start_cash:.2f}  positions: {dict(self.pos)}",
                f"Trades in memory: {len(self.trades)}  rows shown: {len(self.trade_tree.get_children())}",
                f"Errors this run: {getattr(self, 'err_count', 0)}",
                "", "--- crash.log (last 60 lines) ---", tail("crash.log", 60),
                "", "--- activity.log (last 30 lines) ---", tail("activity.log", 30),
                "", "--- trades.csv (first 5 lines) ---"]
        try:
            with open(os.path.join(E.HOME_DIR, "trades.csv"), encoding="utf-8", errors="replace") as f:
                info.append("".join(f.readlines()[:5]).strip() or "(empty)")
        except OSError:
            info.append("(not found)")
        return "\n".join(info)

    def show_diagnostics(self):
        text = self.diagnostics_text()
        w = tk.Toplevel(self.root)
        w.title("Diagnostics")
        t = tk.Text(w, width=100, height=32, wrap="none", padx=8, pady=6)
        t.insert("1.0", text)
        t.pack(fill="both", expand=True, padx=6, pady=6)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            btn.config(text="Copied")
        btn = ttk.Button(w, text="Copy all", command=copy)
        btn.pack(pady=(0, 8))
        copy()

    def on_cb_error(self, exc, val, tb):
        import traceback
        msg = self._record_error("updating the screen", "".join(traceback.format_exception(exc, val, tb)))
        if msg:
            try:
                self.write(msg, "bad")
            except Exception:
                pass

    def drain(self):
        """Handles messages from background threads. One bad message can no longer stop the loop."""
        try:
            for _ in range(300):
                try:
                    kind, data = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_msg(kind, data)
                except Exception:
                    msg = self._record_error(f"handling '{kind}'")
                    if msg:
                        self.write(msg, "bad")
        finally:
            self.root.after(200, self.drain)

    def _handle_msg(self, kind, data):
        if kind == "log":
            self.write(*data) if isinstance(data, tuple) else self.write(data)
        elif kind == "seed":
            self.hist, self.tourn = data[0], data[1]
            self.hx = data[2] if len(data) > 2 else {c: dict(h=[], l=[], v=[]) for c in self.hist}
            if self.tourn:
                self.tourn.recording = True  # history is loaded; from now on bot trades are listed
            self.seeded, self.last_bar = True, 0.0
            n = min((len(v) for v in self.hist.values()), default=0)
            self.write(f"History loaded ({n} bars per coin)." if n else "Starting without history (cold start).")
        elif kind == "prices":
            if self.running:
                self.on_prices(data)
        elif kind == "connected":
            self.client, self.balances = data
            self.usd_avail = self.avail()
            self.logging_in = False
            self.update_status()
            self.write(f"Logged in to Coinbase. {self.cur_quote()} available: ${self.usd_avail:,.2f} "
                       f"(USD ${self.balances.get('USD', 0):,.2f}, USDC ${self.balances.get('USDC', 0):,.2f})")
        elif kind == "balances":
            self.balances = data
            self.usd_avail = self.avail()
            self.update_status()
        elif kind == "login_failed":
            self.logging_in = False
            self.update_status()
            self.write(f"Login failed: {data}")
            messagebox.showerror("Login failed", str(data))
        elif kind == "fill":
            self.on_fill(*data)
        elif kind == "fail":
            self.on_fail(*data)
        elif kind == "bt":
            self.show_backtest(*data)
        elif kind == "call":  # run a small UI update that a background thread asked for
            data()
        elif kind == "watch":
            self.on_watch(*data)
        elif kind == "net":
            self.on_net(*data)
        elif kind == "vols":
            self.cur_vol.update(data)
        elif kind == "unfilled":
            self.on_unfilled(*data)
        elif kind == "lab":
            self.on_lab_msg(*data)
        elif kind == "scouted":
            self.on_scouted(*data)
        elif kind == "update":
            self.on_update_msg(*data)
        elif kind == "dep_log":
            self.setup_log(data)
        elif kind == "dep_done":
            self.setup_done(*data)

    # ---------------- price handling / strategy loop ----------------
    def on_prices(self, prices):
        if not prices:
            return
        self.last.update(prices)
        now = datetime.now()
        ts = time.time()
        for c, px in prices.items():
            hl = self.bar_hl.get(c)
            self.bar_hl[c] = [max(hl[0], px), min(hl[1], px)] if hl else [px, px]
            self.tick_px.setdefault(c, deque(maxlen=8000)).append((ts, px))
        if self._gap_ref and (now - self._gap_ref).total_seconds() > max(120, 8 * int(self.settings["refresh"])):
            self.write(f"No prices for {(now - self._gap_ref).total_seconds() / 60:.0f} min "
                       f"(computer asleep or offline). Back online now.", "warn")
        self.last_tick = self._gap_ref = now
        for c, p in prices.items():
            self.start_px.setdefault(c, p)
        if not self.bench_units and all(c in self.last for c in self.session_coins):
            share = self.start_cash / len(self.session_coins)
            self.bench_units = {c: share / self.last[c] for c in self.session_coins}
        self.risk_checks()
        if time.time() - self.last_bar >= self.session_bar * 60 and all(c in prices for c in self.session_coins):
            self.last_bar = time.time()
            self.on_bar(prices)
        self.curve.append((self.equity(), self.bench_equity(), ts))
        del self.curve[:-8000]
        if not hasattr(self, "alloc_hist"):
            self.alloc_hist = deque(maxlen=8000)
        self.alloc_hist.append((ts, self.cash, self.equity() - self.cash))
        self.check_daily()
        self.refresh_view()

    def on_bar(self, prices):
        self.bar_n += 1
        keep = min(8000, max(2400, int(1440 / self.session_bar) + 20))
        for c in self.session_coins:
            h = self.hist.setdefault(c, [])
            x = self.hx.setdefault(c, dict(h=[], l=[], v=[]))
            if len(x["h"]) != len(h):  # line the extra data up with the closes (older sessions had none)
                x["h"] = list(h)
                x["l"] = list(h)
                x["v"] = [0.0] * len(h)
            hi, lo = self.bar_hl.pop(c, [prices[c], prices[c]])
            h.append(prices[c])
            x["h"].append(max(hi, prices[c]))
            x["l"].append(min(lo, prices[c]))
            x["v"].append(float(self.cur_vol.get(c, 0.0)))
            del h[:-keep]
            for k in ("h", "l", "v"):
                del x[k][:-keep]
        if self.wild and self.tourn and set(self.tourn.coins) != set(self.session_coins):
            fixed = self.tourn.align_coins(self.session_coins, {c: self.hist[c][-1] for c in self.session_coins if self.hist.get(c)})
            self.write(f"The farm's coins were out of step with the session; it now trades {', '.join(fixed)}.", "warn")
        if self.wild and self.tourn:
            self.save_farm()
            self.tourn.step({c: self.hist[c] for c in self.session_coins}, {c: self.hx[c] for c in self.session_coins})
            self.collect_shadow()
            for fn in (self.shadow_live_step, self.record_farm, self.maybe_retest):
                try:
                    fn()
                except Exception:
                    note = self._record_error(fn.__name__)
                    if note:
                        self.write(note, "bad")
            self.check_hof()
            for e in self.tourn.pop_events():
                self.write("Wildcard: " + friendly_text(e[0].lower() + e[1:]))
            self.update_wild_msg()
            self.refresh_board()
        for c in self.session_coins:
            if c in self.pending:
                continue
            mult, who, voters = 1.0, [], []
            if self.wild and self.tourn:
                sig, mult = self.tourn.vote(c)
                if sig:
                    add = self.tourn.kind.get(c) == "add"
                    pool = self.tourn.active if add else self.tourn.active + self.tourn.slowed
                    voters = [v for v in pool if (self.tourn._holding(v, c) if add else v.sig.get(c) == sig)]
                    who = [v.name for v in voters]
            else:
                votes = [E.strat_signal(n, self.hist[c], self.bar_n, self.settings["params"][n], 1, self.hx.get(c))
                         for n in self.active]
                b, s = votes.count("buy"), votes.count("sell")
                sig = "buy" if b > s else "sell" if s > b else None
                if sig:
                    ms = [self.settings["size_mult"].get(n, 1.0) for n, v in zip(self.active, votes) if v == sig]
                    mult = sum(ms) / len(ms)
                    who = [n for n, v in zip(self.active, votes) if v == sig]
            if sig and not self.brake_ok():
                sig = None
            if sig and not in_hours(self.settings.get("trade_hours", ""), self.settings.get("weekdays_only", False)):
                if time.time() - getattr(self, "_hours_note", 0) > 3600:
                    self._hours_note = time.time()
                    self.write("Outside your trading hours: the account waits (the bots keep learning, stops still work). "
                               "Settings > Orders > Trading hours", "warn")
                sig = None
            hold_min = float(self.settings.get("min_hold_min", 0) or 0)
            if sig == "sell" and hold_min > 0 and c in self.bought_at and time.time() - self.bought_at[c] < hold_min * 60:
                sig = None  # minimum hold time not reached yet
            if sig == "sell" and self.settings.get("fee_aware", True) and self.pos.get(c, 0) > 0:
                fee, slip = E.cost_model(self.settings)
                gain = prices[c] / (self.cost[c] / self.pos[c]) - 1
                if 0 <= gain < 2 * (fee + slip):
                    sig = None  # the profit wouldn't cover the fees: keep holding
            if sig == "buy" and voters:
                mult = self.allowance_mult(c, voters, mult)
                if mult <= 0:
                    sig = None
            if sig:
                self.pending_detail[c] = self.why_text(c, sig, voters, who)
                self.pending_voters[c] = [v.uid for v in voters]
                counts = {}
                for n in who:
                    counts[n] = counts.get(n, 0) + 1
                self.pending_who[c] = ", ".join(STRAT_LABELS.get(n, n) + (f" x{k}" if k > 1 else "") for n, k in counts.items())
            if sig == "buy":
                kind = self.tourn.kind.get(c) if (self.wild and self.tourn) else None
                self.buy(c, prices[c], "add-on" if kind == "add" else "signal", mult)
            elif sig == "sell":
                self.sell(c, prices[c], "signal")
            if c not in self.pending:  # nothing is waiting on a live order, so drop any unused note
                self.pending_who.pop(c, None)
                self.pending_detail.pop(c, None)
                self.pending_voters.pop(c, None)
        self.save_state(force=False)
        if self.bar_n % 60 == 0:
            self.weekly_report()

    def brake_ok(self):
        """Max-trades-per-hour brake for strategy trades (safety stops are never braked)."""
        lim = int(self.settings.get("max_trades_hour", 0) or 0)
        if lim <= 0:
            return True
        cut = datetime.now().timestamp() - 3600
        n = sum(1 for t in self.trades[-500:] if t.get("reason") in ("signal", "add-on")
                and (self._ts(t.get("time")) or 0) >= cut) + len(self.pending)
        if n < lim:
            return True
        if time.time() - self.brake_note > 900:
            self.brake_note = time.time()
            self.write(f"Brake: {lim} strategy trades in the last hour, so new ones wait. Safety stops still work. "
                       f"(Settings > Orders)", "warn")
        return False

    # ================= 1.13: trust, insight and convenience =================
    # ---------------- kill switch ----------------
    def kill_switch(self, ask=True):
        """Stop everything now: stop the bot, cancel waiting orders, sell every position at market, lock LIVE."""
        if ask and not messagebox.askyesno("KILL SWITCH", "Stop the bot, cancel any waiting orders, SELL EVERYTHING at "
                                                          "market and lock LIVE mode?\n\n(Use it when something looks wrong.)"):
            return
        if self.running:
            self.toggle()
        oids = list(getattr(self, "open_oids", set()))
        if self.client and oids:
            def cancel():
                try:
                    self.client.cancel_orders(order_ids=oids)
                except Exception as e:
                    self.q.put(("log", (f"Kill switch: could not cancel orders: {e}", "bad")))
            self.executor.submit(cancel)
        sold = 0
        for c, q in list(self.pos.items()):
            if q > 0 and c in self.last and c not in self.pending:
                self.sell(c, self.last[c], "manual")
                sold += 1
        if self.live:
            self.cfg["live_locked"] = True
            E.save_json(CONFIG_PATH, self.cfg)
        msg = (f"KILL SWITCH: bot stopped, {len(oids)} waiting order(s) cancelled, {sold} position(s) sold"
               + (", LIVE mode locked (type UNLOCK to use it again)." if self.live else "."))
        self.write(msg, "bad")
        self.alert("problems", msg)
        self.notify("CBBF", msg, sound="problem")
        return msg

    # ---------------- graduation ----------------
    def graduated(self, b):
        """(ok, [what's missing]) for a saved bot before it may trade real money."""
        if not self.settings.get("graduation", True):
            return True, []
        lt = b.get("lifetime") or {}
        miss = []
        need = float(self.settings.get("grad_hours", 24) or 0)
        hrs = lt.get("hours_winning") or 0
        if hrs < need:
            miss.append(f"{hrs:.1f} of {need:g} h winning")
        if (lt.get("return_pct") or 0) <= 0:
            miss.append("no lifetime profit")
        if lt.get("vs_holding_pct") is None or lt["vs_holding_pct"] <= 0:
            miss.append("not ahead of holding")
        if (lt.get("trades") or 0) < 5:
            miss.append("fewer than 5 trades")
        bts = b.get("backtests") or []
        twin = b.get("twin_test_pct")
        if not ((twin is not None and twin > 0) or any(x.get("edge", 0) > 0 for x in bts)):
            miss.append("no passed twin test or backtest")
        if (b.get("retest") or {}).get("rusty"):
            miss.append("rusty in the last retest")
        return not miss, miss

    # ---------------- Shadow Live ----------------
    def shadow_live_step(self):
        """A pretend account that follows only the Hall of Fame bots on the farm, so you can see how they'd do with
        real money before you trust them with it."""
        if not self.settings.get("shadow_live", True) or not self.tourn:
            return
        hofk = self.hof_keys()
        members = [v for v in self.tourn.vars if v.key() in hofk]
        if self.shadow_live is None:
            fee, slip = E.cost_model(self.settings)
            self.shadow_live = E.Sim(self.start_cash, fee, self.settings["trade_pct"] / 100,
                                     self.settings["max_coin_pct"] / 100, slip)
        sim = self.shadow_live
        for c in self.session_coins:
            px = self.last.get(c) or (self.hist.get(c) or [None])[-1]
            if not px:
                continue
            b = sum(1 for v in members if v.sig.get(c) == "buy")
            sl = sum(1 for v in members if v.sig.get(c) == "sell")
            if b > sl:
                sim.buy(c, px)
            elif sl > b:
                sim.sell(c, px)
        eq = sim.equity({c: self.last.get(c, 0) for c in self.session_coins})
        self.shadow_curve.append((time.time(), eq))
        del self.shadow_curve[:-8000]

    # ---------------- farm pulse ----------------
    def pulse_text(self):
        t = self.tourn
        if not t:
            return "Farm pulse: the farm hasn't started yet."
        hour = max(1, int(60 / max(self.session_bar, 0.25)))
        born = sum(1 for n in t.births if n > t.n - hour)
        died = sum(1 for n in t.deaths if n > t.n - hour)
        best, bv = None, -9.0
        for v in t.vars:
            k = min(len(v.eq) - 1, hour)
            if k >= 3 and v.eq[-1 - k] > 0:
                g = v.eq[-1] / v.eq[-1 - k] - 1
                if g > bv:
                    best, bv = v, g
        parts = [f"◉ gen {max((v.gen for v in t.vars), default=0)}", f"+{born} born / -{died} retired this hour",
                 f"season: {MOOD_TEXT.get(t.season, t.season) if t.season else 'settling'}"]
        if best:
            parts.append(f"best of the hour: {E.bot_nickname(best.key())} {bv * 100:+.2f}%")
        if getattr(self, "roam_n", 0):
            parts.append(f"free roam: {self.roam_n} self-studies")
        if self.shadow_curve and self.settings.get("shadow_live", True):
            parts.append(f"Hall of Fame shadow: {(self.shadow_curve[-1][1] / self.start_cash - 1) * 100:+.1f}%")
        return "   •   ".join(parts)

    # ---------------- achievements ----------------
    def achievements(self, v, lf):
        """Badges a dragon has earned: (name, what it means)."""
        out = []
        hrs = v.win_steps * self.session_bar / 60
        closed = (lf.get("wins") or 0) + (lf.get("losses") or 0)
        for ok, name, why in (
                ((lf.get("ret") or 0) >= 0.10, "First 10%", "made 10% or more in its life"),
                ((lf.get("edge") or 0) >= 0.05, "Beat the market", "5%+ ahead of just holding"),
                ((lf.get("hold") or 0) <= -0.05 and (lf.get("ret") or 0) > 0, "Crash survivor", "made money while holding lost 5%+"),
                ((lf.get("winrate") or 0) >= 0.7 and closed >= 10, "Sharpshooter", "70%+ wins over 10+ trades"),
                (hrs >= 24, "Marathon", "a full day spent Winning"),
                (v.gen >= 5, "Dynasty", "fifth generation or later"),
                (v.twin is not None and v.twin > 0, "Twin-proven", "passed its twin test"),
                (any(b.get("edge", 0) > 0 for b in v.backtests), "Backtested", "beat holding in a saved backtest"),
                (v.power >= 5, "Powerhouse", "power 5 or more")):
            if ok:
                out.append((name, why))
        return out

    # ---------------- nightly retest ----------------
    def maybe_retest(self):
        if not self.settings.get("retest_nightly", True) or self.cfg.get("last_retest") == date.today().isoformat():
            return
        if not self.hof() or not self.hist or min(len(self.hist.get(c, [])) for c in self.session_coins) < 400:
            return
        self.cfg["last_retest"] = date.today().isoformat()
        E.save_json(CONFIG_PATH, self.cfg)
        self.retest_hof(quiet=True)

    def retest_hof(self, quiet=False):
        """Re-backtests every Hall of Fame bot on the latest prices (in the background). Bots that lose to just
        holding get a RUSTY badge until they pass again."""
        hof = list(self.hof())
        if not hof or not self.session_coins:
            return messagebox.showinfo("Retest", "No Hall of Fame bots, or no prices yet.") if not quiet else None
        closes = {c: list(self.hist[c]) for c in self.session_coins}
        extra = {c: {k: list(x) for k, x in self.hx[c].items()} for c in self.session_coins} if self.hx else None
        fee, slip = E.cost_model(self.settings)
        pct = self.settings["trade_pct"] / 100

        def work():
            res = []
            for b in hof:
                try:
                    r = E.backtest_bot(b, closes, extra, self.start_cash, fee, pct, slip)
                    res.append((E.bot_key(b), b, r))
                except Exception:
                    continue

            def done():
                rusty = 0
                for k, b, r in res:
                    rt = dict(date=date.today().isoformat(), edge=round(r["edge"] * 100, 2), ret=round(r["ret"] * 100, 2),
                              rusty=r["edge"] <= 0)
                    rusty += rt["rusty"]
                    E.update_saved(k, lambda e, rt=rt: e.__setitem__("retest", rt), self.version)
                self.__dict__.pop("_file_cache", None)
                msg = f"Hall of Fame retest: {len(res) - rusty} still sharp, {rusty} rusty (losing to just holding lately)."
                self.write(msg, "good" if not rusty else "warn")
                self.after_hof_change()
            self.q.put(("call", done))
        threading.Thread(target=work, daemon=True).start()
        self.write("Retesting the Hall of Fame on the latest prices...")

    # ---------------- backtest a bot ----------------
    BT_LENGTHS = (("1 day", 1440), ("3 days", 4320), ("1 week", 10080), ("2 weeks", 20160), ("30 days", 43200))
    BT_CANDLES = (("1 min", 60), ("5 min", 300), ("15 min", 900), ("1 hr", 3600))

    def _bt_sources(self):
        """Bots you can backtest: farm bots first, then Library bots. -> [(label, genes, farm bot or None, saved key)]"""
        out = []
        if self.tourn:
            for r in self.tourn.board():
                v = r["v"]
                out.append((f"Farm: {E.bot_nickname(v.key())} ({STRAT_LABELS.get(v.name, v.name)})", v, v, v.key()))
        seen = {x[3] for x in out}
        for b in self.library():
            k = E.bot_key(b)
            if k not in seen:
                out.append((f"Library: {b.get('nickname') or E.bot_nickname(k)} ({STRAT_LABELS.get(b['name'], b['name'])})", b, None, k))
        return out

    def open_backtest(self, v=None):
        srcs = self._bt_sources()
        if not srcs:
            return messagebox.showinfo("Backtest", "No bots yet: start the farm or save some bots to your Library first.")
        w = tk.Toplevel(self.root)
        w.title("Backtest a bot")
        w.geometry(f"960x{min(820, self.root.winfo_screenheight() - 80)}")
        w.configure(bg=BG)
        top = ttk.Frame(w, padding=10)
        top.pack(fill="x")
        names = [x[0] for x in srcs]
        pick = tk.StringVar(value=next((x[0] for x in srcs if x[2] is v), names[0]))
        ttk.Label(top, text="Bot").grid(row=0, column=0, sticky="w")
        ttk.Combobox(top, textvariable=pick, values=names, state="readonly", width=52).grid(row=0, column=1, columnspan=3, sticky="w", padx=6)
        length = tk.StringVar(value=self.cfg.get("bt_len", "3 days"))
        candle = tk.StringVar(value=self.cfg.get("bt_candle", "5 min"))
        ttk.Label(top, text="History").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(top, textvariable=length, values=[x[0] for x in self.BT_LENGTHS], state="readonly", width=10).grid(
            row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(top, text="Candles").grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Combobox(top, textvariable=candle, values=[x[0] for x in self.BT_CANDLES], state="readonly", width=8).grid(
            row=1, column=3, sticky="w", padx=6, pady=(6, 0))
        coins = list(self.session_coins or [c for c, x in self.coin_vars.items() if x.get()] or ["BTC"])
        ttk.Label(top, text=f"Coins: {', '.join(coins)}", foreground=MUTED).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        go = ttk.Button(top, text="Run backtest", style="Accent.TButton")
        go.grid(row=0, column=4, rowspan=2, padx=(16, 0))
        status = ttk.Label(top, text="", foreground=MUTED)
        status.grid(row=2, column=4, sticky="e")
        body = tk.Frame(w, bg=BG)
        body.pack(fill="both", expand=True, padx=10)
        summary = tk.Label(body, text="Pick a bot, a stretch of history and a candle size, then Run backtest.\n\n"
                                      "You get its result against just holding, a walk-forward check (does it work in every "
                                      "part of the history, not just one lucky stretch), a bad-luck stress test (same trades, "
                                      "random order), a double-fees test, and results per coin, per market mood and per rule.",
                           bg=BG, fg=TEXT, justify="left", anchor="nw", wraplength=920, font=("", 10))
        summary.pack(fill="x")
        cv = tk.Canvas(body, height=220, bg=CARD, highlightthickness=0)
        cv.pack(fill="both", expand=True, pady=(8, 0))
        detail = tk.Label(body, text="", bg=BG, fg=MUTED, justify="left", anchor="nw", wraplength=920, font=("", 9))
        detail.pack(fill="x", pady=(6, 0))
        btns = ttk.Frame(w, padding=10)
        btns.pack(fill="x")
        save_btn = ttk.Button(btns, text="Save to its memory", state="disabled")
        save_btn.pack(side="left")
        teach_btn = ttk.Button(btns, text="Save + teach the farm", state="disabled")
        teach_btn.pack(side="left", padx=6)
        Tip(save_btn, "Keeps what this test learned with the bot: how each of its studies and never-rules did, which "
                      "coins it struggles with, how it does in each market mood, and the test result. Its children inherit it.")
        Tip(teach_btn, "Also teaches the whole farm: losing buys go into the post-trade review, and if it beat holding "
                       "it becomes an elder that nudges future eggs.")
        ttk.Button(btns, text="Close", command=w.destroy).pack(side="right")
        state = {}

        def run():
            src = next(x for x in srcs if x[0] == pick.get())
            self.cfg.update(bt_len=length.get(), bt_candle=candle.get())
            mins = dict(self.BT_LENGTHS)[length.get()]
            gran = dict(self.BT_CANDLES)[candle.get()]
            count = min(5000, max(200, mins * 60 // gran))
            go.config(state="disabled")
            status.config(text="Loading prices...")
            fee, slip = E.cost_model(self.settings)
            pct, cash, quote = self.settings["trade_pct"] / 100, self.start_cash, self.quote

            def work():
                try:
                    data = {c: E.fetch_ohlcv(c, gran, count, quote) for c in coins}
                    L = min(len(d["c"]) for d in data.values())
                    closes = {c: d["c"][-L:] for c, d in data.items()}
                    extra = {c: {k: d[k][-L:] for k in ("h", "l", "v")} for c, d in data.items()}
                    self.q.put(("call", lambda: status.config(text="Testing...")))
                    r = E.backtest_bot(src[1], closes, extra, cash, fee, pct, slip)
                    self.q.put(("call", lambda: show(src, r, closes, gran)))
                except Exception as e:
                    self.q.put(("call", lambda e=e: (status.config(text=""), go.config(state="normal"),
                                                     messagebox.showerror("Backtest", f"Couldn't run it: {e}", parent=w))))
            threading.Thread(target=work, daemon=True).start()

        def show(src, r, closes, gran):
            if not w.winfo_exists():
                return
            go.config(state="normal")
            status.config(text="")
            state.update(src=src, r=r)
            segs = r["segments"]
            passed = sum(1 for x in segs if x[2] > 0)
            verdict = ("Solid: beat holding overall and in most parts of the history." if r["edge"] > 0 and passed >= max(1, len(segs) - 1)
                       else "Mixed: beat holding overall, but not in every part. Could be luck." if r["edge"] > 0
                       else "Weak: did worse than just holding on these prices.")
            wr = "-" if r["winrate"] is None else f"{r['winrate'] * 100:.0f}%"
            mc = "-" if r["mc_dd95"] is None else f"{r['mc_dd95'] * 100:.1f}%"
            summary.config(text=(
                f"{src[0]}  on {len(closes)} coin(s), {r['bars']} candles of {dict((b, a) for a, b in self.BT_CANDLES)[gran]}\n"
                f"Return {r['ret'] * 100:+.2f}%   |   just holding {r['hold'] * 100:+.2f}%   |   vs holding {r['edge'] * 100:+.2f}%   |   "
                f"worst drop {r['maxdd'] * 100:.1f}%   |   {r['trades']} trades, win rate {wr}   |   fees ${r['fees']:,.2f}\n"
                f"Walk-forward: beat holding in {passed} of {len(segs)} parts   |   double fees: {r['fees2x'] * 100:+.2f}%   |   "
                f"bad-luck drop (95%): {mc}\n"
                f"Verdict: {verdict}"))
            bits = []
            if r["by_coin"]:
                bits.append("Per coin: " + ", ".join(f"{c} {x[0]} trades ${x[1]:+,.2f}" for c, x in r["by_coin"].items()))
            if r["by_mood"]:
                bits.append("Per market mood: " + ", ".join(f"{MOOD_TEXT.get(m, m)} {x[1] * 100:+.2f}%" for m, x in r["by_mood"].items()))
            st = dict(r["layer_stats"], **r["veto_stats"])
            if st:
                bits.append("Rules: " + "; ".join(f"{E.VETOES[k]['name'] if k in E.VETOES else k} "
                                                   f"{'saved' if x[1] >= 0 else 'cost'} {abs(x[1]):.1f}% over {int(x[0])}"
                                                   for k, x in st.items()))
            if r["review"]:
                bits.append("Losing buys a never-rule would have stopped: " +
                            ", ".join(f"{E.VETOES[k]['name']} {x[0]}" for k, x in r["review"].items() if k in E.VETOES))
            bits.append("Parts (walk-forward): " + "  ".join(f"[{i + 1}] {a * 100:+.1f}% vs {h * 100:+.1f}%" for i, (a, h, _) in enumerate(segs)))
            detail.config(text="\n".join(bits))
            save_btn.config(state="normal")
            teach_btn.config(state="normal")
            draw()

        def draw(_=None):
            r = state.get("r")
            cv.delete("all")
            if not r:
                return
            W, H = cv.winfo_width(), cv.winfo_height()
            eq, he = r["eq"], r["hold_eq"]
            lo, hi = min(eq + he), max(eq + he)
            pad = (hi - lo) * 0.08 or 1
            lo, hi = lo - pad, hi + pad
            n = len(eq)
            X = lambda i: 60 + i * (W - 70) / max(1, n - 1)
            Y = lambda y: H - 16 - (y - lo) / (hi - lo) * (H - 30)
            for k in range(4):
                yv = lo + (hi - lo) * k / 3
                cv.create_line(60, Y(yv), W - 10, Y(yv), fill=LINE)
                cv.create_text(54, Y(yv), text=f"{yv:,.0f}", anchor="e", fill=MUTED, font=("", 8))
            segs = len(r["segments"]) or 1
            for k in range(1, segs):
                cv.create_line(X(k * (n - 1) / segs), 8, X(k * (n - 1) / segs), H - 14, fill=LINE, dash=(3, 3))
            step = max(1, n // 1200)
            cv.create_line(*[z for i in range(0, n, step) for z in (X(i), Y(he[i]))], fill="#9ca3af", width=1)
            cv.create_line(*[z for i in range(0, n, step) for z in (X(i), Y(eq[i]))], fill=ACCENT, width=2)
            for i, c, side, px in r["marks"][-300:]:
                j = i - r["start"] + 1
                if 0 <= j < n:
                    x, y = X(j), Y(eq[j])
                    cv.create_polygon(*((x, y + 2, x - 4, y + 9, x + 4, y + 9) if side == "buy" else (x, y - 2, x - 4, y - 9, x + 4, y - 9)),
                                      fill=GREEN if side == "buy" else RED, outline="")
            cv.create_text(W - 12, 6, anchor="ne", fill=MUTED, font=("", 8), text="blue = the bot, grey = just holding, dashes = walk-forward parts")
        cv.bind("<Configure>", draw)

        def save(teach):
            src, r = state["src"], state["r"]
            rec = None
            if src[2] is not None and self.tourn and src[2] in self.tourn.vars:
                rec = E.remember_backtest(src[2], r, self.tourn if teach else None)
            E.update_saved(src[3], lambda e: E.remember_backtest(e, r), self.version)
            if rec is None and teach and self.tourn:
                E.remember_backtest(dict(src[1]) if isinstance(src[1], dict) else src[1], r, self.tourn)
            self.__dict__.pop("_file_cache", None)
            self.write(f"Saved the backtest to {src[0].split(': ', 1)[-1]}'s memory" + (" and taught the farm." if teach else "."), "good")
            save_btn.config(state="disabled")
            teach_btn.config(state="disabled")
            self.save_farm(force=True)
        save_btn.config(command=lambda: save(False))
        teach_btn.config(command=lambda: save(True))
        go.config(command=run)
        if v is not None:
            run()

    def backtest_hof(self):
        """Backtests every Hall of Fame bot on the current prices and ranks them."""
        hof = list(self.hof())
        if not hof or not self.session_coins:
            return messagebox.showinfo("Backtest the Hall of Fame", "You need Hall of Fame bots and a running session (for prices).")
        w = tk.Toplevel(self.root)
        w.title("Hall of Fame backtest")
        w.geometry("900x480")
        bb = ttk.Frame(w, padding=8)
        bb.pack(side="bottom", fill="x")
        lbl = ttk.Label(w, text="Testing...", padding=8)
        lbl.pack(side="bottom", fill="x")
        t = self.make_tree(w, ("name", "strategy", "ret", "edge", "parts", "dd", "trades", "fees2x"),
                           (150, 180, 80, 90, 80, 80, 60, 90),
                           ("Bot", "Strategy", "Return", "vs holding", "Parts won", "Worst drop", "Trades", "2x fees"))
        closes = {c: list(self.hist[c]) for c in self.session_coins}
        extra = {c: {k: list(x) for k, x in self.hx[c].items()} for c in self.session_coins} if self.hx else None
        fee, slip = E.cost_model(self.settings)
        pct, cash = self.settings["trade_pct"] / 100, self.start_cash
        res = []

        def work():
            for b in hof:
                try:
                    res.append((b, E.backtest_bot(b, closes, extra, cash, fee, pct, slip)))
                except Exception:
                    continue
            self.q.put(("call", fill))

        def fill():
            if not w.winfo_exists():
                return
            for b, r in sorted(res, key=lambda x: -x[1]["edge"]):
                t.insert("", "end", values=(b.get("nickname") or E.bot_nickname(E.bot_key(b)), STRAT_LABELS.get(b["name"], b["name"]),
                                            f"{r['ret'] * 100:+.2f}%", f"{r['edge'] * 100:+.2f}%",
                                            f"{sum(1 for x in r['segments'] if x[2] > 0)}/{len(r['segments'])}",
                                            f"{r['maxdd'] * 100:.1f}%", r["trades"], f"{r['fees2x'] * 100:+.2f}%"),
                         tags=("up" if r["edge"] > 0 else "down",))
            t.tag_configure("up", foreground=GREEN)
            t.tag_configure("down", foreground=RED)
            lbl.config(text=f"{len(res)} bots tested on {len(closes)} coins, {min(len(x) for x in closes.values())} bars each.")

        def save_all():
            for b, r in res:
                E.update_saved(E.bot_key(b), lambda e, r=r: E.remember_backtest(e, r), self.version)
                if self.tourn:
                    live = next((v for v in self.tourn.vars if v.key() == E.bot_key(b)), None)
                    if live is not None:
                        E.remember_backtest(live, r, self.tourn)
            self.__dict__.pop("_file_cache", None)
            self.write(f"Saved {len(res)} Hall of Fame backtests to their memories.", "good")
        ttk.Button(bb, text="Save all to their memories", style="Accent.TButton", command=save_all).pack(side="left")
        ttk.Button(bb, text="Close", command=w.destroy).pack(side="right")
        threading.Thread(target=work, daemon=True).start()

    # ---------------- training camp ----------------
    TC_LENGTHS = (("1 day", 1440), ("3 days", 4320), ("1 week", 10080), ("2 weeks", 20160), ("30 days", 43200),
                  ("60 days", 86400), ("90 days", 129600), ("180 days", 259200))
    TC_CANDLES = (("1 min", 60), ("5 min", 300), ("15 min", 900), ("1 hr", 3600), ("6 hr", 21600))
    TC_MAX = 6000

    def open_training(self):
        if getattr(self, "tc_win", None) is not None and self.tc_win.winfo_exists():
            return self.tc_win.lift()
        w = self.tc_win = tk.Toplevel(self.root)
        w.title("Training camp")
        w.geometry(f"1000x{min(780, self.root.winfo_screenheight() - 80)}")
        w.configure(bg=BG)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="x")
        ttk.Label(f, text="Training camp", font=("", 13, "bold")).grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(f, foreground=MUTED, wraplength=940, justify="left", text=(
            "A whole farm lives through a stretch of past prices, as many times as you like: hatching, competing, breeding "
            "and learning the whole way (between passes the bots keep their genes and lessons, with fresh money). The most "
            "recent part is held back as a final exam: the best bots are frozen and tested on prices they never saw. "
            "Only the ones that beat just holding in training AND in the exam pass.")).grid(row=1, column=0, columnspan=6,
                                                                                           sticky="w", pady=(2, 10))
        c = self.cfg
        length = tk.StringVar(value=c.get("tc_len", "2 weeks"))
        candle = tk.StringVar(value=c.get("tc_candle", "15 min"))
        passes = tk.StringVar(value=str(c.get("tc_passes", 2)))
        size = tk.StringVar(value=str(c.get("tc_size", int(self.settings.get("max_bots", 30)))))
        exam = tk.StringVar(value=c.get("tc_exam", "25%"))
        start = tk.StringVar(value=c.get("tc_start", "Fresh eggs"))
        for idx, (lab, var, vals, wd) in enumerate((
                ("History", length, [x[0] for x in self.TC_LENGTHS], 9), ("Candles", candle, [x[0] for x in self.TC_CANDLES], 7),
                ("Passes", passes, ["1", "2", "3", "4", "5"], 3), ("Farm size", size, ["20", "30", "40", "60", "80", "120"], 4),
                ("Final exam", exam, ["None", "15%", "25%", "35%"], 6),
                ("Start from", start, ["Fresh eggs", "My current farm", "Hall of Fame", "Library"], 15))):
            r0, col = (2, idx) if idx < 3 else (4, idx - 3)
            ttk.Label(f, text=lab).grid(row=r0, column=col, sticky="w", padx=(0, 14))
            ttk.Combobox(f, textvariable=var, values=vals, state="readonly" if lab not in ("Farm size",) else "normal",
                         width=wd).grid(row=r0 + 1, column=col, sticky="w", padx=(0, 14))
        coins = list(self.session_coins or [k for k, x in self.coin_vars.items() if x.get()] or ["BTC", "ETH"])
        note = ttk.Label(f, text="", foreground=MUTED)
        note.grid(row=6, column=0, columnspan=6, sticky="w", pady=(6, 0))

        def update_note(*_):
            mins = dict(self.TC_LENGTHS)[length.get()]
            gran = dict(self.TC_CANDLES)[candle.get()]
            n = mins * 60 // gran
            cap = f" (capped to the latest {self.TC_MAX:,} candles, about {self.TC_MAX * gran / 86400:.1f} days)" if n > self.TC_MAX else ""
            note.config(text=f"Coins: {', '.join(coins)}   |   {min(n, self.TC_MAX):,} candles per coin{cap}   |   "
                             f"about {min(n, self.TC_MAX) * int(passes.get() or 1):,} farm decisions in total")
        for var in (length, candle, passes):
            var.trace_add("write", update_note)
        update_note()
        row = ttk.Frame(f)
        row.grid(row=7, column=0, columnspan=6, sticky="we", pady=(10, 0))
        go = ttk.Button(row, text="⚔ Start training", style="Accent.TButton")
        go.pack(side="left")
        stop_b = ttk.Button(row, text="Stop", state="disabled")
        stop_b.pack(side="left", padx=6)
        bar = ttk.Progressbar(row, length=360, mode="determinate")
        bar.pack(side="left", padx=10)
        status = ttk.Label(row, text="", foreground=MUTED)
        status.pack(side="left")
        bb = ttk.Frame(w, padding=10)
        bb.pack(side="bottom", fill="x")
        tbox = tk.Frame(w, bg=BG)
        tbox.pack(fill="both", expand=True, padx=12)
        tree = self.make_tree(tbox, ("name", "strategy", "pace", "train", "exam", "trades", "wr", "passed"),
                              (150, 190, 90, 110, 110, 60, 60, 80),
                              ("Bot", "Strategy", "Decides", "Training vs hold", "Exam vs hold", "Trades", "Win rate", "Result"))
        tree.configure(selectmode="extended")
        tree.tag_configure("pass", foreground=GREEN)
        tree.tag_configure("fail", foreground=MUTED)
        b_bring = ttk.Button(bb, text="Bring passed bots to my farm", state="disabled")
        b_teach = ttk.Button(bb, text="Teach my farm what it learned", state="disabled")
        b_lib = ttk.Button(bb, text="Save passed bots to the Library", state="disabled")
        for b in (b_bring, b_teach, b_lib):
            b.pack(side="left", padx=(0, 6))
        Tip(b_bring, "The bots that passed join your farm (as royal eggs, keeping what they learned) and compete on live prices. "
                     "Select rows first to bring only those.")
        Tip(b_teach, "Gives your farm the camp's memory: its elders (nudge future eggs), its post-trade review, its mutation "
                     "memory and its season champions (woken up when their market mood comes back).")
        Tip(b_lib, "Saves the passed bots to your Library, with their exam result in their memory.")
        ttk.Button(bb, text="Close", command=w.destroy).pack(side="right")
        st = dict(stop=False, res=None)

        def run():
            mins = dict(self.TC_LENGTHS)[length.get()]
            gran = dict(self.TC_CANDLES)[candle.get()]
            try:
                n_pass = min(5, max(1, int(passes.get())))
                cap = min(200, max(14, int(float(size.get()))))
            except ValueError:
                return messagebox.showerror("Training camp", "Farm size must be a number (14 to 200).", parent=w)
            ex = {"None": 0.0, "15%": 0.15, "25%": 0.25, "35%": 0.35}[exam.get()]
            self.cfg.update(tc_len=length.get(), tc_candle=candle.get(), tc_passes=n_pass, tc_size=cap, tc_exam=exam.get(),
                            tc_start=start.get())
            E.save_json(CONFIG_PATH, self.cfg)
            seeds = []
            if start.get() == "My current farm" and self.tourn:
                seeds = [dict(E._genes(v), size=v.size_mult) for v in self.tourn.vars]
            elif start.get() == "Hall of Fame":
                seeds = [dict(b) for b in self.hof()]
            elif start.get() == "Library":
                seeds = [dict(b) for b in self.library()]
            count = min(self.TC_MAX, max(300, mins * 60 // gran))
            opts = E.tournament_opts(self.settings)
            fee, slip = E.cost_model(self.settings)
            pct, cash, quote = self.settings["trade_pct"] / 100, self.start_cash, self.quote
            evolve, params = int(self.settings["evolve_every"]), self.settings["params"]
            retire = int(float(self.settings["retire_hours"]) * 3600 / gran)
            st["stop"] = False
            go.config(state="disabled")
            stop_b.config(state="normal")
            for b in (b_bring, b_teach, b_lib):
                b.config(state="disabled")
            tree.delete(*tree.get_children())
            status.config(text="Loading prices...")
            bar.config(value=0)

            def prog(done, total, text):
                self.q.put(("call", lambda: (bar.config(value=100 * done / max(1, total)),
                                             status.config(text=f"{done:,} of {total:,} decisions: {text}"))
                            if w.winfo_exists() else None))

            def work():
                try:
                    data = {}
                    for i, c_ in enumerate(coins):
                        self.q.put(("call", lambda i=i, c_=c_: status.config(text=f"Loading {c_} ({i + 1}/{len(coins)})...")
                                    if w.winfo_exists() else None))
                        data[c_] = E.fetch_ohlcv(c_, gran, count, quote)
                    L = min(len(d["c"]) for d in data.values())
                    closes = {c_: d["c"][-L:] for c_, d in data.items()}
                    extra = {c_: {k: d[k][-L:] for k in ("h", "l", "v")} for c_, d in data.items()}
                    res = E.train_farm(closes, extra, opts, cap=cap, passes=n_pass, exam_frac=ex, seed_genes=seeds,
                                       start_cash=cash, fee=fee, pct=pct, evolve=evolve, params=params,
                                       retire_steps=retire, progress=prog, stop=lambda: st["stop"])
                    res["gran"] = gran
                    self.q.put(("call", lambda: show(res)))
                except E.TrainingStopped:
                    self.q.put(("call", lambda: finish("Stopped.")))
                except Exception as e:
                    self.q.put(("call", lambda e=e: (finish(""), messagebox.showerror("Training camp", f"Couldn't finish: {e}", parent=w))
                                if w.winfo_exists() else None))
            threading.Thread(target=work, daemon=True).start()

        def finish(text):
            if not w.winfo_exists():
                return
            go.config(state="normal")
            stop_b.config(state="disabled")
            status.config(text=text)

        def show(res):
            if not w.winfo_exists():
                return
            st["res"] = res
            bar.config(value=100)
            t = res["tourn"]
            passed = sum(1 for r in res["results"] if r["passed"])
            gen = max((v.gen for v in t.vars), default=0)
            finish(f"Done: {res['passes_done']} pass(es) over {res['train_bars']:,} candles, {gen} generations, "
                   f"{len(t.lineage)} bots lived. " + (f"Exam on the last {res['exam_bars']:,} candles: {passed} passed "
                                                     f"(holding did {res['exam_hold'] * 100:+.1f}%)." if res["exam_bars"] else ""))
            for i, r in enumerate(res["results"]):
                g = r["genes"]
                tree.insert("", "end", iid=str(i), tags=("pass" if r["passed"] else "fail",), values=(
                    E.bot_nickname(r["v"].key()), STRAT_LABELS.get(g["name"], g["name"]), E.fmt_bar(g["tf"] * res["gran"] / 60),
                    pct_txt(r["train_edge"]), pct_txt(r["exam_edge"]), r["trades"],
                    "-" if r["winrate"] is None else f"{r['winrate'] * 100:.0f}%",
                    "✓ passed" if r["passed"] else ("no exam" if r["passed"] is None else "failed")))
            for b in (b_bring, b_teach, b_lib):
                b.config(state="normal")
            self.write(f"⚔ Training camp finished: {passed} of {len(res['results'])} top bots passed the exam.", "good")

        def chosen():
            res = st["res"]
            sel = [res["results"][int(i)] for i in tree.selection()]
            return sel or [r for r in res["results"] if r["passed"] or (r["passed"] is None and (r["train_edge"] or 0) > 0)]

        def bring():
            if not self.tourn or not self.seeded:
                return messagebox.showinfo("Training camp", "Start your farm first (Wildcard), then bring the bots over.", parent=w)
            picks = chosen()
            if not picks:
                return messagebox.showinfo("Training camp", "No bots passed. Select rows to bring some anyway.", parent=w)
            closes = {c_: self.hist[c_] for c_ in self.session_coins}
            n = 0
            for r in picks:
                g = r["genes"]
                v = self.tourn.add_variant(g["name"], g["params"], closes, 1.0, note="From the training camp:", tf=g["tf"],
                                           origin="training camp", risk=g["risk"], traits=g["traits"], vetoes=g["vetoes"],
                                           layers=g["layers"])
                if v:
                    v.royal = True
                    v.layer_stats = {k: list(x) for k, x in r["layer_stats"].items()}
                    v.veto_stats = {k: list(x) for k, x in r["veto_stats"].items() if k in v.vetoes}
                    if r["exam_edge"] is not None:
                        v.backtests.append(dict(date=datetime.now().strftime("%Y-%m-%d %H:%M"), bars=st["res"]["exam_bars"],
                                                ret=round(r["exam_ret"] * 100, 2), edge=round(r["exam_edge"] * 100, 2),
                                                maxdd=None, trades=r["trades"], winrate=None, segments_passed=int(bool(r["passed"])),
                                                segments=1, fees2x=None, mc_dd95=None))
                    n += 1
            self.tourn.cap = max(self.tourn.cap, len(self.tourn.vars))
            self.flush_events()
            self.refresh_board()
            self.write(f"⚔ {n} bots from the training camp joined your farm. They still have to prove themselves on live prices.", "good")

        def teach():
            if not self.tourn:
                return messagebox.showinfo("Training camp", "Start your farm first (Wildcard).", parent=w)
            E.teach_from(self.tourn, st["res"]["tourn"])
            self.save_farm(force=True)
            self.write("Your farm learned from the training camp: elders, post-trade review, mutation memory and season champions.", "good")

        def save_lib():
            picks = chosen()
            if not picks:
                return messagebox.showinfo("Training camp", "No bots passed. Select rows to save some anyway.", parent=w)
            t = st["res"]["tourn"]
            ids = {id(r["v"]) for r in picks}
            rows = [dict(r, bar_minutes=st["res"]["gran"] / 60) for r in t.board() if id(r["v"]) in ids]
            entries = E.export_bots(rows, self.version, note="training camp graduate")["bots"]
            by_key = {r["v"].key(): r for r in picks}
            for e in entries:
                r = by_key.get(E.bot_key(e))
                if r and r["exam_edge"] is not None:
                    e["backtests"] = [dict(date=datetime.now().strftime("%Y-%m-%d %H:%M"), bars=st["res"]["exam_bars"],
                                           ret=round(r["exam_ret"] * 100, 2), edge=round(r["exam_edge"] * 100, 2),
                                           trades=r["trades"], segments_passed=int(bool(r["passed"])), segments=1)]
            self.catch_rows(entries, "training camp")
        go.config(command=run)
        stop_b.config(command=lambda: (st.__setitem__("stop", True), status.config(text="Stopping...")))
        b_bring.config(command=bring)
        b_teach.config(command=teach)
        b_lib.config(command=save_lib)

    # ---------------- free roam (self-study) ----------------
    ROAM_CANDLES = (300, 900, 3600, 21600)  # 5 min, 15 min, 1 hr, 6 hr: the farm picks one each study

    def roam_path(self):
        return os.path.join(E.HOME_DIR, "free-roam.json")

    def start_free_roam(self):
        """Free roam: the farm goes back in time on its own. With Free roam mode ON it studies back to back; otherwise
        (Settings > Learning) it studies quietly now and then while idle. Each study either backtests some of its own
        bots on a random stretch of history, or runs a small practice farm there; every lesson is saved."""
        if getattr(self, "_roam_thread", None) and self._roam_thread.is_alive():
            return
        saved = E.load_json(self.roam_path(), {}) or {}
        self.roam_log = list(saved.get("log") or [])[-300:]
        self.roam_n = int(saved.get("n", len(self.roam_log)))
        self.roam_now = None

        def loop():
            k = 0
            while True:
                time.sleep(2)
                try:
                    s_ = self.settings
                    t = self.tourn
                    mode = bool(self.cfg.get("roam_on"))
                    if t is None or not self.session_coins:
                        continue
                    if not mode:
                        if not s_.get("free_roam", True) or (self.running and not s_.get("free_roam_running", False)):
                            continue
                    wait = 3 if mode else (900 if self.running else 120)
                    if time.time() - getattr(self, "_roam_last", 0) < wait:
                        continue
                    k += 1
                    if k % 4 == 0:
                        self._roam_camp(t)
                    else:
                        self._roam_check(t)
                    self._roam_last = time.time()
                except E.TrainingStopped:  # the farm changed (reset, new session): just move on
                    self.roam_now = None
                except Exception:
                    self.roam_now = None
                    self.q.put(("log", (self._record_error("free roam") or "Free roam hit a problem; it will try again.", "warn")))
                    time.sleep(30)
        self._roam_thread = threading.Thread(target=loop, daemon=True)
        self._roam_thread.start()

    def _roam_pick(self, rng, min_bars=400):
        """The farm chooses where to learn: a candle size, then a stretch of history it has studied least."""
        gran = rng.choice(self.ROAM_CANDLES)
        coins = list(self.session_coins)
        self.roam_now = dict(kind="choosing", text="Choosing where to learn next...", gran=gran, coins=coins, t0=time.time())
        data = {c: E.fetch_ohlcv(c, gran, 3000, self.quote) for c in coins}
        L = min(len(d["c"]) for d in data.values())
        span = min(L, rng.randint(min_bars, max(min_bars, min(L, 1500))))
        now = time.time()
        covered = [(x["start"], x["end"]) for x in self.roam_log[-120:]]
        best, best_score = 0, None
        for _ in range(8):  # prefer stretches it hasn't studied yet
            a = rng.randint(0, max(0, L - span))
            st, en = now - (L - a) * gran, now - (L - a - span) * gran
            overlap = sum(max(0, min(en, e) - max(st, s0)) for s0, e in covered)
            if best_score is None or overlap < best_score:
                best, best_score = a, overlap
        a = best
        closes = {c: d["c"][a:a + span] for c, d in data.items()}
        extra = {c: {x: d[x][a:a + span] for x in ("h", "l", "v")} for c, d in data.items()}
        start, end = now - (L - a) * gran, now - (L - a - span) * gran
        hold = sum(closes[c][-1] / closes[c][0] for c in coins) / len(coins) - 1
        moods = [E.market_mood(closes[c])[0] for c in coins]
        known = [m for m in moods if m != "unknown"]
        prev = [sum(closes[c][i] / closes[c][0] for c in coins) / len(coins) for i in range(0, span, max(1, span // 300))]
        info = dict(gran=gran, coins=coins, start=start, end=end, bars=span, hold=hold, preview=prev,
                    mood=max(set(known), key=known.count) if known else "unknown", oldest=now - L * gran)
        return closes, extra, info

    @staticmethod
    def _when(ts):
        return datetime.fromtimestamp(ts).strftime("%b %d %H:%M")

    def _roam_finish(self, rec):
        self.roam_log.append(rec)
        del self.roam_log[:-300]
        self.roam_n = getattr(self, "roam_n", 0) + 1
        self.roam_now = None
        try:
            E.save_json(self.roam_path(), dict(n=self.roam_n, log=self.roam_log))
        except OSError:
            pass

    def _roam_check(self, t):
        """Self-backtest: the leader and a few others test themselves on the chosen stretch."""
        import random as _r
        rng = _r.Random()
        vars_ = list(t.vars)
        if not vars_:
            return
        picks = list(dict.fromkeys(([t.active[0]] if t.active else []) + rng.sample(vars_, min(3, len(vars_)))))
        closes, extra, info = self._roam_pick(rng)
        names = [E.bot_nickname(v.key()) for v in picks]
        self.roam_now = dict(info, kind="Self-test", text=f"{len(picks)} bots are testing themselves", bots=names, done=0,
                             total=len(picks), t0=time.time())
        fee, slip = E.cost_model(self.settings)
        out = []
        for i, v in enumerate(picks):
            if self.tourn is not t:
                return
            self.roam_now.update(done=i, text=f"{names[i]} is testing itself ({i + 1} of {len(picks)})")
            out.append((v, E.backtest_bot(v, closes, extra, self.start_cash, fee, self.settings["trade_pct"] / 100, slip)))

        def apply():
            if self.tourn is not t:
                return
            res = []
            for v, r in out:
                if v in t.vars:
                    E.remember_backtest(v, r, t)
                res.append((E.bot_nickname(v.key()), round(r["edge"] * 100, 2), r["trades"]))
            good = sum(1 for x in res if x[1] > 0)
            rec = dict(time=time.time(), kind="Self-test", start=info["start"], end=info["end"], gran=info["gran"],
                       bars=info["bars"], hold=round(info["hold"] * 100, 2), mood=info["mood"], results=res,
                       summary=f"{good} of {len(res)} beat holding; lessons saved to their memories")
            self._roam_finish(rec)
            if not self.cfg.get("roam_on"):
                self.write(f"◇ Free roam: {len(res)} bots tested themselves on {self._when(info['start'])} to "
                           f"{self._when(info['end'])}; {good} beat holding. Lessons saved.")
        self.q.put(("call", apply))

    def _roam_camp(self, t):
        """A practice farm (seeded with the farm's leaders) lives through the chosen stretch; your farm learns its
        lessons and adopts its best graduate."""
        import random as _r
        rng = _r.Random()
        closes, extra, info = self._roam_pick(rng, 700)
        seeds = [E._genes(v) for v in (t.active[:4] or t.vars[:4])]
        self.roam_now = dict(info, kind="Practice farm", text="A practice farm is living through this stretch",
                             bots=[E.bot_nickname(v.key()) for v in (t.active[:4] or t.vars[:4])], done=0, total=1, t0=time.time())
        fee, slip = E.cost_model(self.settings)

        def prog(done, total, text):
            if self.roam_now:
                self.roam_now.update(done=done, total=total, text=f"Practice farm: {text}")
        res = E.train_farm(closes, extra, E.tournament_opts(self.settings), cap=20, passes=1, exam_frac=0.25,
                           seed_genes=seeds, start_cash=self.start_cash, fee=fee, pct=self.settings["trade_pct"] / 100,
                           evolve=int(self.settings["evolve_every"]), params=self.settings["params"], progress=prog,
                           stop=lambda: self.tourn is not t)

        def apply():
            if self.tourn is not t:
                return
            E.teach_from(t, res["tourn"])
            passed = [r for r in res["results"] if r["passed"]]
            grad = ""
            if passed and self.seeded and len(t.vars) < t.cap + 2:
                g = passed[0]["genes"]
                v = t.add_variant(g["name"], g["params"], {c: self.hist[c] for c in self.session_coins}, 1.0,
                                  note="Free roam graduate:", tf=g["tf"], origin="free roam", risk=g["risk"],
                                  traits=g["traits"], vetoes=g["vetoes"], layers=g["layers"])
                if v:
                    v.layer_stats = {k: list(x) for k, x in passed[0]["layer_stats"].items()}
                    grad = E.bot_nickname(v.key())
                    self.refresh_board()
            gen = max((v.gen for v in res["tourn"].vars), default=0)
            rec = dict(time=time.time(), kind="Practice farm", start=info["start"], end=info["end"], gran=info["gran"],
                       bars=info["bars"], hold=round(info["hold"] * 100, 2), mood=info["mood"],
                       results=[(E.bot_nickname(r["v"].key()), round((r["exam_edge"] or 0) * 100, 2), r["trades"]) for r in passed[:5]],
                       graduate=grad, summary=f"{gen} generations, {len(passed)} passed the exam"
                                              + (f"; {grad} joined your farm" if grad else "") + "; farm memory updated")
            self._roam_finish(rec)
            if not self.cfg.get("roam_on"):
                self.write(f"◇ Free roam: a practice farm lived through {self._when(info['start'])} to "
                           f"{self._when(info['end'])}. {rec['summary']}.")
        self.q.put(("call", apply))

    def toggle_roam(self, on=None):
        on = (not self.cfg.get("roam_on")) if on is None else on
        self.cfg["roam_on"] = bool(on)
        E.save_json(CONFIG_PATH, self.cfg)
        self._roam_last = 0
        self.write("◇ Free roam mode ON: the farm is going back in time to learn on its own." if on else
                   "◇ Free roam mode OFF.", "good" if on else None)
        if hasattr(self, "roam_btn"):
            self.roam_btn.config(text="◇ Free roam: ON" if on else "◇ Free roam")
        self.start_free_roam()

    def open_roam(self):
        """The Free roam screen: where in the past the farm is learning right now, what it has covered, and what it found."""
        if getattr(self, "roam_win", None) is not None and self.roam_win.winfo_exists():
            return self.roam_win.lift()
        self.start_free_roam()
        w = self.roam_win = tk.Toplevel(self.root)
        w.title("Free roam")
        w.geometry(f"1080x{min(860, self.root.winfo_screenheight() - 80)}")
        w.configure(bg=BG)
        top = tk.Frame(w, bg=BG, padx=12, pady=10)
        top.pack(fill="x")
        tk.Label(top, text="◇ Free roam", bg=BG, fg=TEXT, font=("", 15, "bold")).pack(side="left")
        tog = tk.Button(top, relief="flat", bd=0, padx=16, pady=5, cursor="hand2", font=("", 10, "bold"), fg="white",
                        activeforeground="white")
        tog.pack(side="left", padx=14)
        state_lbl = tk.Label(top, text="", bg=BG, fg=MUTED, font=("", 9), justify="left", anchor="w")
        state_lbl.pack(side="left", fill="x", expand=True)
        tog.config(command=lambda: (self.toggle_roam(), paint()))
        tk.Label(w, bg=BG, fg=MUTED, wraplength=1040, justify="left", anchor="w", padx=12, text=(
            "The farm goes back in time on its own. For every study it picks a candle size (5 min to 6 hours) and a stretch "
            "of the past it has studied least, then either lets a few of its bots test themselves there, or sends a small "
            "practice farm through it. Every lesson is saved: rule evidence and weak coins to each bot's memory, the "
            "practice farm's elders, season champions and post-trade review to your farm, and its best graduate joins "
            "your farm. Your account never trades in Free roam.")).pack(fill="x")
        now_f = tk.Frame(w, bg=CARD, padx=12, pady=8, highlightthickness=1, highlightbackground=LINE)
        now_f.pack(fill="x", padx=12, pady=(8, 0))
        now_t = tk.Label(now_f, text="", bg=CARD, fg=TEXT, font=("", 11, "bold"), anchor="w", justify="left")
        now_t.pack(fill="x")
        now_d = tk.Label(now_f, text="", bg=CARD, fg=MUTED, anchor="w", justify="left", wraplength=1000)
        now_d.pack(fill="x")
        pbar = ttk.Progressbar(now_f, mode="determinate")
        pbar.pack(fill="x", pady=(4, 0))
        tk.Label(w, text="WHERE IT IS LEARNING  (the last few months; shaded = already studied, bright = right now)",
                 bg=BG, fg=ACCENT, font=("", 8, "bold"), anchor="w", padx=12).pack(fill="x", pady=(10, 0))
        tl = tk.Canvas(w, height=74, bg=CARD, highlightthickness=0)
        tl.pack(fill="x", padx=12)
        tk.Label(w, text="THE STRETCH BEING STUDIED  (average of your coins; grey line = where it started)",
                 bg=BG, fg=ACCENT, font=("", 8, "bold"), anchor="w", padx=12).pack(fill="x", pady=(8, 0))
        pc = tk.Canvas(w, height=130, bg=CARD, highlightthickness=0)
        pc.pack(fill="x", padx=12)
        stats = tk.Label(w, text="", bg=BG, fg=TEXT, anchor="w", justify="left", padx=12, font=("", 9))
        stats.pack(fill="x", pady=(8, 0))
        learn = tk.Label(w, text="", bg=BG, fg=MUTED, anchor="w", justify="left", padx=12, wraplength=1040, font=("", 9))
        learn.pack(fill="x", pady=(2, 0))
        tk.Label(w, text="STUDY LOG", bg=BG, fg=ACCENT, font=("", 8, "bold"), anchor="w", padx=12).pack(fill="x", pady=(8, 0))
        box = tk.Frame(w, bg=BG)
        box.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        log = self.make_tree(box, ("when", "kind", "stretch", "candles", "market", "result"), (100, 95, 220, 60, 190, 380),
                             ("When", "Study", "Stretch of the past", "Candles", "Market then", "What it learned"))
        st = dict(n=-1)

        def paint():
            if not w.winfo_exists():
                return
            on = bool(self.cfg.get("roam_on"))
            tog.config(text="ON  —  click to stop" if on else "OFF  —  click to start", bg=GREEN if on else "#4b5563",
                       activebackground=GREEN if on else "#4b5563")
            if not self.tourn:
                state_lbl.config(text="Start your farm once (Wildcard) so there are bots to study with; it can be paused "
                                      "afterwards and Free roam keeps learning.")
            elif on:
                state_lbl.config(text="Free roam mode: studying back to back" + (" (alongside live prices)" if self.running else
                                                                                 " (the farm is paused, so it has the computer to itself)"))
            else:
                state_lbl.config(text="Off. " + ("It still studies now and then while idle (Settings > Learning)."
                                                 if self.settings.get("free_roam", True) else "Idle studies are off too."))
            cur = self.roam_now
            if cur and cur.get("start"):
                now_t.config(text=f"{cur['kind']}: {self._when(cur['start'])}  →  {self._when(cur['end'])}   "
                                  f"({(cur['end'] - cur['start']) / 86400:.1f} days, {E.fmt_bar(cur['gran'] / 60)} candles)")
                now_d.config(text=f"{cur.get('text', '')}.   Coins: {', '.join(cur['coins'])}.   Market then: "
                                  f"{MOOD_TEXT.get(cur['mood'], cur['mood'])}, just holding {cur['hold'] * 100:+.1f}%."
                                  + (f"   Bots: {', '.join(cur.get('bots', [])[:6])}" if cur.get("bots") else "")
                                  + f"   Running for {int(time.time() - cur['t0'])} s.")
                pbar.config(value=100 * cur.get("done", 0) / max(1, cur.get("total", 1)))
            elif cur:
                now_t.config(text=cur.get("text", ""))
                now_d.config(text="")
                pbar.config(value=0)
            else:
                now_t.config(text="Resting between studies" if (on or self.settings.get("free_roam", True)) and self.tourn else "Not studying")
                now_d.config(text="")
                pbar.config(value=0)
            draw_timeline(cur)
            draw_price(cur)
            L_ = self.roam_log
            days = covered_days()
            res = [x for r in L_ for x in r.get("results", [])]
            best = max(res, key=lambda x: x[1]) if res else None
            grads = [r["graduate"] for r in L_ if r.get("graduate")]
            stats.config(text=(f"Studies: {getattr(self, 'roam_n', 0)}   |   past covered: {days:.1f} days   |   bot self-tests: "
                               f"{sum(len(r.get('results', [])) for r in L_ if r['kind'] == 'Self-test')}   |   practice farms: "
                               f"{sum(1 for r in L_ if r['kind'] == 'Practice farm')}   |   graduates adopted: {len(grads)}"
                               + (f"   |   best find: {best[0]} {best[1]:+.1f}% vs holding" if best else "")))
            t = self.tourn
            if t:
                rv = sorted(((k, x) for k, x in t.review.items() if k in E.VETOES), key=lambda kv: -kv[1][0])[:3]
                ms = sorted(((k, x) for k, x in t.mut_stats.items() if x[0] >= 5), key=lambda kv: -(kv[1][1] / kv[1][0]))[:3]
                moods = {}
                for r in L_[-60:]:
                    for _, e, _ in r.get("results", []):
                        moods.setdefault(r.get("mood"), []).append(e)
                mtxt = ", ".join(f"{MOOD_TEXT.get(m, m)} {sum(v) / len(v):+.1f}%" for m, v in moods.items() if m and v)
                learn.config(text=(
                    "What the farm is learning:  "
                    + (f"never-rules that would have stopped the most losing buys: "
                       + ", ".join(f"{E.VETOES[k]['name']} ({x[0]})" for k, x in rv) + ".  " if rv else "")
                    + (f"Changes that make winners most often: " + ", ".join(f"{k} ({x[1] / x[0] * 100:.0f}%)" for k, x in ms) + ".  " if ms else "")
                    + (f"Season champions known: {', '.join(MOOD_TEXT.get(m, m) for m in t.champions)}.  " if t.champions else "")
                    + (f"Average result by market: {mtxt}." if mtxt else "")))
            if len(L_) != st["n"]:
                st["n"] = len(L_)
                log.delete(*log.get_children())
                for r in reversed(L_[-200:]):
                    ex = r["summary"] + (": " + ", ".join(f"{n} {e:+.1f}%" for n, e, _ in r.get("results", [])[:4]) if r.get("results") else "")
                    log.insert("", "end", values=(datetime.fromtimestamp(r["time"]).strftime("%b %d %H:%M"), r["kind"],
                                                  f"{self._when(r['start'])} → {self._when(r['end'])}",
                                                  E.fmt_bar(r["gran"] / 60), f"{MOOD_TEXT.get(r['mood'], r['mood'])} ({r['hold']:+.1f}%)", ex))
            w.after(1000, paint)

        def covered_days():
            iv = sorted((r["start"], r["end"]) for r in self.roam_log)
            tot, cs, ce = 0.0, None, None
            for a, b in iv:
                if ce is None or a > ce:
                    if ce is not None:
                        tot += ce - cs
                    cs, ce = a, b
                else:
                    ce = max(ce, b)
            if ce is not None:
                tot += ce - cs
            return tot / 86400

        def draw_timeline(cur):
            tl.delete("all")
            W, H = tl.winfo_width(), tl.winfo_height()
            now = time.time()
            oldest = min([r["start"] for r in self.roam_log] + ([cur["oldest"]] if cur and cur.get("oldest") else []) + [now - 30 * 86400])
            X = lambda ts: 10 + (ts - oldest) / max(1, now - oldest) * (W - 20)
            tl.create_rectangle(10, 26, W - 10, 46, fill=BG, outline=LINE)
            for r in self.roam_log:
                tl.create_rectangle(X(r["start"]), 27, max(X(r["start"]) + 2, X(r["end"])), 45, fill="#1d4ed8" if DARK else "#93c5fd",
                                    outline="", stipple="gray50")
            if cur and cur.get("start"):
                x0, x1 = X(cur["start"]), max(X(cur["start"]) + 3, X(cur["end"]))
                tl.create_rectangle(x0, 22, x1, 50, fill="#f59e0b", outline="")
                tl.create_text((x0 + x1) / 2, 12, text="studying now", fill="#f59e0b", font=("", 8, "bold"))
            span = now - oldest
            step = 86400 * (1 if span < 10 * 86400 else 7 if span < 120 * 86400 else 30)
            ts = now
            while ts > oldest:
                x = X(ts)
                tl.create_line(x, 46, x, 52, fill=MUTED)
                tl.create_text(x, 62, text="now" if ts == now else datetime.fromtimestamp(ts).strftime("%b %d"), fill=MUTED, font=("", 7))
                ts -= step

        def draw_price(cur):
            pc.delete("all")
            W, H = pc.winfo_width(), pc.winfo_height()
            if not cur or not cur.get("preview"):
                pc.create_text(W // 2, H // 2, text="The stretch appears here while the farm studies it.", fill=MUTED)
                return
            pv = cur["preview"]
            lo, hi = min(pv), max(pv)
            pad = (hi - lo) * 0.1 or 0.01
            lo, hi = lo - pad, hi + pad
            X = lambda i: 50 + i * (W - 60) / max(1, len(pv) - 1)
            Y = lambda y: H - 12 - (y - lo) / (hi - lo) * (H - 24)
            pc.create_line(50, Y(1.0), W - 10, Y(1.0), fill=LINE, dash=(3, 3))
            pc.create_line(*[z for i, y in enumerate(pv) for z in (X(i), Y(y))], fill=GREEN if pv[-1] >= 1 else RED, width=2)
            for y in (lo + pad, hi - pad):
                pc.create_text(46, Y(y), text=f"{(y - 1) * 100:+.1f}%", anchor="e", fill=MUTED, font=("", 7))
            pc.create_text(52, 8, anchor="nw", text=self._when(cur["start"]), fill=MUTED, font=("", 7))
            pc.create_text(W - 12, 8, anchor="ne", text=self._when(cur["end"]), fill=MUTED, font=("", 7))
            if cur.get("total") and cur.get("kind") == "Practice farm":
                x = X(int((len(pv) - 1) * min(1.0, cur.get("done", 0) / max(1, cur["total"]) / 0.75)))
                pc.create_line(x, 4, x, H - 4, fill="#f59e0b", width=2)
                pc.create_text(x + 4, H - 6, anchor="sw", text="the practice farm is here", fill="#f59e0b", font=("", 7, "bold"))
        paint()

    # ================= Slow farm: hourly candles, holds of days to weeks =================
    SLOW_GRAN = 3600   # one candle per hour
    SLOW_HISTORY = 3000  # about four months of hourly candles to learn from

    def slow_path(self):
        return os.path.join(E.HOME_DIR, "farm-slow.json")

    def build_slow_tab(self, parent):
        self.slow = dict(t=None, wallet=None, curve=[], hist={}, hx={}, last_ts=0, busy=False, coins=[], status="")
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", padx=8, pady=(8, 2))
        self.slow_btn = ttk.Button(top, text="▶ Start the slow farm", style="Accent.TButton", command=self.slow_toggle)
        self.slow_btn.pack(side="left")
        tb = ttk.Button(top, text="⚔ Train it on the past", style="Plain.TButton", command=self.slow_train)
        tb.pack(side="left", padx=6)
        Tip(tb, "Sends the slow farm through its months of hourly history twice more (keeping what it learned), then "
                "tests the best on the most recent weeks. Graduates join it.")
        for txt, cmd in (("◉ Save selected to Library", self.slow_catch), ("★ Add selected to Hall of Fame", self.slow_hof),
                         ("Reset slow farm", self.slow_reset)):
            ttk.Button(top, text=txt, style="Plain.TButton", command=cmd).pack(side="left", padx=(0, 6))
        self.slow_trade_var = tk.BooleanVar(value=bool(self.settings.get("slow_trades_account", False)))
        cb = ttk.Checkbutton(top, text="Let it trade my account too", variable=self.slow_trade_var, command=self._slow_trade_setting)
        cb.pack(side="right")
        Tip(cb, "Off: the slow farm only uses its own pretend wallet. On: when its winners agree on a new hourly candle, "
                "your account follows them too (while the bot is running), with the reason 'slow farm'.")
        tk.Label(parent, bg=BG, fg=MUTED, anchor="w", justify="left", padx=10, wraplength=1000, text=(
            "A second, separate farm of slow dragons. They decide on hourly candles, from every hour up to every two and a "
            "half days, hold for days to weeks, and can aim for much bigger moves (targets up to 100%, stops up to 30%), so "
            "fees matter far less. They learn from about four months of history first, then carry on with each new hour. "
            "They never mix with your fast farm, and only trade your account if you tick the box.")).pack(fill="x")
        self.slow_lbl = tk.Label(parent, text="", bg=CARD, fg=TEXT, anchor="w", justify="left", padx=10, pady=5, font=("", 9, "bold"))
        self.slow_lbl.pack(fill="x", padx=8, pady=(6, 4))
        self.slow_cv = tk.Canvas(parent, height=150, bg=CARD, highlightthickness=0)
        self.slow_cv.pack(fill="x", padx=8)
        self.slow_cv.bind("<Configure>", lambda e: self.later(self.slow_draw, 200))
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="both", expand=True, padx=8, pady=(6, 6))
        self.slow_tree = self.make_tree(box, ("name", "strategy", "pace", "status", "recent", "edge", "life", "trades", "holds", "stops"),
                                        (130, 170, 90, 120, 70, 80, 80, 55, 90, 190),
                                        ("Name", "Strategy", "Decides", "Status", "Recent", "vs holding", "Lifetime", "Trades",
                                         "Typical hold", "Own stop / target"))
        self.slow_tree.configure(selectmode="extended")
        for tag, col in (("win", GREEN), ("shaky", "#f59e0b"), ("stopped", MUTED), ("learning", MUTED)):
            self.slow_tree.tag_configure(tag, foreground=col)
        if self.cfg.get("slow_on"):
            self.root.after(6000, lambda: self.slow_start(quiet=True))
        self.slow_refresh()

    def _slow_trade_setting(self):
        self.settings["slow_trades_account"] = bool(self.slow_trade_var.get())
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)

    def slow_toggle(self):
        if self.cfg.get("slow_on"):
            self.cfg["slow_on"] = False
            E.save_json(CONFIG_PATH, self.cfg)
            self.slow_save()
            self.write("◇ Slow farm paused. It keeps everything it learned.")
            self.slow_refresh()
        else:
            self.slow_start()

    def slow_coins(self):
        return list(self.session_coins or [c for c, v in self.coin_vars.items() if v.get()] or ["BTC", "ETH"])

    def slow_start(self, quiet=False):
        """Loads (or hatches and warms up) the slow farm, then follows each new hourly candle."""
        sl = self.slow
        self.cfg["slow_on"] = True
        E.save_json(CONFIG_PATH, self.cfg)
        if sl["busy"]:
            return
        sl["busy"] = True
        sl["status"] = "Loading hourly history..."
        self.slow_refresh()
        saved = E.load_json(self.slow_path(), {}) or {}
        coins = list(saved.get("coins") or []) if sl["t"] is None and saved.get("farm") else (sl["coins"] or self.slow_coins())
        quote = self.cur_quote()
        fee, slip = E.cost_model(self.settings)
        pct, cash = self.settings["trade_pct"] / 100, self.start_cash or 1000.0
        opts = dict(E.tournament_opts(self.settings), risk_caps=dict(E.SLOW_RISK), window=72)
        cap = int(self.settings.get("slow_size", 30))

        def make():
            return E.Tournament(coins, cash, fee, pct, 24, window=72, params=self.settings["params"], opts=opts, cap=cap,
                                retire_steps=24 * 14, risk_caps=E.SLOW_RISK)

        def work():
            try:
                hist, hx, ts = {}, {}, {}
                for c in coins:
                    d = E.fetch_ohlcv(c, self.SLOW_GRAN, self.SLOW_HISTORY, quote)
                    n = len(d["c"])
                    t = list(d.get("t") or [])
                    if len(t) != n:  # older engine without candle times: assume one per hour up to now
                        top = int(time.time()) // self.SLOW_GRAN * self.SLOW_GRAN
                        t = [top - (n - 1 - i) * self.SLOW_GRAN for i in range(n)]
                    done = [i for i, x in enumerate(t) if x + self.SLOW_GRAN <= time.time()]  # finished candles only
                    k = (done[-1] + 1) if done else n
                    hist[c], ts[c] = d["c"][:k], t[:k]
                    hx[c] = {x: d[x][:k] for x in ("h", "l", "v")}
                L = min(len(v) for v in hist.values())
                hist = {c: v[-L:] for c, v in hist.items()}
                hx = {c: {x: y[-L:] for x, y in d.items()} for c, d in hx.items()}
                last_ts = min(ts[c][-1] for c in coins) if L else 0
                t = sl["t"]
                if t is None and saved.get("farm"):
                    t = make()
                    if not t.restore(saved["farm"]):
                        t = None
                    else:
                        t.xfull = None
                        self.q.put(("log", (f"◇ Slow farm: carrying on with its {len(t.vars)} bots.", "good")))
                if t is None:
                    self.q.put(("log", f"◇ Slow farm: hatching and warming up on {L} hours of history (this can take a minute)..."))
                    sl["status"] = f"Warming up on {L} hours of history..."
                    t = None
                    if self.settings.get("multicore", True) and opts.get("islands", 1) > 1:
                        t = E.warm_islands(coins, cash, fee, pct, 24, self.settings["params"], dict(opts), cap, 24 * 14, hist, hx)
                        if t is not None:
                            t.risk_caps = dict(E.SLOW_RISK)
                            t.W = 72
                    if t is None:
                        t = make()
                        t.warmup(hist, extra=hx)
                    last_ts = min(ts[c][-1] for c in coins)
                elif saved.get("last_ts") and saved["last_ts"] < last_ts:  # catch up on the hours it missed
                    miss = [i for i, x in enumerate(ts[coins[0]][-L:]) if x > saved["last_ts"]]
                    for i in miss:
                        t.step({c: E.View(hist[c], i + 1) for c in coins}, E.slice_extra(hx, i + 1))
                t.recording = False

                def done_():
                    sl.update(t=t, hist=hist, hx=hx, last_ts=last_ts, coins=coins, busy=False, status="")
                    if sl["wallet"] is None:
                        w = saved.get("wallet") or {}
                        sl["wallet"] = E.Sim(cash, fee, pct, slip=slip)
                        if w:
                            sl["wallet"].cash = float(w.get("cash", cash))
                            sl["wallet"].pos = {c: float(q) for c, q in (w.get("pos") or {}).items()}
                            sl["wallet"].cost = {c: float(q) for c, q in (w.get("cost") or {}).items()}
                        sl["curve"] = [tuple(x) for x in saved.get("curve") or []]
                        sl["hold0"] = saved.get("hold0") or {c: hist[c][-1] for c in coins}
                    self.slow_save()
                    self.write(f"◇ Slow farm ready: {len(t.vars)} bots on {', '.join(coins)}, deciding on hourly candles.", "good")
                    self.slow_refresh()
                self.q.put(("call", done_))
            except Exception as e:
                def fail(e=e):
                    sl.update(busy=False, status=f"Couldn't start: {e}")
                    self.slow_refresh()
                self.q.put(("call", fail))
        threading.Thread(target=work, daemon=True).start()
        if not getattr(self, "_slow_loop", None):
            self._slow_loop = threading.Thread(target=self._slow_feed, daemon=True)
            self._slow_loop.start()

    def _slow_feed(self):
        """Every few minutes: has a new hourly candle finished? Then the slow farm takes its next step."""
        while True:
            time.sleep(60 if self.slow.get("t") else 15)
            sl = self.slow
            if not self.cfg.get("slow_on") or sl["t"] is None or sl["busy"]:
                continue
            if time.time() < sl["last_ts"] + 2 * self.SLOW_GRAN + 20:
                continue  # the next candle hasn't finished yet
            try:
                new = {}
                for c in sl["coins"]:
                    d = E.fetch_ohlcv(c, self.SLOW_GRAN, 60, self.cur_quote())
                    t = list(d.get("t") or [])
                    if len(t) != len(d["c"]):
                        continue
                    new[c] = [(t[i], d["c"][i], d["h"][i], d["l"][i], d["v"][i]) for i in range(len(t))
                              if t[i] > sl["last_ts"] and t[i] + self.SLOW_GRAN <= time.time()]
                if new and all(new.get(c) for c in sl["coins"]):
                    k = min(len(new[c]) for c in sl["coins"])
                    self.q.put(("call", lambda new=new, k=k: self._slow_step(new, k)))
            except Exception:
                pass

    def _slow_step(self, new, k):
        sl, t = self.slow, self.slow["t"]
        if t is None:
            return
        for i in range(k):
            for c in sl["coins"]:
                ts, cl, hi, lo, vol = new[c][i]
                sl["hist"][c].append(cl)
                for x, val in (("h", hi), ("l", lo), ("v", vol)):
                    sl["hx"][c][x].append(val)
                del sl["hist"][c][:-6000]
                for x in ("h", "l", "v"):
                    del sl["hx"][c][x][:-6000]
            sl["last_ts"] = new[sl["coins"][0]][i][0]
            t.step({c: sl["hist"][c] for c in sl["coins"]}, {c: sl["hx"][c] for c in sl["coins"]})
            px = {c: sl["hist"][c][-1] for c in sl["coins"]}
            for c in sl["coins"]:
                sig, mult = t.vote(c)
                if sig:
                    sl["wallet"].buy(c, px[c], 0, mult) if sig == "buy" else sl["wallet"].sell(c, px[c])
                    if self.settings.get("slow_trades_account") and self.running and self.session and c in self.session_coins \
                            and c not in self.pending:
                        self.pending_detail[c] = "The slow farm's winners agreed on the new hourly candle."
                        (self.buy(c, self.last.get(c, px[c]), "slow farm", mult) if sig == "buy"
                         else self.sell(c, self.last.get(c, px[c]), "slow farm"))
            sl["curve"].append((time.time(), sl["wallet"].equity(px)))
        for e in t.pop_events()[-3:]:
            self.write("◇ Slow farm: " + friendly_text(e[0].lower() + e[1:]))
        self.slow_save()
        self.slow_refresh()

    def slow_save(self):
        sl = self.slow
        if sl.get("t") is None:
            return
        w = sl["wallet"]
        try:
            E.save_json(self.slow_path(), dict(coins=sl["coins"], last_ts=sl["last_ts"], farm=sl["t"].to_state(),
                                              wallet=None if w is None else dict(cash=w.cash, pos=w.pos, cost=w.cost),
                                              curve=sl["curve"][-3000:], hold0=sl.get("hold0")))
        except OSError:
            pass

    def slow_reset(self):
        if not messagebox.askyesno("Reset slow farm", "Clear the slow farm and its wallet? (Your Library and Hall of Fame are kept.)"):
            return
        self.slow.update(t=None, wallet=None, curve=[], hist={}, hx={}, last_ts=0, coins=[], status="")
        try:
            os.remove(self.slow_path())
        except OSError:
            pass
        self.cfg["slow_on"] = False
        E.save_json(CONFIG_PATH, self.cfg)
        self.slow_refresh()
        self.write("◇ Slow farm reset. Start it again to hatch a fresh one on your current coins.")

    def _slow_selected(self):
        t = self.slow.get("t")
        if not t:
            return []
        ids = set(self.slow_tree.selection())
        return [v for v in t.vars if str(id(v)) in ids]

    def _slow_entries(self, vs):
        t = self.slow["t"]
        ids = {id(v) for v in vs}
        rows = [dict(r, bar_minutes=60) for r in t.board() if id(r["v"]) in ids]
        out = E.export_bots(rows, self.version, note="slow farm bot (decides on hourly candles)")["bots"]
        return out

    def slow_catch(self):
        vs = self._slow_selected()
        if not vs:
            return messagebox.showinfo("Slow farm", "Select bots in the list first (Ctrl+click for several).")
        self.catch_rows(self._slow_entries(vs), "slow farm")

    def slow_hof(self):
        vs = self._slow_selected()
        if not vs:
            return messagebox.showinfo("Slow farm", "Select bots in the list first.")
        entries = self._slow_entries(vs)
        self.catch_rows(entries, "slow farm")
        E.induct(entries, self.version)
        self.after_hof_change()
        self.write(f"★ Added {len(entries)} slow-farm bot(s) to the Hall of Fame.", "good")

    def slow_train(self):
        sl = self.slow
        if sl.get("t") is None or sl["busy"]:
            return messagebox.showinfo("Slow farm", "Start the slow farm first (it needs its history loaded).")
        sl["busy"] = True
        sl["status"] = "Training on the past..."
        self.slow_refresh()
        hist = {c: list(v) for c, v in sl["hist"].items()}
        hx = {c: {x: list(y) for x, y in d.items()} for c, d in sl["hx"].items()}
        t = sl["t"]
        seeds = [E._genes(v) for v in t.vars]
        fee, slip = E.cost_model(self.settings)

        def prog(done, total, text):
            sl["status"] = f"Training on the past: {done * 100 // max(1, total)}% ({text})"

        def work():
            try:
                res = E.train_farm(hist, hx, dict(E.tournament_opts(self.settings), risk_caps=dict(E.SLOW_RISK), window=72),
                                   cap=len(t.vars), passes=2, exam_frac=0.2,
                                   seed_genes=seeds, start_cash=t.start_cash, fee=fee, pct=t.pct, evolve=24,
                                   params=self.settings["params"], retire_steps=24 * 14, progress=prog)

                def done_():
                    E.teach_from(t, res["tourn"])
                    n = 0
                    for r in res["results"]:
                        if r["passed"] and len(t.vars) < t.cap + 6:
                            g = r["genes"]
                            v = t.add_variant(g["name"], g["params"], hist, 1.0, note="Slow farm graduate:", tf=g["tf"],
                                              origin="training camp", risk=g["risk"], traits=g["traits"], vetoes=g["vetoes"],
                                              layers=g["layers"])
                            if v:
                                v.layer_stats = {k: list(x) for k, x in r["layer_stats"].items()}
                                n += 1
                    t.cap = max(t.cap, len(t.vars))
                    sl.update(busy=False, status="")
                    self.slow_save()
                    self.write(f"◇ Slow farm trained on {res['train_bars']} hours of history; "
                               f"{sum(1 for r in res['results'] if r['passed'])} passed the exam, {n} joined it.", "good")
                    self.slow_refresh()
                self.q.put(("call", done_))
            except Exception as e:
                self.q.put(("call", lambda e=e: (sl.update(busy=False, status=f"Training stopped: {e}"), self.slow_refresh())))
        threading.Thread(target=work, daemon=True).start()

    def slow_refresh(self):
        if not hasattr(self, "slow_tree"):
            return
        sl, t = self.slow, self.slow.get("t")
        on = bool(self.cfg.get("slow_on"))
        self.slow_btn.config(text="⏸ Pause the slow farm" if on and t else "▶ Start the slow farm")
        try:
            if "Slow farm" not in self.nb.tab(self.nb.select(), "text"):
                return  # redrawn when you open the tab
        except Exception:
            pass
        if sl.get("status"):
            txt = sl["status"]
        elif t is None:
            txt = "Not started. Press Start: it hatches on your ticked coins and learns from about four months of hourly history."
        else:
            px = {c: sl["hist"][c][-1] for c in sl["coins"]}
            w = sl["wallet"].equity(px) if sl["wallet"] else t.start_cash
            h0 = sl.get("hold0") or px
            hold = sum(px[c] / h0[c] for c in sl["coins"] if h0.get(c)) / len(sl["coins"]) - 1
            nxt = max(0, int(sl["last_ts"] + 2 * self.SLOW_GRAN - time.time()))
            txt = (f"{'Running' if on else 'Paused'}  |  {len(t.vars)} bots on {', '.join(sl['coins'])}  |  generation "
                   f"{max((v.gen for v in t.vars), default=0)}  |  {len(t.active)} winning  |  last candle "
                   f"{datetime.fromtimestamp(sl['last_ts']).strftime('%b %d %H:%M') if sl['last_ts'] else '-'}"
                   + (f", next in about {nxt // 60} min" if on else "")
                   + f"  |  its wallet ${w:,.2f} ({(w / t.start_cash - 1) * 100:+.1f}%, just holding {hold * 100:+.1f}%)")
        self.slow_lbl.config(text=txt)
        self.slow_draw()
        tr = self.slow_tree
        sel = tr.selection()
        tr.delete(*tr.get_children())
        if t is None:
            return
        for r in t.board():
            v, lf = r["v"], r["life"]
            holds = [b - a for a, b in self._hold_spans(v)]  # in hourly candles
            typ = sum(holds) / len(holds) if holds else None
            tr.insert("", "end", iid=str(id(v)), tags=(STATUS_TAG.get(r["status"], "learning"),), values=(
                E.bot_nickname(v.key()), STRAT_LABELS.get(v.name, v.name), E.fmt_bar(v.tf * 60),
                STATUS_TEXT.get(r["status"], r["status"]), pct_txt(r["score"]), pct_txt(r.get("edge")), pct_txt(lf["ret"]),
                lf["trades"], "-" if typ is None else (f"{typ / 24:.1f} days" if typ >= 24 else f"{typ:.0f} h"),
                E.risk_text(v.risk) or "none"))
        keep = [i for i in sel if tr.exists(i)]
        if keep:
            tr.selection_set(keep)

    @staticmethod
    def _hold_spans(v):
        """(bought, sold) decision numbers of the bot's recent round trips."""
        out, open_ = [], {}
        for n, c, side, _ in v.log:
            if side == "buy":
                open_.setdefault(c, n)
            elif c in open_:
                out.append((open_.pop(c), n))
        return out[-20:]

    def slow_draw(self):
        cv, sl = self.slow_cv, self.slow
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        pts = sl.get("curve") or []
        t = sl.get("t")
        if t is not None and len(pts) < 2:  # nothing live yet: show what its leader did over the history
            lead = (t.active or [v for v, _ in t.ranking()] or [None])[0]
            if lead is not None and len(lead.eq) > 2:
                pts = [(i, x) for i, x in enumerate(lead.eq)]
                cv.create_text(W - 10, 8, anchor="ne", fill=MUTED, font=("", 8),
                               text=f"its leader {E.bot_nickname(lead.key())} over its scoring window (hours)")
        if len(pts) < 2:
            cv.create_text(W // 2, H // 2, fill=MUTED, text="Its wallet appears here as the slow farm trades.")
            return
        ys = [p[1] for p in pts]
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.1 or 1
        lo, hi = lo - pad, hi + pad
        X = lambda i: 60 + i * (W - 70) / (len(pts) - 1)
        Y = lambda y: H - 12 - (y - lo) / (hi - lo) * (H - 24)
        for k in range(3):
            yv = lo + (hi - lo) * k / 2
            cv.create_line(60, Y(yv), W - 10, Y(yv), fill=LINE)
            cv.create_text(54, Y(yv), text=f"{yv:,.0f}", anchor="e", fill=MUTED, font=("", 8))
        cv.create_line(*[z for i, (_, y) in enumerate(pts) for z in (X(i), Y(y))], fill="#a78bfa", width=2)

    # ---------------- compare ----------------
    def compare_bots(self, a=None, b=None):
        if a is None or b is None:
            sel = self.selected_variants()
            if len(sel) != 2:
                return messagebox.showinfo("Compare", "Select exactly two bots (Ctrl+click on the farm), then compare.")
            a, b = sel
        w = tk.Toplevel(self.root)
        w.title("Compare bots")
        w.configure(bg=BG)
        f = tk.Frame(w, bg=BG, padx=14, pady=10)
        f.pack(fill="both", expand=True)

        def facts(v):
            lf = self.tourn.life(v)
            d = self.tourn.info.get(id(v), {})
            wr = "-" if lf["winrate"] is None else f"{lf['winrate'] * 100:.0f}%"
            return [E.bot_nickname(v.key()), STRAT_LABELS.get(v.name, v.name),
                    ", ".join(f"{k}={x}" for k, x in v.params.items()), f"every {E.fmt_bar(v.tf * self.session_bar)}",
                    STATUS_TEXT.get(d.get("status"), "-"), pct_txt(d.get("score")), pct_txt(lf["ret"]), pct_txt(lf["edge"]),
                    wr, str(lf["trades"]), f"{lf['maxdd'] * 100:.1f}%", f"{self.tourn.health(v) * 100:.0f}% / {v.power:.1f}",
                    E.risk_text(v.risk) or "none", E.traits_text(v.traits) or "none", E.vetoes_text(v.vetoes, v.veto_stats) or "none",
                    E.layers_text(v.layers, v.layer_stats) or "none",
                    "-" if v.twin is None else f"{v.twin * 100:+.1f}%", f"gen {v.gen}, island {v.island + 1}",
                    ", ".join(n for n, _ in self.achievements(v, lf)) or "-"]
        rows = ["Name", "Strategy", "Settings", "Decides", "Status", "Recent", "Lifetime", "vs holding", "Win rate", "Trades",
                "Worst drop", "Health / power", "Own stops", "Traits", "Never-rules", "Studies", "Twin test", "Family", "Badges"]
        fa, fb = facts(a), facts(b)
        for i, name in enumerate(rows):
            tk.Label(f, text=name, bg=BG, fg=MUTED, anchor="nw").grid(row=i, column=0, sticky="nw", pady=2, padx=(0, 12))
            for j, val in enumerate((fa[i], fb[i])):
                diff = fa[i] != fb[i]
                tk.Label(f, text=val, bg=CARD if diff else BG, fg=TEXT, anchor="nw", justify="left", wraplength=330,
                         font=("", 9, "bold" if i == 0 else "normal"), padx=6).grid(row=i, column=1 + j, sticky="nwe", pady=2, padx=2)
        tk.Label(f, text="Highlighted rows are where they differ.", bg=BG, fg=MUTED).grid(row=len(rows), column=0, columnspan=3, sticky="w", pady=(8, 0))

    # ---------------- bloodlines (drawn tree) ----------------
    def open_bloodlines(self):
        if not self.tourn:
            return messagebox.showinfo("Bloodlines", "Start the farm first.")
        w = tk.Toplevel(self.root)
        w.title("Bloodlines")
        w.geometry("1100x640")
        w.configure(bg=BG)
        tk.Label(w, text="Each row is a generation. Lines run from parent to child; bright = alive, gold = strong line "
                         "(a living descendant beating holding). Click a living dragon to open it.", bg=BG, fg=MUTED,
                 padx=10, pady=6, anchor="w").pack(fill="x")
        area = tk.Frame(w, bg=BG)
        area.pack(fill="both", expand=True)
        cv = tk.Canvas(area, bg=CARD, highlightthickness=0)
        xs = ttk.Scrollbar(area, orient="horizontal", command=cv.xview)
        ys = ttk.Scrollbar(area, orient="vertical", command=cv.yview)
        cv.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        xs.pack(side="bottom", fill="x")
        ys.pack(side="right", fill="y")
        cv.pack(fill="both", expand=True)
        recs = {r["uid"]: r for r in self.tourn.family()}
        alive = {v.uid: v for v in self.tourn.vars}
        keep = set()
        for u in alive:  # each living bot and its ancestors (keeps the picture readable)
            stack = [u]
            while stack:
                x = stack.pop()
                if x in recs and x not in keep:
                    keep.add(x)
                    stack.extend(recs[x]["parents"])
        rows = {}
        for u in sorted(keep):
            rows.setdefault(recs[u]["gen"], []).append(u)
        strong = set()
        for u, v in alive.items():
            lf = self.tourn.life(v)
            if (lf.get("edge") or 0) > 0:
                stack = [u]
                while stack:
                    x = stack.pop()
                    if x in recs and x not in strong:
                        strong.add(x)
                        stack.extend(recs[x]["parents"])
        pos, gx, gy = {}, 96, 92
        for g, us in sorted(rows.items()):
            for i, u in enumerate(us):
                pos[u] = (60 + i * gx, 50 + g * gy)
        for u in keep:
            for pnt in recs[u]["parents"]:
                if pnt in pos:
                    (x0, y0), (x1, y1) = pos[pnt], pos[u]
                    cv.create_line(x0, y0 + 20, x0, (y0 + y1) / 2, x1, (y0 + y1) / 2, x1, y1 - 22, smooth=True,
                                   fill="#f59e0b" if (u in strong and pnt in strong) else LINE, width=2 if u in strong else 1)
        for u, (x, y) in pos.items():
            r = recs[u]
            v = alive.get(u)
            if v is not None:
                st = self.tourn.info.get(id(v), {}).get("status", "warming up")
                draw_creature(cv, v.name, x - 18, y - 22, 36, self.GRID_STATUS.get(st, ("", "#8b5cf6"))[1], status=st,
                              awake=True, phase=0, tags=(f"u{u}",))
                cv.tag_bind(f"u{u}", "<Button-1>", lambda e, v=v: self.bot_detail(v, quiet=True))
                name = E.bot_nickname(v.key())
            else:
                cv.create_oval(x - 7, y - 7, x + 7, y + 7, fill=LINE, outline="")
                name = f"#{u}"
            cv.create_text(x, y + 26, text=name, fill=TEXT if v is not None else MUTED, font=("", 7, "bold" if v else "normal"))
        for g in rows:
            cv.create_text(8, 50 + g * gy, text=f"G{g}", anchor="w", fill=MUTED, font=("", 8, "bold"))
        bb = cv.bbox("all") or (0, 0, 100, 100)
        cv.configure(scrollregion=(0, 0, bb[2] + 40, bb[3] + 40))

    # ---------------- farm replay ----------------
    def record_farm(self):
        if not self.tourn:
            return
        rows = self.tourn.board()
        tiers = [self.tier(r) for r in rows]
        ev = [e for e in self.tourn.events if any(w in e for w in ("Bred", "Retired", "Season", "Migration", "Classic", "Twin"))][-3:]
        self.farm_hist.append((time.time(), tiers.count("egg"), tiers.count("rising"), tiers.count("beast"), ev))

    def open_replay(self):
        data = list(self.farm_hist)
        if len(data) < 5:
            return messagebox.showinfo("Farm replay", "Not enough farm history yet. Let the farm run for a while.")
        w = tk.Toplevel(self.root)
        w.title("Farm replay")
        w.geometry("900x520")
        w.configure(bg=BG)
        cv = tk.Canvas(w, bg=CARD, highlightthickness=0, height=300)
        cv.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        log = tk.Label(w, text="", bg=BG, fg=TEXT, justify="left", anchor="nw", height=5, font=("", 9))
        log.pack(fill="x", padx=12)
        ctl = ttk.Frame(w, padding=8)
        ctl.pack(fill="x")
        st = dict(i=0, playing=True, speed=8)
        pb = ttk.Button(ctl, text="Pause")
        pb.pack(side="left")
        sp = tk.StringVar(value="8x")
        ttk.Combobox(ctl, textvariable=sp, values=["2x", "8x", "32x"], state="readonly", width=5).pack(side="left", padx=6)
        when = ttk.Label(ctl, text="")
        when.pack(side="left", padx=10)
        recent = deque(maxlen=5)

        def toggle():
            st["playing"] = not st["playing"]
            pb.config(text="Pause" if st["playing"] else "Play")
            if st["playing"]:
                tick()
        pb.config(command=toggle)

        def draw(i):
            cv.delete("all")
            W, H = cv.winfo_width(), cv.winfo_height()
            mx = max(max(d[1] + d[2] + d[3] for d in data), 1)
            n = len(data)
            X = lambda k: 40 + k * (W - 60) / max(1, n - 1)
            Y = lambda y: H - 20 - y / mx * (H - 40)
            for idx, col, name in ((1, "#22c55e", "eggs"), (2, "#3b82f6", "rising"), (3, "#f97316", "BEASTS")):
                cv.create_line(*[z for k in range(0, i + 1, max(1, n // 800)) for z in (X(k), Y(data[k][idx]))] + [X(i), Y(data[i][idx])],
                               fill=col, width=2)
                cv.create_text(W - 12, 14 + idx * 14, anchor="ne", text=f"{name}: {data[i][idx]}", fill=col, font=("", 9, "bold"))
            cv.create_line(X(i), 10, X(i), H - 14, fill=MUTED, dash=(2, 3))
            when.config(text=datetime.fromtimestamp(data[i][0]).strftime("%a %H:%M:%S"))
            for e in data[i][4]:
                recent.append(e)
            log.config(text="\n".join(friendly_text(e)[:140] for e in recent))

        def tick():
            if not w.winfo_exists() or not st["playing"]:
                return
            step = int(sp.get().rstrip("x")) // 2 or 1
            st["i"] = min(len(data) - 1, st["i"] + step)
            draw(st["i"])
            if st["i"] >= len(data) - 1:
                st["playing"] = False
                pb.config(text="Play")
                st["i"] = 0
                return
            w.after(60, tick)
        w.after(200, tick)

    # ---------------- settings profiles ----------------
    def fill_profiles(self, menu):
        menu.delete(0, "end")
        menu.add_command(label="Save current settings as...", command=self.save_profile)
        profs = self.cfg.get("profiles") or {}
        if profs:
            menu.add_separator()
            for name in sorted(profs):
                menu.add_command(label=f"Load: {name}", command=lambda n=name: self.load_profile(n))
            menu.add_separator()
            for name in sorted(profs):
                menu.add_command(label=f"Delete: {name}", command=lambda n=name: self.delete_profile(n))

    PRIVATE = ("alert_discord", "alert_tg_token", "alert_tg_chat")

    def save_profile(self):
        name = simpledialog.askstring("Settings profile", "Name this setup (for example 'Weekday calm live'):")
        if not name or not name.strip():
            return
        snap = {k: v for k, v in json.loads(json.dumps(self.settings)).items() if k not in self.PRIVATE}
        self.cfg.setdefault("profiles", {})[name.strip()] = snap
        E.save_json(CONFIG_PATH, self.cfg)
        self.write(f"Saved settings profile '{name.strip()}'.", "good")

    def load_profile(self, name):
        snap = (self.cfg.get("profiles") or {}).get(name)
        if not snap:
            return
        keep = {k: self.settings.get(k) for k in self.PRIVATE}
        self.settings = E.merge_settings(dict(snap, **keep))
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)
        if self.tourn:
            self.tourn.o.update(E.tournament_opts(self.settings))
            self.tourn.cap = int(self.settings["max_bots"])
        if hasattr(self, "farm_size"):
            self.farm_size.set(str(int(self.settings["max_bots"])))
        self.write(f"Loaded settings profile '{name}'." + (" A new decision speed starts with a new session." if self.session else ""), "good")

    def delete_profile(self, name):
        if messagebox.askyesno("Settings profiles", f"Delete the profile '{name}'?"):
            (self.cfg.get("profiles") or {}).pop(name, None)
            E.save_json(CONFIG_PATH, self.cfg)

    # ---------------- backup / restore ----------------
    def backup_all(self):
        p = filedialog.asksaveasfilename(title="Back up CBBF", defaultextension=".zip",
                                         initialfile=f"cbbf-backup-{datetime.now():%Y%m%d-%H%M}.zip", filetypes=[("Zip", "*.zip")])
        if not p:
            return
        self.save_state()
        self.save_farm(force=True)
        n = 0
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for root_, dirs, files in os.walk(E.HOME_DIR):
                dirs[:] = [d for d in dirs if d not in ("cache", "backups")]
                for fn in files:
                    full = os.path.join(root_, fn)
                    z.write(full, os.path.relpath(full, E.HOME_DIR))
                    n += 1
        self.write(f"Backed up {n} files (Library, Hall of Fame, farms, sessions, settings, trades) to {p}. "
                   f"Your Coinbase login is not in it.", "good")
        return p

    def restore_all(self):
        p = filedialog.askopenfilename(title="Restore a CBBF backup", filetypes=[("Zip", "*.zip")])
        if not p:
            return
        if self.running:
            return messagebox.showinfo("Restore", "Stop the bot first.")
        if not messagebox.askyesno("Restore", "Replace your Library, Hall of Fame, farms, sessions and settings with the "
                                              "backup? Your current ones are kept in a safety copy first."):
            return
        safety = os.path.join(E.HOME_DIR, "backups", f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.zip")
        os.makedirs(os.path.dirname(safety), exist_ok=True)
        with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as z:
            for root_, dirs, files in os.walk(E.HOME_DIR):
                dirs[:] = [d for d in dirs if d not in ("cache", "backups")]
                for fn in files:
                    full = os.path.join(root_, fn)
                    z.write(full, os.path.relpath(full, E.HOME_DIR))
        with zipfile.ZipFile(p) as z:
            for m in z.namelist():
                dest = os.path.normpath(os.path.join(E.HOME_DIR, m))
                if not dest.startswith(os.path.normpath(E.HOME_DIR) + os.sep):
                    continue  # never write outside the data folder
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(m) as src, open(dest, "wb") as out:
                    out.write(src.read())
        messagebox.showinfo("Restore", f"Restored. CBBF will now restart to load it.\n(Safety copy: {safety})")
        self.restart_app()

    def restart_app(self):
        try:
            self.on_close()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ---------------- phone remote (Telegram) ----------------
    def start_tg_remote(self):
        """Listens for commands from your Telegram chat: /status /pause /resume /report /kill."""
        if getattr(self, "_tg_thread", None) and self._tg_thread.is_alive():
            return

        def loop():
            offset = None
            while True:
                s_ = self.settings
                if not s_.get("tg_remote") or not s_.get("alert_tg_token") or not s_.get("alert_tg_chat"):
                    time.sleep(15)
                    continue
                try:
                    ups, offset = E.tg_updates(s_["alert_tg_token"], offset)
                    for chat, text in ups:
                        if str(chat) == str(s_["alert_tg_chat"]).strip():
                            self.q.put(("call", lambda t=text: self.tg_command(t)))
                except Exception:
                    time.sleep(20)
                time.sleep(3)
        self._tg_thread = threading.Thread(target=loop, daemon=True)
        self._tg_thread.start()

    def tg_command(self, text):
        cmd = text.strip().split()[0].lower().split("@")[0] if text.strip() else ""
        reply = None
        if cmd in ("/status", "/start"):
            eq = self.equity() if self.session else 0
            reply = (f"CBBF {'LIVE' if self.live else 'Practice'}: {'running' if self.running else 'stopped'}. "
                     + (f"Value ${eq:,.2f} ({(eq / self.start_cash - 1) * 100:+.1f}%). " if self.session else "")
                     + self.pulse_text())
        elif cmd == "/pause":
            if self.running:
                self.toggle()
            reply = "Paused."
        elif cmd == "/resume":
            if self.live:
                reply = "LIVE can't be resumed from the phone (it needs you to type LIVE at the computer)."
            elif not self.running and self.session:
                self.toggle()
                reply = "Resumed (Practice)."
            else:
                reply = "Already running." if self.running else "No session to resume."
        elif cmd == "/report":
            reply = self.weekly_report(force=True)
        elif cmd == "/kill":
            if text.strip().lower().endswith("confirm"):
                reply = self.kill_switch(ask=False)
            else:
                reply = "Send '/kill confirm' to stop, sell everything and lock LIVE."
        elif cmd:
            reply = "Commands: /status /pause /resume /report /kill confirm"
        if reply:
            s_ = dict(self.settings, alert_discord="")
            threading.Thread(target=lambda: E.send_alert(s_, reply), daemon=True).start()

    # ---------------- compact top bar ----------------
    def apply_compact(self):
        """Compact: the account tiles shrink to one slim strip (title and number side by side), giving the farm more room."""
        on = bool(self.cfg.get("compact", False))
        for key, lbl in getattr(self, "tiles", {}).items():
            t = self.tile_titles.get(key)
            if not t:
                continue
            if on:
                t.pack_configure(side="left", anchor="w", pady=0)
                lbl.pack_configure(side="left", anchor="w", padx=(8, 0))
                lbl.config(font=("", 11, "bold"))
                t.master.config(pady=3)
                if key == "day" and hasattr(self, "day_sub"):
                    self.day_sub.pack_configure(side="left", padx=(8, 0))
            else:
                t.pack_configure(side="top", anchor="w", pady=(0, 4))
                lbl.pack_configure(side="top", anchor="w", padx=0)
                lbl.config(font=("", 15, "bold"))
                t.master.config(pady=8)
                if key == "day" and hasattr(self, "day_sub"):
                    self.day_sub.pack_configure(side="top", padx=0)

    def toggle_compact(self):
        self.cfg["compact"] = bool(self.compact_var.get())
        E.save_json(CONFIG_PATH, self.cfg)
        self.apply_compact()

    # ---------------- background mode ----------------
    def _on_root_configure(self, e):
        """While the window is being dragged or resized, animations and chart redraws pause so it moves smoothly."""
        if e.widget is self.root:
            geo = (e.x, e.y, e.width, e.height)
            if geo != getattr(self, "_last_geo", None):
                self._last_geo = geo
                self._moving_until = time.time() + 0.25

    def moving(self):
        return time.time() < getattr(self, "_moving_until", 0)

    def in_bg(self):
        """True while minimized with Background mode on: screens skip their redraws until you come back."""
        try:
            return bool(self.settings.get("bg_mode", True)) and self.root.state() == "iconic"
        except Exception:
            return False

    def on_restore(self, e=None):
        if e is not None and e.widget is not self.root:
            return
        if getattr(self, "_bg_dirty", False):
            self._bg_dirty = False
            for fn in (self.refresh_board, self.draw_chart, self.refresh_view):
                try:
                    fn()
                except Exception:
                    pass

    # ---------------- undo (Library / Hall of Fame) ----------------
    def lib_snapshot(self):
        return (json.loads(json.dumps(E.load_collection())), json.loads(json.dumps(E.load_hof())))

    def offer_undo(self, msg, snap):
        """A small bar with an Undo button for 15 seconds after releasing bots or removing them from the Hall of Fame."""
        old = getattr(self, "_undo_win", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        try:
            w = tk.Toplevel(self.root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            f = tk.Frame(w, bg=CARD, highlightthickness=1, highlightbackground=ACCENT, padx=12, pady=6)
            f.pack()
            tk.Label(f, text=msg, bg=CARD, fg=TEXT).pack(side="left")

            def undo():
                E.save_collection(snap[0])
                E.save_hof(snap[1], self.version)
                self.__dict__.pop("_file_cache", None)
                w.destroy()
                self.write("Undone: your Library and Hall of Fame are back as they were.", "good")
                self.after_hof_change()
                if getattr(self, "col_win", None) is not None and self.col_win.winfo_exists():
                    self.col_refresh()
            tk.Button(f, text="Undo", command=undo, bg=ACCENT, fg="white", relief="flat", padx=10,
                      activebackground=ACCENT, cursor="hand2").pack(side="left", padx=(12, 0))
            tk.Label(f, text="\u2715", bg=CARD, fg=MUTED, cursor="hand2", padx=6).pack(side="left")
            f.winfo_children()[-1].bind("<Button-1>", lambda e: w.destroy())
            w.update_idletasks()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            w.geometry(f"+{rx + (rw - w.winfo_reqwidth()) // 2}+{ry + rh - w.winfo_reqheight() - 30}")
            w.after(15000, lambda: w.winfo_exists() and w.destroy())
            self._undo_win = w
        except Exception:
            pass

    # ---------------- farm presets ----------------
    PRESETS = {
        "Calm": dict(max_bots=24, evolve_every=36, winner_boost=1.5, fail_drop=2.0, retire_hours=24.0, classic_rate=15,
                     long_holds=True),
        "Balanced": dict(max_bots=30, evolve_every=24, winner_boost=2.5, fail_drop=3.0, retire_hours=12.0, classic_rate=25,
                         long_holds=True),
        "Wild": dict(max_bots=80, evolve_every=12, winner_boost=3.0, fail_drop=4.0, retire_hours=6.0, classic_rate=35,
                     long_holds=True),
    }
    PRESET_TIPS = {"Calm": "fewer bots, slower evolution, small trade sizes for winners, patient",
                   "Balanced": "the default farm",
                   "Wild": "a big farm that evolves fast, bolder winners, quick turnover"}

    def preset_menu(self, widget):
        m = self._menu()
        m.add_command(label="Farm preset", state="disabled")
        m.add_separator()
        for name in self.PRESETS:
            m.add_command(label=f"{name}: {self.PRESET_TIPS[name]}", command=lambda n=name: self.apply_preset(n))
        try:
            m.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            m.grab_release()

    def apply_preset(self, name):
        self.settings.update(self.PRESETS[name])
        self.cfg["settings"] = self.settings
        self.cfg["preset"] = name
        E.save_json(CONFIG_PATH, self.cfg)
        if hasattr(self, "farm_size"):
            self.farm_size.set(str(self.settings["max_bots"]))
        t = self.tourn
        if t:
            t.o.update(E.tournament_opts(self.settings))
            t.cap = int(self.settings["max_bots"])
            t.family_cap = max(5, -(-t.cap // 4))
            t.evolve_every = int(self.settings["evolve_every"])
            t.retire_steps = self.retire_steps(self.session_bar)
            while len(t.vars) > t.cap:
                worst = [v for v, _ in reversed(t.ranking()) if v not in t.active]
                if not worst:
                    break
                t._remove(worst[0], "farm made smaller")
            t._rate()
            self.refresh_board()
        self.write(f"Farm preset: {name} ({self.PRESET_TIPS[name]}).", "good")

    # ---------------- first-run tour ----------------
    def maybe_tour(self):
        if not self.cfg.get("toured"):
            self.start_tour()

    def start_tour(self):
        steps = [
            (lambda: self.grid_chips["Eggs"], "The dens",
             "Every dragon is a bot. New ones start as Eggs, grow into Rising, and the dominant ones become BEASTS. "
             "Click a den to see who lives there."),
            (lambda: self.catch_btn, "Catch!",
             "Select a dragon you like and press Catch! to keep it forever in your Library (it gets a name and a rarity)."),
            (lambda: self.grid_chips["Hall of Fame"], "Hall of Fame",
             "Your hand-picked champions. Only you decide who gets in: right-click any dragon, or drag its card onto this tab."),
            (lambda: self.seg_paper, "Practice and Live",
             "Green PRACTICE uses pretend money. Blue LIVE is real money and always asks you to type LIVE first."),
            (lambda: self.btn, "Start",
             "Press Start and the farm wakes up: dragons hatch, compete, breed and evolve while you watch. "
             "Help > Take the tour shows this again."),
        ]
        self.cfg["toured"] = True
        E.save_json(CONFIG_PATH, self.cfg)
        self._tour_step(steps, 0)

    def _tour_step(self, steps, i):
        old = getattr(self, "_tour_win", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        if i >= len(steps):
            return
        try:
            widget = steps[i][0]()
            if not widget.winfo_ismapped():
                return self._tour_step(steps, i + 1)
        except Exception:
            return self._tour_step(steps, i + 1)
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        f = tk.Frame(w, bg=CARD, highlightthickness=2, highlightbackground=ACCENT, padx=14, pady=10)
        f.pack()
        tk.Label(f, text=f"\u25b2  {steps[i][1]}   ({i + 1}/{len(steps)})", bg=CARD, fg=ACCENT, font=("", 10, "bold")).pack(anchor="w")
        tk.Label(f, text=steps[i][2], bg=CARD, fg=TEXT, wraplength=320, justify="left").pack(anchor="w", pady=(4, 8))
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x")
        tk.Button(row, text="Next" if i < len(steps) - 1 else "Done", bg=ACCENT, fg="white", relief="flat", padx=12,
                  cursor="hand2", command=lambda: self._tour_step(steps, i + 1)).pack(side="right")
        if i < len(steps) - 1:
            tk.Button(row, text="Skip tour", bg=CARD, fg=MUTED, relief="flat", cursor="hand2",
                      command=w.destroy).pack(side="right", padx=(0, 6))
        w.update_idletasks()
        x = min(widget.winfo_rootx(), self.root.winfo_rootx() + self.root.winfo_width() - w.winfo_reqwidth() - 10)
        y = widget.winfo_rooty() + widget.winfo_height() + 6
        if y + w.winfo_reqheight() > self.root.winfo_rooty() + self.root.winfo_height():
            y = widget.winfo_rooty() - w.winfo_reqheight() - 6
        w.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._tour_win = w

    def allowance_mult(self, c, voters, mult):
        """Allowance per bot: shrinks (or skips) a buy when the bots voting for it already have their share of the
        account tied up. Returns the new size multiplier (0 = skip)."""
        pct = float(self.settings.get("bot_allowance", 25) or 0)
        if pct <= 0 or not voters:
            return mult
        cap = self.start_cash * pct / 100
        used = {}
        for alloc in self.bot_alloc.values():
            for uid, usd in alloc.items():
                used[uid] = used.get(uid, 0.0) + usd
        room = sum(max(0.0, cap - used.get(v.uid, 0.0)) for v in voters)
        want = self.trade_size(c, mult)
        if want <= 0 or room >= want:
            return mult
        if room < 1:
            if time.time() - getattr(self, "_allow_note", 0) > 900:
                self._allow_note = time.time()
                self.write(f"Allowance: the bots voting to buy {c} already have their share of the account tied up, "
                           f"so the buy was skipped. (Settings > Basics > Allowance per bot)", "warn")
            return 0.0
        return mult * room / want

    def why_text(self, c, sig, voters, who):
        """Plain-English 'why did it trade?' for the account's trade on coin c."""
        lines = []
        kind = self.tourn.kind.get(c) if (self.wild and self.tourn) else None
        if kind == "add":
            lines.append(f"Adding to {c}: the winning bots holding most of the weight are still in it.")
        elif kind == "slow":
            lines.append(f"Only shaky bots wanted this, so it's a small, slowed-down trade.")
        for v in sorted(voters, key=lambda x: -self.tourn.info.get(id(x), {}).get("weight", 0))[:5]:
            st = STATUS_TEXT.get(self.tourn.info.get(id(v), {}).get("status"), "")
            lines.append(f"- {E.bot_nickname(v.key())} ({STRAT_LABELS.get(v.name, v.name)}, {st}): "
                         f"{v.why.get(c) or v.name + ' said ' + sig}")
        if len(voters) > 5:
            lines.append(f"...and {len(voters) - 5} more bot{'s' if len(voters) > 6 else ''} agreed.")
        if not voters and who:
            lines.append("Your picked strategies voted " + sig + ": " + ", ".join(STRAT_LABELS.get(n, n) for n in dict.fromkeys(who)))
        return "\n".join(lines)

    def show_trade_why(self, row):
        """The 'why did it trade?' window for one trade."""
        w = tk.Toplevel(self.root)
        w.title("Why did it trade?")
        w.configure(bg=BG)
        f = tk.Frame(w, bg=BG, padx=16, pady=12)
        f.pack(fill="both", expand=True)
        side = str(row.get("side", "")).upper()
        tk.Label(f, text=f"{side} {row.get('coin', '')}  at {row.get('price', '')}  ({str(row.get('time', '')).replace('T', ' ')})",
                 bg=BG, fg=GREEN if side == "BUY" else RED, font=("", 12, "bold")).pack(anchor="w")
        reason = REASON_TEXT.get(row.get("reason"), row.get("reason", ""))
        tk.Label(f, text=f"Reason: {reason}", bg=BG, fg=TEXT).pack(anchor="w", pady=(4, 0))
        if row.get("who"):
            tk.Label(f, text=f"Strategies behind it: {row['who']}", bg=BG, fg=MUTED, wraplength=560, justify="left").pack(anchor="w")
        detail = row.get("detail") or ("Only trades made from now on carry the full story of which bots voted and why."
                                       if row.get("mode") != "bot" else "This is one bot's own practice trade inside the farm.")
        tk.Label(f, text=detail, bg=BG, fg=TEXT, wraplength=560, justify="left", font=("", 10)).pack(anchor="w", pady=(8, 0))
        ttk.Button(f, text="Close", command=w.destroy).pack(anchor="e", pady=(12, 0))

    # ---------------- weekly report ----------------
    def weekly_report(self, force=False):
        """Once a week (or on demand): what the farm learned and how the account did. Saved to the data folder
        and sent to Discord / Telegram if set up. Returns the report text."""
        last = float(self.cfg.get("last_report", 0) or 0)
        if not force and (not self.settings.get("weekly_report", True) or time.time() - last < 7 * 86400):
            return None
        if not force and not last:  # first run: start the clock instead of reporting on nothing
            self.cfg["last_report"] = time.time()
            E.save_json(CONFIG_PATH, self.cfg)
            return None
        since = datetime.now().timestamp() - 7 * 86400
        lines = [f"CBBF weekly report, {datetime.now():%Y-%m-%d}", ""]
        for mode in ("live", "paper"):
            rows = [r for r in self.trades if r.get("mode") == mode and (self._ts(r.get("time")) or 0) >= since]
            if rows:
                pl = sum(float(r["pl"]) for r in rows if r.get("pl") not in ("", None))
                lines.append(f"{'LIVE' if mode == 'live' else 'Practice'} account: {len(rows)} trades this week, "
                             f"closed profit/loss ${pl:+,.2f}.")
        if self.session:
            eq = self.equity()
            lines.append(f"Current session ({'LIVE' if self.live else 'Practice'}): total value ${eq:,.2f} "
                         f"({(eq / self.start_cash - 1) * 100:+.1f}% since it began).")
        t = self.tourn
        if t:
            rows = t.board()
            lines += ["", f"Farm: {len(rows)} bots, generation {max((r['gen'] for r in rows), default=0)}, "
                          f"{sum(1 for r in rows if r['status'] == 'active')} winning right now."]
            if t.season:
                lines.append(f"Season: {t.season}. Champions known for: {', '.join(sorted(t.champions)) or 'none yet'}.")
            twins = [r for r in rows if r.get("twin") is not None]
            if twins:
                lines.append(f"Twin tests: {sum(1 for r in twins if r['twin'] > 0)} passed, {sum(1 for r in twins if r['twin'] <= 0)} failed.")
            lines.append(f"Elders remembered: {len(t.elders)}.")
            best = sorted((r for r in rows if r["life"]["edge"] is not None), key=lambda r: -r["life"]["edge"])[:5]
            if best:
                lines += ["", "Top dragons (lifetime, vs just holding):"]
                for r in best:
                    lines.append(f"  {E.bot_nickname(r['v'].key())} ({STRAT_LABELS.get(r['name'], r['name'])}): "
                                 f"{r['life']['ret'] * 100:+.1f}%, {r['life']['edge'] * 100:+.1f}% vs holding")
        lines += ["", f"Library: {len(E.load_collection())} bots saved, {len(E.load_hof())} in the Hall of Fame."]
        text = "\n".join(lines)
        try:
            d = os.path.join(E.HOME_DIR, "reports")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"report-{datetime.now():%Y-%m-%d}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.write(f"Weekly report saved: {path}", "good")
        except OSError:
            pass
        if E.alerts_configured(self.settings):
            s = dict(self.settings)
            threading.Thread(target=lambda: E.send_alert(s, text), daemon=True).start()
        self.cfg["last_report"] = time.time()
        E.save_json(CONFIG_PATH, self.cfg)
        return text

    def show_report(self):
        text = self.weekly_report(force=True)
        w = tk.Toplevel(self.root)
        w.title("Weekly report")
        w.configure(bg=BG)
        box = tk.Text(w, width=90, height=28, bg=CARD, fg=TEXT, relief="flat", padx=10, pady=8, font=("", 10))
        box.pack(fill="both", expand=True)
        box.insert("1.0", text or "")
        box.config(state="disabled")

    def check_hof(self):
        """Saves long-term winners to the Hall of Fame."""
        try:
            need = int(float(self.settings.get("hof_hours", 6)) * 60 / max(self.session_bar, 0.25))
            cands = self.tourn.hall_of_fame_candidates(max(1, need), int(self.settings.get("hof_min_trades", 4)))
            if not cands:
                return
            rows = [dict(r, bar_minutes=self.session_bar) for r in self.tourn.board() if r["v"] in cands]
            entries = E.export_bots(rows, self.version, note=f"long-term winner, {', '.join(self.session_coins)}")["bots"]
            to_hof = self.settings.get("auto_hof", False)
            if self.settings.get("auto_catch", True):
                self.catch_rows(entries, "long-term winner")
            new = E.induct(entries, self.version) if to_hof else 0
            for v in cands:
                v.inducted = True  # remembered so it is only saved once
                lf = self.tourn.life(v)
                where = "the Hall of Fame" if to_hof else "your Bot Library (promote it to the Hall of Fame if you trust it)"
                msg = (f"Long-term winner: {E.bot_nickname(v.key())} ({friendly_label(v.name, v.params, v.tf, self.session_bar)}) "
                       f"made {lf['ret'] * 100:+.1f}% ({pct_txt(lf['edge'])} vs holding). Saved to {where}.")
                self.write(msg, "good")
                self.alert("hof", msg)
            self.notify("CBBF", f"{len(cands)} long-term winner{'s' if len(cands) != 1 else ''} saved"
                        + (" to the Hall of Fame" if new else " to your Library"), sound="hof")
        except Exception:
            note = self._record_error("updating the Hall of Fame")
            if note:
                self.write(note, "bad")

    def collect_shadow(self):
        """Adds every bot's pretend trades from this bar to the Trades tab (they never touch the account)."""
        try:
            stamp = datetime.now().isoformat(timespec="seconds")
            new = self.tourn.pop_shadow()
            for t in new:
                self.shadow_trades.append(dict(
                    time=stamp, mode="bot", side=t["side"], coin=t["coin"], qty=round(t["qty"], 8),
                    price=round(t["price"], 6), usd=round(t["usd"], 2),
                    pl="" if t["pl"] is None else round(t["pl"], 2), reason="bot-test",
                    who=friendly_label(t["name"], t["params"], t.get("tf", 1), self.session_bar)))
            del self.shadow_trades[:-4000]
            if new:
                if self.nb.tab(self.nb.select(), "text").strip() == "Trades":
                    self.refresh_trades()
                else:
                    self._trades_dirty = True  # redrawn when you open the Trades tab
        except Exception:
            note = self._record_error("listing the bots' trades")
            if note:
                self.write(note, "bad")

    def update_wild_msg(self):
        n, top, scored = self.tourn.summary()
        if n:
            info = self.tourn.info[id(top)]
            slow = len(self.tourn.slowed)
            msg = (f"Wildcard is following {n} winning strateg{'y' if n == 1 else 'ies'}. "
                   f"Best right now: {friendly_label(top.name, top.params)} at {info['stake']:.1f}x trade size."
                   + (f" {slow} shaky one{'s are' if slow != 1 else ' is'} slowed down." if slow else ""))
        elif scored:
            slow = len(self.tourn.slowed)
            msg = ("No strategy is winning consistently right now, so the bot is " +
                   (f"only making small, slow trades with {slow} shaky one{'s' if slow != 1 else ''}." if slow
                    else "standing aside."))
        else:
            msg = "Wildcard is still learning from recent prices."
        self.wild_msg = msg
        if msg != self.last_wild_msg:
            self.write(msg)
            self.last_wild_msg = msg

    def account_stops(self):
        """(stop, take, who decided) as fractions. With 'bot_stops' on in Wildcard, the account uses the
        weighted average stop-loss / take-profit that the current winners evolved, as long as winners holding
        most of the weight actually use one. Otherwise your own Safety stop / Lock in profit settings."""
        stop, take = self.settings["stop_loss"] / 100, self.settings["take_profit"] / 100
        src = ["you", "you"]
        t = self.tourn
        if self.settings.get("bot_stops", True) and self.wild and t and t.active:
            tot = sum(t.info[id(v)]["weight"] for v in t.active) or 0
            for i, k in enumerate(("sl", "tp")):
                using = [(t.info[id(v)]["weight"], v.risk[k]) for v in t.active if v.risk.get(k)]
                w = sum(x for x, _ in using)
                if tot > 0 and w * 2 >= tot:
                    val = sum(x * r for x, r in using) / w / 100
                    if i == 0:
                        stop = val
                    else:
                        take = val
                    src[i] = "bots"
        return stop, take, src

    def risk_checks(self):
        stop, take, _ = self.account_stops()
        trail = float(self.settings.get("trail_stop", 0) or 0) / 100
        for c, q in list(self.pos.items()):
            if q <= 0 or c in self.pending or c not in self.last:
                continue
            px = self.last[c]
            self.high[c] = max(self.high.get(c, px), px)
            r = px / (self.cost[c] / q) - 1
            if stop > 0 and r <= -stop:
                self.sell(c, px, "stop-loss")
            elif take > 0 and r >= take:
                self.sell(c, px, "take-profit")
            elif trail > 0 and px <= self.high[c] * (1 - trail):
                self.sell(c, px, "trailing-stop")

    def check_daily(self):
        today, eq = date.today(), self.equity()
        if self.day != today:
            self.day, self.day_eq = today, eq
            return
        lim = self.settings["daily_loss"] / 100
        if lim > 0 and self.running and eq < self.day_eq * (1 - lim):
            self.running = False
            self.halted_day = today
            self.ui_running(False)
            self.write(f"HALTED: daily loss limit ({self.settings['daily_loss']}%) reached. Positions are still open.")
            self.alert("problems", f"HALTED: daily loss limit ({self.settings['daily_loss']}%) reached. Positions are still open.")
            self.notify("CBBF", "Daily loss limit reached. Trading halted.", sound="problem")
            self.save_state()

    # ---------------- trading ----------------
    def trade_size(self, c, mult=1.0):
        spend = min(self.start_cash * self.settings["trade_pct"] / 100 * mult, self.cash)
        room = self.start_cash * self.settings["max_coin_pct"] / 100 - self.cost.get(c, 0)
        return max(min(spend, room), 0)

    def buy(self, c, px, reason="signal", mult=1.0):
        spend = self.trade_size(c, mult)
        if self.live:
            if spend < MIN_LIVE_ORDER:
                return
            self.cash -= spend  # reserve until the fill comes back
            self.pending.add(c)
            self.executor.submit(self.live_buy, c, spend, reason)
            return
        if spend < 1:
            return
        fee, slip = E.cost_model(self.settings)
        px = px * (1 + slip)  # Practice: market buys fill a little above the quoted price
        qty = spend * (1 - fee) / px
        self.fees_paid += spend * fee
        self.cash -= spend
        self.pos[c] = self.pos.get(c, 0) + qty
        self.cost[c] = self.cost.get(c, 0) + spend
        self.log_trade("buy", c, qty, px, spend, None, reason)

    def sell(self, c, px, reason="signal"):
        qty = self.pos.get(c, 0)
        if qty <= 0:
            return
        if self.live:
            self.pending.add(c)
            self.executor.submit(self.live_sell, c, qty, reason)
            return
        fee, slip = E.cost_model(self.settings)
        px = px * (1 - slip)  # Practice: market sells fill a little below the quoted price
        proceeds = qty * px * (1 - fee)
        self.fees_paid += qty * px * fee
        pl = proceeds - self.cost.get(c, 0)
        self.cash += proceeds
        self.pos[c], self.cost[c] = 0, 0
        self.log_trade("sell", c, qty, px, proceeds, pl, reason)

    def log_trade(self, side, c, qty, px, usd, pl, reason):
        row = dict(time=datetime.now().isoformat(timespec="seconds"), mode="live" if self.live else "paper",
                   side=side, coin=c, qty=round(qty, 8), price=round(px, 6), usd=round(usd, 2),
                   pl="" if pl is None else round(pl, 2), reason=reason, who=self.pending_who.pop(c, ""))
        detail = self.pending_detail.pop(c, "")
        if not detail and reason not in ("signal", "add-on"):
            detail = {"stop-loss": "Your account's safety stop was hit.", "take-profit": "Your account locked in its profit target.",
                      "trailing-stop": "Your trailing stop was hit.", "manual": "You placed this trade yourself."}.get(reason, "")
        if detail:
            row["detail"] = detail
        uids = self.pending_voters.pop(c, [])
        if side == "buy" and uids:
            alloc = self.bot_alloc.setdefault(c, {})
            for u in uids:
                alloc[u] = alloc.get(u, 0.0) + usd / len(uids)
        elif side == "sell" and self.pos.get(c, 0) <= 0:
            self.bot_alloc.pop(c, None)
        self.trades.append(row)
        if side == "buy":
            self.high[c] = max(self.high.get(c, px), px)
            self.bought_at.setdefault(c, time.time())
        elif self.pos.get(c, 0) <= 0:
            self.high.pop(c, None)
            self.bought_at.pop(c, None)
        try:
            E.append_trade_csv(row)
        except OSError:
            pass
        self.save_state()  # cash and positions have already changed, so persist them before any screen code runs
        # Each screen update is isolated: one failing widget must not hide the trade or freeze the balances.
        def trades_tab():
            try:
                visible = self.nb.tab(self.nb.select(), "text").strip() == "Trades"
            except Exception:
                visible = True
            if visible:
                self.refresh_trades()
            else:
                self._trades_dirty = True  # rebuilt when you open the Trades tab
        for step, name in ((trades_tab, "updating the Trades tab"),
                           (self.refresh_view, "updating the balances")):
            try:
                step()
            except Exception:
                note = self._record_error(name)
                if note:
                    self.write(note, "bad")
        why = REASON_TEXT.get(reason, reason)
        if side == "buy":
            msg = f"Bought ${usd:,.2f} of {c} at ${px:,.2f} ({why})"
        else:
            msg = f"Sold {c} at ${px:,.2f} for ${usd:,.2f}, {'made' if (pl or 0) >= 0 else 'lost'} ${abs(pl or 0):,.2f} ({why})"
        self.write(("LIVE: " if self.live else "Practice: ") + msg)
        self.notify("CBBF", f"{side.upper()} {c} @ {px:,.2f} ({reason})", important=False, sound="trade")
        if self.live and self.settings.get("why_popup", True):
            self.toast(f"LIVE {side.upper()} {c}: why?", (detail or why)[:400], ms=20000)
        self.alert("trades", ("LIVE " if self.live else "Practice ") + msg)

    def sell_all(self):
        if not any(q > 0 for q in self.pos.values()):
            return messagebox.showinfo("Sell all", "No positions held.")
        if self.live and not messagebox.askyesno("Sell all", "Place REAL sell orders for all positions?"):
            return
        for c, q in list(self.pos.items()):
            if q > 0 and c in self.last and c not in self.pending:
                self.sell(c, self.last[c], "manual")
        self.refresh_view()

    # ---------------- live trading (Coinbase Advanced Trade) ----------------
    def account_action(self):
        if self.client:
            self.logout()
        else:
            self.open_login()

    def auto_login(self):
        creds = E.load_login()
        if creds and not self.client:
            self.write("Saved Coinbase login found, connecting...")
            self.start_login(*creds, remember=False)

    def open_login(self):
        if self.client:
            return messagebox.showinfo("Login", "Already logged in. Use Account > Log out to switch keys.")
        if any(d["module"] == "coinbase" for d in E.missing_deps()):
            messagebox.showinfo("One quick setup step", "Logging in to Coinbase needs an add-on that isn't installed yet. "
                                                        "The next screen installs it with one click.")
            return self.open_setup()
        w = tk.Toplevel(self.root)
        w.title("Log in to Coinbase")
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack()
        ttk.Label(f, text="Log in with a Coinbase API key", font=("", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, wraplength=480, foreground=MUTED, text=(
            "Coinbase does not offer username/password login to third-party apps. Create an API key in the "
            "Coinbase Developer Platform with View and Trade permissions only, never Transfer. In 'Advanced "
            "settings' choose ECDSA as the signature algorithm (Coinbase's newer default, Ed25519, may be refused). "
            "Then load its JSON file or paste the two values below. Never share the private key with anyone.")
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))
        ttk.Button(f, text="Load key from JSON file...", command=lambda: load_file()).grid(row=2, column=0, sticky="w")
        ttk.Label(f, text="API key name").grid(row=3, column=0, sticky="w", pady=(10, 0))
        name_e = ttk.Entry(f, width=64)
        name_e.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Label(f, text="Private key").grid(row=5, column=0, sticky="w", pady=(8, 0))
        key_t = tk.Text(f, width=64, height=6)
        key_t.grid(row=6, column=0, columnspan=2)
        remember = tk.BooleanVar(value=False)
        can_keep = E.keyring_available()
        ttk.Checkbutton(f, text="Remember on this computer (OS keychain)" if can_keep else
                        "Remember (needs the secure-storage add-on: Tools > Install add-ons)", variable=remember,
                        state="normal" if can_keep else "disabled").grid(row=7, column=0, sticky="w", pady=(8, 0))

        def load_file():
            p = filedialog.askopenfilename(title="Coinbase API key file", filetypes=[("JSON", "*.json")], parent=w)
            if not p:
                return
            try:
                with open(p) as fh:
                    name, secret = E.parse_key_json(fh.read())
            except Exception as e:
                return messagebox.showerror("Key file", f"Could not read that file: {e}", parent=w)
            name_e.delete(0, "end")
            name_e.insert(0, name)
            key_t.delete("1.0", "end")
            key_t.insert("1.0", secret)
            if name and "apiKeys" not in name:
                messagebox.showinfo("Key file", "This file only contains the key's ID. Copy the full API key name "
                                                "(organizations/.../apiKeys/...) from the Coinbase page into the first box.", parent=w)

        def go():
            name, secret = name_e.get().strip(), E.normalize_secret(key_t.get("1.0", "end"))
            ok, msg = E.check_credentials(name, secret)
            if not ok:
                return messagebox.showerror("Login", msg, parent=w)
            key_t.delete("1.0", "end")
            w.destroy()
            self.start_login(name, secret, remember.get())
        row = ttk.Frame(f)
        row.grid(row=8, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(row, text="Cancel", command=w.destroy).pack(side="left", padx=4)
        ttk.Button(row, text="Log in", style="Accent.TButton", command=go).pack(side="left")

    def start_login(self, name, secret, remember):
        self.logging_in = True
        self.update_status()
        threading.Thread(target=self._connect, args=(name, secret, remember), daemon=True).start()

    def _connect(self, name, secret, remember):
        try:
            from coinbase.rest import RESTClient
        except ImportError:
            return self.q.put(("login_failed", "A needed add-on is missing. Open Tools > Install add-ons (setup) and click Install."))
        try:
            client = RESTClient(api_key=name, api_secret=secret)
            accts = E.to_dict(client.get_accounts(limit=250))["accounts"]
            usd = self.read_balances(accts)
        except Exception as e:
            return self.q.put(("login_failed", E.explain_login_error(e, secret)))
        if remember and not E.save_login(name, secret):
            self.q.put(("log", "No OS keychain available, so the login was not saved."))
        self.q.put(("connected", (client, usd)))

    def logout(self):
        if self.running and self.live:
            return messagebox.showinfo("Log out", "Stop trading first.")
        E.clear_login()
        self.client, self.usd_avail = None, 0.0
        self.update_status()
        self.write("Logged out. Saved login removed from this computer.")

    def confirm_live(self, resume):
        if not self.client:
            messagebox.showerror("Live mode", "Connect to Coinbase first.")
            return False
        min_budget = MIN_LIVE_ORDER / (self.settings["trade_pct"] / 100)
        if self.cash > self.usd_avail or (not resume and self.cash < min_budget):
            messagebox.showerror(
                "Live mode", f"Budget must be at least ${min_budget:.0f} (so one trade is ${MIN_LIVE_ORDER:.0f}+) "
                             f"and no more than your available {self.quote} (${self.usd_avail:,.2f}).")
            return False
        warn = ("\nWildcard picks strategies automatically based on recent results. "
                "Recent winners often stop winning" + (", and with 'Winners trade more' on it trades more often, "
                "which means more fees" if self.more_var.get() else "") + ".\n" if self.wild_var.get() else "")
        if self.cfg.get("live_locked"):
            ans = simpledialog.askstring("LIVE is locked", "The kill switch locked LIVE mode.\n\nType UNLOCK to allow live "
                                                           "trading again:")
            if (ans or "").strip() != "UNLOCK":
                return False
            self.cfg["live_locked"] = False
            E.save_json(CONFIG_PATH, self.cfg)
        roster = self.roster("live")
        self.grad_only = False
        if self.wild_var.get() and roster in ("hof", "team") and self.settings.get("graduation", True):
            squad = E.team() if roster == "team" else self.hof()
            fails = [(b, self.graduated(b)[1]) for b in squad if not self.graduated(b)[0]]
            if fails:
                lines = "\n".join(f"- {b.get('nickname') or E.bot_nickname(E.bot_key(b))}: {', '.join(m)}" for b, m in fails[:8])
                more = f"\n...and {len(fails) - 8} more" if len(fails) > 8 else ""
                ans_g = messagebox.askyesnocancel(
                    "Graduation check", f"{len(fails)} of {len(squad)} bots haven't graduated to real money yet:\n\n"
                    f"{lines}{more}\n\nYes = trade only with the {len(squad) - len(fails)} that graduated\n"
                    f"No = trade with all of them anyway\nCancel = don't go live")
                if ans_g is None:
                    return False
                self.grad_only = bool(ans_g)
        who = ({"farm": "the WHOLE FARM (any winning bot, including brand-new ones). Tip: choose 'Hall of Fame only' "
                        "under Strategy to trade real money with only the bots you picked",
                "team": f"only YOUR TEAM ({len(E.team())} bots)",
                "hof": f"only YOUR HALL OF FAME ({len(self.hof())} bots you picked)"}[roster]
               if self.wild_var.get() else "the strategies you ticked")
        ans = simpledialog.askstring(
            "Confirm LIVE trading",
            f"This will place REAL market orders using up to ${self.cash:,.2f}.\n"
            f"Bots allowed to trade: {who}.\n"
            f"Automated strategies can lose money.{warn}\nType LIVE to continue:")
        return (ans or "").strip() == "LIVE"

    def product_info(self, c):
        """(base increment, quote/price increment) for a coin, cached."""
        if c not in self.inc:
            p = E.to_dict(self.client.get_product(f"{c}-{self.quote}"))
            self.inc[c] = (Decimal(p["base_increment"]), Decimal(p.get("quote_increment") or "0.01"))
        return self.inc[c]

    def best_bid_ask(self, c):
        r = E.to_dict(self.client.get_best_bid_ask(product_ids=[f"{c}-{self.quote}"]))
        book = (r.get("pricebooks") or [{}])[0]
        bid = Decimal(str(book["bids"][0]["price"]))
        ask = Decimal(str(book["asks"][0]["price"]))
        return bid, ask

    def use_limit(self, reason):
        # Safety exits must happen, so stops and Sell-all always use market orders.
        return self.settings.get("order_type") == "limit" and reason in ("signal", "add-on", "take-profit")

    def place_limit(self, c, side, size, price):
        """Post-only limit order that waits up to limit_wait seconds. Returns (qty, value, fee) filled (can be 0)."""
        pid = f"{c}-{self.quote}"
        args = dict(client_order_id=str(uuid.uuid4()), product_id=pid, base_size=format(size, "f"),
                    limit_price=format(price, "f"), post_only=True)
        r = E.to_dict((self.client.limit_order_gtc_buy if side == "buy" else self.client.limit_order_gtc_sell)(**args))
        if not r.get("success"):
            err = r.get("error_response") or {}
            raise RuntimeError(err.get("message") or r.get("failure_reason") or "limit order rejected")
        oid = r["success_response"]["order_id"]
        self.__dict__.setdefault("open_oids", set()).add(oid)  # the kill switch cancels these
        o, until = {}, time.time() + max(3, int(self.settings.get("limit_wait", 20)))
        while time.time() < until:
            o = E.to_dict(self.client.get_order(order_id=oid))["order"]
            if o.get("status") in ("FILLED", "CANCELLED", "EXPIRED", "FAILED"):
                break
            time.sleep(1.0)
        if o.get("status") not in ("FILLED", "CANCELLED", "EXPIRED", "FAILED"):
            try:
                self.client.cancel_orders(order_ids=[oid])
            except Exception:
                pass
            for _ in range(6):  # the cancel and any last-moment fill settle
                o = E.to_dict(self.client.get_order(order_id=oid))["order"]
                if o.get("status") in ("FILLED", "CANCELLED", "EXPIRED", "FAILED"):
                    break
                time.sleep(0.5)
        self.open_oids.discard(oid)
        return tuple(float(o.get(k) or 0) for k in ("filled_size", "filled_value", "total_fees"))

    def live_buy(self, c, spend, reason):
        try:
            qty = val = fee = 0.0
            if self.use_limit(reason):
                try:
                    base_inc, px_inc = self.product_info(c)
                    bid, _ = self.best_bid_ask(c)
                    price = (bid // px_inc) * px_inc
                    size = (Decimal(str(spend * 0.995)) / price // base_inc) * base_inc  # leave room for the fee
                    if size > 0:
                        qty, val, fee = self.place_limit(c, "buy", size, price)
                except Exception as e:
                    self.q.put(("log", (f"Limit buy for {c} didn't go through ({e}).", "warn")))
                left = spend - val - fee
                if qty > 0 and left < MIN_LIVE_ORDER:
                    return self.q.put(("fill", ("buy", c, qty, val + fee, spend, reason + " (limit)")))
                if not self.settings.get("limit_fallback", True):
                    if qty > 0:
                        return self.q.put(("fill", ("buy", c, qty, val + fee, spend, reason + " (limit, part filled)")))
                    return self.q.put(("unfilled", ("buy", c, spend)))
                spend_left = left
            else:
                spend_left = spend
            r = E.to_dict(self.client.market_order_buy(
                client_order_id=str(uuid.uuid4()), product_id=f"{c}-{self.quote}", quote_size=f"{spend_left:.2f}"))
            q2, v2, f2 = self.await_fill(r)
            self.q.put(("fill", ("buy", c, qty + q2, val + fee + v2 + f2, spend,
                                 reason + (" (limit + market)" if qty > 0 else ""))))
        except Exception as e:
            if qty > 0:  # part of it filled before the problem: keep what we have
                return self.q.put(("fill", ("buy", c, qty, val + fee, spend, reason + " (limit, part filled)")))
            self.q.put(("fail", ("buy", c, spend, str(e))))

    def live_sell(self, c, qty, reason):
        got_q = got_v = 0.0
        try:
            base_inc, px_inc = self.product_info(c)
            size = (Decimal(str(qty)) // base_inc) * base_inc
            if size <= 0:
                raise RuntimeError("position below minimum increment")
            if self.use_limit(reason):
                try:
                    _, ask = self.best_bid_ask(c)
                    price = -((-ask) // px_inc) * px_inc  # round up to the price step
                    q1, v1, f1 = self.place_limit(c, "sell", size, price)
                    got_q, got_v = q1, v1 - f1
                except Exception as e:
                    self.q.put(("log", (f"Limit sell for {c} didn't go through ({e}).", "warn")))
                size = ((size - Decimal(str(got_q))) // base_inc) * base_inc
                if size <= 0 or not self.settings.get("limit_fallback", True):
                    if got_q > 0:
                        return self.q.put(("fill", ("sell", c, got_q, got_v, 0, reason + " (limit)")))
                    return self.q.put(("unfilled", ("sell", c, 0)))
            r = E.to_dict(self.client.market_order_sell(
                client_order_id=str(uuid.uuid4()), product_id=f"{c}-{self.quote}", base_size=format(size, "f")))
            q, val, fee = self.await_fill(r)
            self.q.put(("fill", ("sell", c, got_q + q, got_v + val - fee, 0,
                                 reason + (" (limit + market)" if got_q > 0 else ""))))
        except Exception as e:
            if got_q > 0:
                return self.q.put(("fill", ("sell", c, got_q, got_v, 0, reason + " (limit, part filled)")))
            self.q.put(("fail", ("sell", c, 0, str(e))))

    def await_fill(self, r):
        if not r.get("success"):
            err = r.get("error_response") or {}
            raise RuntimeError(err.get("message") or r.get("failure_reason") or "order rejected")
        oid = r["success_response"]["order_id"]
        o = {}
        for _ in range(10):
            o = E.to_dict(self.client.get_order(order_id=oid))["order"]
            if o.get("status") in ("FILLED", "CANCELLED", "EXPIRED", "FAILED"):
                break
            time.sleep(0.5)
        qty, val, fee = (float(o.get(k) or 0) for k in ("filled_size", "filled_value", "total_fees"))
        if qty <= 0:
            raise RuntimeError(f"order {o.get('status', 'unknown')} with no fill")
        return qty, val, fee

    def on_unfilled(self, side, c, reserved):
        """A limit order that found no taker in time and had no market fallback: not an error."""
        self.pending.discard(c)
        self.pending_who.pop(c, None)
        self.cash += reserved
        self.write(f"Limit {side} for {c} didn't fill in time, so nothing was traded "
                   f"(turn on the market fallback in Settings > Orders to always trade).", "warn")
        self.refresh_view()

    # ---------------- connection light / alerts ----------------
    def on_net(self, got, want, secs):
        if got == want:
            col, txt = (GREEN if secs < 1.5 else "#fbbf24"), f"Prices OK, {secs * 1000:.0f} ms for {want} coin{'s' if want != 1 else ''}"
        elif got:
            col, txt = "#fbbf24", f"Only {got} of {want} prices came back ({secs:.1f} s)"
        else:
            col, txt = RED, "No prices: Coinbase can't be reached right now"
        self.net_lbl.config(fg=col)
        self.net_tip.text = txt
        if not got and self.running:
            self.alert("problems", "CBBF: can't reach Coinbase for prices.", once_key="net")

    def alert(self, kind, text, once_key=None):
        """Sends a phone alert (Discord/Telegram) in the background, if set up and wanted for this kind."""
        if not E.alerts_configured(self.settings) or not self.settings.get(f"alert_{kind}", True):
            return
        if once_key:
            last = getattr(self, "_alert_once", {}).get(once_key, 0)
            if time.time() - last < 1800:
                return
            self.__dict__.setdefault("_alert_once", {})[once_key] = time.time()
        s = dict(self.settings)

        def work():
            for p in E.send_alert(s, "CBBF: " + text if not text.startswith("CBBF") else text):
                self.q.put(("log", (f"Alert not sent: {p}", "warn")))
        threading.Thread(target=work, daemon=True).start()

    def on_fill(self, side, c, qty, amount, reserved, reason):
        self.fees_paid += amount * float(self.settings.get("fee_pct", 0.6)) / 100  # estimate; Coinbase shows the exact fee
        self.pending.discard(c)
        self.fails = 0
        px = amount / qty if qty else 0
        if side == "buy":
            self.cash += reserved - amount
            self.pos[c] = self.pos.get(c, 0) + qty
            self.cost[c] = self.cost.get(c, 0) + amount
            self.log_trade("buy", c, qty, px, amount, None, reason)
        else:
            held = self.pos.get(c, 0)
            basis = self.cost.get(c, 0) * (qty / held if held else 1)
            self.cash += amount
            self.pos[c] = max(held - qty, 0)
            self.cost[c] = max(self.cost.get(c, 0) - basis, 0)
            if self.pos[c] < 1e-12:
                self.pos[c], self.cost[c] = 0, 0
            self.log_trade("sell", c, qty, px, amount, amount - basis, reason)
        self.refresh_view()

    def on_fail(self, side, c, reserved, err):
        self.pending.discard(c)
        self.pending_who.pop(c, None)
        self.cash += reserved
        self.fails += 1
        self.write(f"Order failed ({side} {c}): {err}")
        self.alert("problems", f"Order failed ({side} {c}): {err}")
        if self.fails >= MAX_FAILS and self.running:
            self.running = False
            self.ui_running(False)
            self.write(f"Stopped after {MAX_FAILS} consecutive order failures.")
            self.notify("CBBF", "Stopped after repeated order failures.", sound="problem")
        self.refresh_view()

    # ---------------- add-on setup ----------------
    def maybe_setup(self):
        if E.missing_deps() and not self.cfg.get("skip_setup_prompt"):
            self.open_setup(auto=True)

    def open_setup(self, auto=False):
        if getattr(self, "setup_win", None) is not None and self.setup_win.winfo_exists():
            self.setup_win.lift()
            return
        w = self.setup_win = tk.Toplevel(self.root)
        w.title("Install add-ons")
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack()
        ttk.Label(f, text="Add-ons", font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(f, wraplength=470, foreground=MUTED, text=(
            "Practice mode works without anything extra. These add-ons are only needed for Live trading with Coinbase. "
            "Installing them takes about a minute and needs an internet connection.")).pack(anchor="w", pady=(2, 10))
        self.setup_rows = {}
        for d in E.DEPENDENCIES:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=2)
            mark = tk.Label(row, text="", width=3, font=("", 12, "bold"))
            mark.pack(side="left")
            col = ttk.Frame(row)
            col.pack(side="left", anchor="w")
            ttk.Label(col, text=d["label"], font=("", 10, "bold")).pack(anchor="w")
            ttk.Label(col, text=d["why"], foreground=MUTED, wraplength=420).pack(anchor="w")
            self.setup_rows[d["module"]] = mark
        self.setup_text = tk.Text(f, width=64, height=7, state="disabled", relief="flat", bg=CARD, font=("Courier", 8))
        self.setup_msg = ttk.Label(f, text="", wraplength=470, justify="left")
        self.setup_msg.pack(anchor="w", pady=(10, 2))
        self.setup_text.pack(pady=(0, 8))
        btns = ttk.Frame(f)
        btns.pack(fill="x")
        if auto:
            skip = tk.BooleanVar(value=False)

            def close_auto():
                if skip.get():
                    self.cfg["skip_setup_prompt"] = True
                    E.save_json(CONFIG_PATH, self.cfg)
                w.destroy()
            ttk.Checkbutton(btns, text="Don't remind me", variable=skip).pack(side="left")
            ttk.Button(btns, text="Not now", command=close_auto).pack(side="right", padx=(6, 0))
        else:
            ttk.Button(btns, text="Close", command=w.destroy).pack(side="right", padx=(6, 0))
        self.setup_btn = ttk.Button(btns, text="Install now", style="Accent.TButton", command=self.start_install)
        self.setup_btn.pack(side="right")
        self.refresh_setup()

    def refresh_setup(self):
        status = E.dep_status()
        for d, ok in status:
            self.setup_rows[d["module"]].config(text="\u2713" if ok else "\u2717", fg=GREEN if ok else RED)
        gone = [d for d, ok in status if not ok]
        self.setup_btn.config(state="normal" if gone else "disabled", text="Install now" if gone else "All installed")
        if not gone and not self.setup_msg.cget("text"):
            self.setup_msg.config(text="Everything is installed. You're ready for Live mode.")

    def start_install(self):
        specs = [d["pip"] for d in E.missing_deps()]
        if not specs:
            return self.refresh_setup()
        self.setup_btn.config(state="disabled", text="Installing...")
        self.setup_msg.config(text="Installing, please wait. This can take a minute...")
        self.setup_text.config(state="normal")
        self.setup_text.delete("1.0", "end")
        self.setup_text.config(state="disabled")
        self.write("Installing add-ons...")

        def work():
            ok, msg = E.install_packages(specs, lambda line: self.q.put(("dep_log", line)))
            self.q.put(("dep_done", (ok, msg)))
        threading.Thread(target=work, daemon=True).start()

    def setup_log(self, line):
        if getattr(self, "setup_win", None) is None or not self.setup_win.winfo_exists():
            return
        self.setup_text.config(state="normal")
        self.setup_text.insert("end", line + "\n")
        self.setup_text.see("end")
        self.setup_text.config(state="disabled")

    def setup_done(self, ok, msg):
        self.write(msg, "good" if ok else "bad")
        if getattr(self, "setup_win", None) is not None and self.setup_win.winfo_exists():
            self.setup_msg.config(text=msg, foreground=GREEN if ok else RED)
            self.refresh_setup()
        self.update_status()
        if ok and not self.client and self.mode_var.get() == "live":
            self.write("Add-ons ready. Click 'Log in to Coinbase' to continue.")

    # ---------------- backtest ----------------
    def backtest(self):
        coins = [c for c, v in self.coin_vars.items() if v.get()]
        names = [n for n, v in self.strat_vars.items() if v.get()] or [n for n in E.STRATS]
        if not coins:
            return messagebox.showinfo("Backtest", "Check at least one coin.")
        s = dict(self.settings)
        s["quote"] = self.cur_quote()
        self.write(f"Backtesting {', '.join(coins)} on {E.fmt_bar(E.candle_gran(s['bar_minutes']) / 60)} candles (fetching history)...")
        threading.Thread(target=self._backtest, args=(coins, names, s), daemon=True).start()

    def _backtest(self, coins, names, s):
        try:
            gran = E.candle_gran(s["bar_minutes"])
            data = {c: E.fetch_ohlcv(c, gran, 900, s.get("quote", "USD")) for c in coins}
            closes = {c: d["c"] for c, d in data.items()}
            extra = {c: dict(h=d["h"], l=d["l"], v=d["v"]) for c, d in data.items()}
            L = min(len(v) for v in closes.values())
            if L < 150:
                return self.q.put(("log", "Not enough history for a backtest."))
            rows = E.run_backtest(closes, names, s, extra=extra)
            days = L * gran / 86400
            self.q.put(("bt", (rows, f"{', '.join(coins)} | {L} bars of {E.fmt_bar(gran / 60)} (~{days:.1f} days)")))
        except Exception as e:
            self.q.put(("log", f"Backtest failed: {e}"))

    def show_backtest(self, rows, info):
        w = tk.Toplevel(self.root)
        w.title("Backtest results")
        ttk.Label(w, text=info, padding=6).pack(anchor="w")
        t = ttk.Treeview(w, columns=("s", "r", "d", "t"), show="headings", height=len(rows))
        for c, h, wd in (("s", "Strategy", 220), ("r", "Return", 90), ("d", "Max drawdown", 110), ("t", "Trades", 70)):
            t.heading(c, text=h)
            t.column(c, width=wd, anchor="w" if c == "s" else "e")
        for name, ret, dd, tr in rows:
            t.insert("", "end", values=(STRAT_LABELS.get(name, name), f"{ret * 100:+.2f}%", f"{dd * 100:.1f}%", tr))
        t.pack(padx=6)
        ttk.Label(w, padding=6, wraplength=470, foreground="gray",
                  text="Past results do not predict future results. Fees and your stop-loss / take-profit "
                       "settings are included. The wildcard row only starts ranking after ~100 bars.").pack()
        self.write("Backtest complete.")

    # ---------------- Bot Farm grid (default tab): eggs, rising, BEASTS, Hall of Fame ----------------
    GRID_STATUS = {"active": ("WINNING", "#22c55e"), "slowed": ("SHAKY", "#f59e0b"), "standby": ("STANDBY", "#3b82f6"),
                   "benched": ("RESTING", "#6b7280"), "warming up": ("LEARNING", "#8b5cf6")}
    DEN_COLORS = {"egg": ("#22c55e", "#14532d"), "rising": ("#3b82f6", "#1e3a8a"), "beast": ("#f97316", "#7c2d12")}
    DEN_PORTRAIT = {"egg": "#0c2416", "rising": "#0c1a36", "beast": "#2a1406"}
    SECTIONS = (("Eggs", "\u25d2 Eggs"), ("Rising", "\u25b2 Rising"), ("BEASTS", "\u265b BEASTS"),
                ("Hall of Fame", "\u2605 Hall of Fame"), ("All", "All"), ("Caught", "\u25c9 Library"))
    SECTION_TIPS = {
        "Eggs": "New bots still learning. They hatch once they've seen enough prices and made a few trades.",
        "Rising": "Hatched bots doing OK: shaky, on standby, or winning but not dominant yet. Resting ones are at the end.",
        "BEASTS": "The dominant ones: winning, steady (3 of the last 4 stretches or more) and ahead of holding. "
                  "The account follows these the most.",
        "Hall of Fame": "The bots YOU picked as champions. Only you add or remove them (right-click any bot). "
                        "'Bots trading: Hall of Fame only' trades with just these.",
        "All": "Every bot on the farm.", "Caught": "Bots on the farm that are saved in your Bot Library."}

    def tier(self, r):
        """egg / rising / beast for a scoreboard row."""
        v = r["v"]
        if r["status"] == "warming up" or v.age < (self.tourn.W if self.tourn else 100):
            return "egg"
        if r["status"] == "active" and ((r.get("cons") or 0) >= 0.75 or v.streak >= 30) and (r.get("edge") is None or r["edge"] > 0):
            return "beast"
        return "rising"

    def build_grid_tab(self, parent):
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", padx=6, pady=(6, 2))
        chips = tk.Frame(top, bg=BG)
        chips.pack(side="left")
        self.grid_filter = tk.StringVar(value=self.cfg.get("grid_filter", "BEASTS"))
        if self.grid_filter.get() not in dict(self.SECTIONS):
            self.grid_filter.set("BEASTS")
        self.grid_chips = {}
        for key, label in self.SECTIONS:
            l = tk.Label(chips, text=label, bg=CARD, fg=MUTED, padx=12, pady=4, cursor="hand2", font=("", 9, "bold"))
            l.pack(side="left", padx=(0, 3))
            l.bind("<Button-1>", lambda e, n=key: self._grid_set_filter(n))
            Tip(l, self.SECTION_TIPS[key])
            self.grid_chips[key] = l
        self.catch_btn = ttk.Button(top, text="Catch!", style="Accent.TButton", command=self.catch_selected)
        self.catch_btn.pack(side="right")
        # second row: actions on the left, view controls on the right
        tools = tk.Frame(parent, bg=BG)
        tools.pack(fill="x", padx=6, pady=(2, 2))
        db = ttk.Button(tools, text="\u270e Design an egg", style="Plain.TButton", command=self.open_designer)
        eb = ttk.Button(tools, text="+ Add eggs", style="Plain.TButton", command=self.add_eggs)
        tb = ttk.Button(tools, text="\u2694 Train", style="Plain.TButton", command=self.open_training)
        self.roam_btn = ttk.Button(tools, text="\u25c7 Free roam: ON" if self.cfg.get("roam_on") else "\u25c7 Free roam",
                                   style="Plain.TButton", command=self.open_roam)
        xb = ttk.Button(tools, text="\u21e9 Export", style="Plain.TButton", command=self.export_bots)
        pb = ttk.Button(tools, text="Preset \u25be", style="Plain.TButton")
        pb.configure(command=lambda: self.preset_menu(pb))
        for b in (db, eb, tb, self.roam_btn, xb, pb):
            b.pack(side="left", padx=(0, 6))
        Tip(db, "Write your own bot: strategy, numbers (like RSI buy below 28 / sell above 72), pace, exits and habits. (Ctrl+E)")
        Tip(eb, "Hatch brand-new random bots now. They learn on recent prices and have to earn their place.")
        Tip(tb, "Training camp: run a whole farm through days or months of past prices, several times over, then test "
                "the best on recent prices they never saw. Bring the graduates to your farm or Library.")
        Tip(self.roam_btn, "Free roam: switch it on and the farm goes back in time by itself, choosing its own stretches "
                           "of the past and candle sizes to learn from. Opens a screen showing where it's learning.")
        Tip(xb, "Export bots to a file: the whole farm, a den, the Hall of Fame, the Library or the ones you selected.")
        Tip(pb, "Farm presets: Calm (few bots, patient), Balanced (default) or Wild (big, fast-evolving farm).")
        self.farm_size = tk.StringVar(value=str(int(self.settings.get("max_bots", 30))))
        fs = ttk.Spinbox(tools, from_=14, to=200, increment=2, width=5, textvariable=self.farm_size, command=self.set_farm_size)
        fs.pack(side="right")
        fs.bind("<Return>", lambda e: self.set_farm_size())
        fs.bind("<FocusOut>", lambda e: self.set_farm_size())
        tk.Label(tools, text="Farm size", bg=BG, fg=MUTED, font=("", 8)).pack(side="right", padx=(0, 4))
        Tip(fs, "Most bots on the farm at once (14 to 200). More bots explore more ideas but use more computer power; "
                "the farm grows toward this number as it evolves, or press '+ Add eggs'.")
        self.farm_style = tk.StringVar(value=self.cfg.get("farm_style", "Dragons"))
        vb = ttk.Combobox(tools, textvariable=self.farm_style, values=["Dragons", "Simple cards", "List"], state="readonly", width=12)
        vb.pack(side="right", padx=(0, 10))
        vb.bind("<<ComboboxSelected>>", lambda e: self.set_farm_style())
        tk.Label(tools, text="View", bg=BG, fg=MUTED, font=("", 8)).pack(side="right", padx=(0, 4))
        self.motion_var = tk.StringVar(value=self.settings.get("animations", "Full"))
        mb = ttk.Combobox(tools, textvariable=self.motion_var, values=["Full", "Light", "Off"], state="readonly", width=5)
        mb.pack(side="right", padx=(0, 10))
        mb.bind("<<ComboboxSelected>>", lambda e: self._set_motion())
        tk.Label(tools, text="Motion", bg=BG, fg=MUTED, font=("", 8)).pack(side="right", padx=(0, 4))
        Tip(mb, "Full: smooth animation. Light: about 2 frames a second, uses very little computer power. Off: still pictures.")
        Tip(vb, "Dragons: animated cards. Simple cards: the same cards without animation. List: a plain table.")
        srow = tk.Frame(parent, bg=BG)
        srow.pack(fill="x", padx=8)
        self.grid_search = tk.StringVar()
        se = tk.Entry(srow, textvariable=self.grid_search, width=24, bg=CARD, fg=TEXT, insertbackground=TEXT,
                      relief="flat", highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        se.pack(side="right", ipady=2)
        tk.Label(srow, text="\u2315 Search", bg=BG, fg=MUTED, font=("", 8)).pack(side="right", padx=(0, 4))
        Tip(se, "Find dragons by name, strategy, trait, never-rule or study. Esc clears it.")
        self.grid_search.trace_add("write", lambda *a: self.soon(self.grid_refresh, 150))
        se.bind("<Escape>", lambda e: self.grid_search.set(""))
        self.grid_sum = tk.Label(srow, text="", bg=BG, fg=MUTED, font=("", 9), anchor="w")
        self.grid_sum.pack(side="left", fill="x", expand=True)
        self.pulse_lbl = tk.Label(parent, text="", bg=CARD, fg=TEXT, font=("", 8, "bold"), anchor="w", padx=8, pady=3)
        self.pulse_lbl.pack(fill="x", padx=8, pady=(2, 0))
        Tip(self.pulse_lbl, "Farm pulse: births and retirements in the last hour, the current season, the best bot of the "
                            "hour, and how your Hall of Fame would be doing on its own (Shadow Live).")
        self.grid_area = tk.Frame(parent, bg=BG)
        self.grid_area.pack(fill="both", expand=True)
        self.grid = tk.Canvas(self.grid_area, bg=BG, highlightthickness=0)
        ys = ttk.Scrollbar(self.grid_area, orient="vertical", command=self.grid.yview)
        self.grid.configure(yscrollcommand=lambda a, b: (ys.set(a, b), self._grid_scrolled()))
        self.grid_ys = ys
        ys.pack(side="right", fill="y")
        self.grid.pack(fill="both", expand=True, padx=(6, 0), pady=(2, 4))
        self.grid_list = None
        self.grid.bind("<Configure>", lambda e: self.later(self.grid_refresh, 180))
        self.grid.bind("<Button-1>", self._grid_click)
        self.grid.bind("<Double-1>", lambda e: (self._grid_click(e), self.bot_detail() if self.selected_variant() else None))
        self.grid.bind("<Motion>", self._grid_motion)
        self.grid.bind("<B1-Motion>", self._grid_drag)
        self.grid.bind("<ButtonRelease-1>", self._grid_drop)
        for btn in ("<Button-3>", "<Button-2>"):
            self.grid.bind(btn, lambda e: (lambda card: self.bot_menu(e, card[4]) if card else None)(self._grid_at(e)))
        self.grid.bind("<Leave>", lambda e: setattr(self, "grid_hover", None))
        for ev, d in (("<Button-4>", -1), ("<Button-5>", 1)):
            self.grid.bind(ev, lambda e, d=d: self.grid.yview_scroll(d, "units"))
        self.grid.bind("<MouseWheel>", lambda e: self.grid.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.grid_cards, self.grid_hover, self.beasts_seen = [], None, set()
        self._grid_paint_chips()
        self.root.after(300, self.animate_all)

    def _set_motion(self):
        self.settings["animations"] = self.motion_var.get()
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)

    def set_farm_size(self):
        try:
            n = int(float(self.farm_size.get()))
        except ValueError:
            return
        n = min(max(n, 14), 200)
        self.farm_size.set(str(n))
        if n == int(self.settings.get("max_bots", 30)):
            return
        self.settings["max_bots"] = n
        self.cfg["settings"] = self.settings
        E.save_json(CONFIG_PATH, self.cfg)
        if self.tourn:
            self.tourn.cap = n
            self.tourn.family_cap = max(5, -(-n // 4))
            while len(self.tourn.vars) > n:  # shrink: let the weakest go
                worst = [v for v, _ in reversed(self.tourn.ranking()) if v not in self.tourn.active] or \
                        [v for v in self.tourn.vars if self.tourn.info.get(id(v), {}).get("status") == "warming up"]
                if not worst:
                    break
                self.tourn._remove(worst[0], "farm made smaller")
            self.tourn._rate()
            self.refresh_board()
        self.write(f"Farm size: up to {n} bots." + (" It grows toward this as it evolves, or press '+ Add eggs'." if self.tourn else ""))

    def add_eggs(self):
        if not self.tourn or not self.seeded:
            return messagebox.showinfo("Add eggs", "Start the bot with 'Let the bot choose (Wildcard)' ticked first.")
        room = self.tourn.cap - len(self.tourn.vars)
        n = simpledialog.askinteger("Add eggs", f"How many new eggs? (room for {max(room, 0)} more; raise Farm size for more)",
                                    initialvalue=max(1, min(5, room)) if room > 0 else 0, minvalue=0, maxvalue=200)
        if not n:
            return
        if n > room:
            self.tourn.cap = len(self.tourn.vars) + n
            self.settings["max_bots"] = min(200, self.tourn.cap)
            self.farm_size.set(str(self.settings["max_bots"]))
            self.cfg["settings"] = self.settings
            E.save_json(CONFIG_PATH, self.cfg)
        closes = {c: self.hist[c] for c in self.session_coins}
        made = 0
        for _ in range(n):
            if self.tourn.spawn_random(closes):
                made += 1
        self.flush_events()
        self.refresh_board()
        self.write(f"\u25d2 {made} new egg{'s' if made != 1 else ''} added to the farm.", "good")

    def set_farm_style(self):
        st = self.farm_style.get()
        self.cfg["farm_style"] = st
        E.save_json(CONFIG_PATH, self.cfg)
        if st == "List":
            self.grid.pack_forget()
            self.grid_ys.pack_forget()
            if self.grid_list is None:
                box = tk.Frame(self.grid_area, bg=BG)
                self.grid_list_box = box
                self.grid_list = self.make_tree(box, ("tier", "name", "strategy", "status", "recent", "edge", "winrate", "trades", "saved"),
                                                (80, 140, 190, 140, 70, 75, 65, 55, 110),
                                                ("Section", "Name", "Strategy", "Status", "Recent", "vs holding", "Win rate",
                                                 "Trades", "Saved"))
                self.grid_list.bind("<Double-1>", lambda e: self.bot_detail(self._list_variant(), quiet=True)
                                    if self.on_row(self.grid_list, e) else None)
                self.grid_list.bind("<<TreeviewSelect>>", lambda e: self._list_select())
                for btn in ("<Button-3>", "<Button-2>"):
                    self.grid_list.bind(btn, lambda e: (self.grid_list.selection_set(self.grid_list.identify_row(e.y))
                                                        if self.grid_list.identify_row(e.y) else None,
                                                        self.bot_menu(e, self._list_variant())))
            self.grid_list_box.pack(fill="both", expand=True, padx=6, pady=(2, 4))
        else:
            if self.grid_list is not None:
                self.grid_list_box.pack_forget()
            self.grid_ys.pack(side="right", fill="y")
            self.grid.pack(fill="both", expand=True, padx=(6, 0), pady=(2, 4))
        if hasattr(self, "farm"):
            if st == "Dragons":
                if not self.farm.winfo_ismapped():
                    self.farm.pack(fill="x", padx=4, pady=(0, 4), after=self.best_lbl)
            else:
                self.farm.pack_forget()
        self.grid_refresh()

    def _list_variant(self):
        sel = self.grid_list.selection() if self.grid_list is not None else ()
        if not sel or not self.tourn:
            return None
        return next((v for v in self.tourn.vars if str(id(v)) == sel[0]), None)

    def _list_select(self):
        v = self._list_variant()
        if v and self.board.exists(str(id(v))):
            self.board.selection_set(str(id(v)))

    def _fill_grid_list(self, items):
        t = self.grid_list
        sel = t.selection()
        t.delete(*t.get_children())
        lib, hofk = self.library_keys(), self.hof_keys()
        names = {"egg": "Egg", "rising": "Rising", "beast": "BEAST"}
        for kind, data, extra in items:
            if kind == "hof":
                b = data
                lt = b.get("lifetime") or {}
                t.insert("", "end", values=("Hall of Fame", E.bot_nickname(E.bot_key(b)), STRAT_LABELS.get(b["name"], b["name"]),
                                            "on the farm" if extra else "resting",
                                            "-", "-" if lt.get("vs_holding_pct") is None else f"{lt['vs_holding_pct']:+.1f}%",
                                            "-" if lt.get("win_rate_pct") is None else f"{lt['win_rate_pct']:.0f}%",
                                            lt.get("trades", "-"), "\u2605 HoF"))
                continue
            r, tier = data, extra
            v, lf = r["v"], r["life"]
            saved = ("\u2605 HoF" if v.key() in hofk else "") + (" \u25c9 Library" if v.key() in lib else "")
            t.insert("", "end", iid=str(id(v)), tags=(STATUS_TAG.get(r["status"], "learning"),), values=(
                names[tier], E.bot_nickname(v.key()), friendly_label(v.name, v.params, v.tf, self.session_bar),
                STATUS_TEXT.get(r["status"], r["status"]), pct_txt(r["score"]), pct_txt(r.get("edge")),
                "-" if lf["winrate"] is None else f"{lf['winrate'] * 100:.0f}%", lf["trades"], saved.strip()))
        amber = "#fbbf24" if DARK else "#b45309"
        for tag, color in (("win", GREEN), ("shaky", amber), ("standby", TEXT), ("stopped", MUTED), ("learning", MUTED)):
            t.tag_configure(tag, foreground=color)
        keep = [i for i in sel if t.exists(i)]
        if keep:
            t.selection_set(keep)
        resort(t)

    def _grid_scrolled(self):
        """Scrolling the farm: draw the newly visible cards once you get near the edge of what's drawn."""
        band = getattr(self, "_grid_drawn", None)
        if not band:
            return
        top, bot = self.grid.canvasy(0), self.grid.canvasy(self.grid.winfo_height())
        if top < band[0] + 60 or bot > band[1] - 60:
            self.soon(self.grid_refresh, 30)

    def _grid_set_filter(self, n):
        self.grid_filter.set(n)
        self.cfg["grid_filter"] = n
        self._grid_paint_chips()
        self.grid.yview_moveto(0)
        self.grid_refresh()

    def _grid_paint_chips(self):
        counts = getattr(self, "grid_counts", {})
        for n, l in self.grid_chips.items():
            on = n == self.grid_filter.get()
            base = dict(self.SECTIONS)[n]
            l.config(text=base + (f"  {counts[n]}" if n in counts else ""),
                     bg=({"BEASTS": "#c2410c", "Hall of Fame": "#a16207", "Eggs": "#15803d", "Rising": "#1d4ed8"}.get(n, ACCENT))
                     if on else CARD,
                     fg="white" if on else (("#f59e0b" if n in ("BEASTS", "Hall of Fame") else MUTED)))

    def _grid_items(self):
        """Cards to show: ('farm', row, tier) for live bots or ('hof', entry, in_farm) for Hall of Fame bots."""
        f = self.grid_filter.get()
        rows = self.tourn.board() if self.tourn else []
        tiers = [(r, self.tier(r)) for r in rows]
        hof = self.hof()
        self._hof_n = len(hof)
        self.grid_counts = {"Eggs": sum(1 for _, t in tiers if t == "egg"),
                            "Rising": sum(1 for _, t in tiers if t == "rising"),
                            "BEASTS": sum(1 for _, t in tiers if t == "beast"),
                            "Hall of Fame": getattr(self, "_hof_n", 0), "All": len(rows)}
        # promotions (egg -> rising -> BEAST) get a little animation
        rank = {"egg": 0, "rising": 1, "beast": 2}
        seen = self.__dict__.setdefault("tier_seen", {})
        promos = self.__dict__.setdefault("promos", {})
        for r, t in tiers:
            v = r["v"]
            old = seen.get(id(v))
            if old is not None and rank[t] > rank[old] and self.running and self.seeded:
                promos[id(v)] = ("roar" if t == "beast" else "hatch", getattr(self, "farm_phase", 0))
            seen[id(v)] = t
        # new BEASTS get announced
        for r, t in tiers:
            k = r["v"].key()
            if t == "beast" and k not in self.beasts_seen and self.running and self.seeded:
                self.beasts_seen.add(k)
                msg = (f"{E.bot_nickname(k)} became a BEAST! ({friendly_label(r['name'], r['params'], r.get('tf', 1), self.session_bar)}, "
                       f"{pct_txt(r['score'])} recently, {pct_txt(r.get('edge'))} vs holding)")
                self.write("\u265b " + msg, "good")
                self.notify("CBBF", msg, sound="hof")
        q = self.grid_search.get().strip().lower() if hasattr(self, "grid_search") else ""
        if q:
            tiers = [(r, t) for r, t in tiers if q in self._search_text(r)]
        if f == "Hall of Fame":
            live = {r["v"].key(): r for r in rows}
            return [("hof", b, live.get(E.bot_key(b))) for b in hof
                    if not q or q in (E.bot_nickname(E.bot_key(b)) + " " + b["name"] + " "
                                      + STRAT_LABELS.get(b["name"], "")).lower()]
        if f == "Caught":
            have = self.library_keys()
            return [("farm", r, t) for r, t in tiers if r["v"].key() in have]
        want = {"Eggs": "egg", "Rising": "rising", "BEASTS": "beast"}.get(f)
        items = [("farm", r, t) for r, t in tiers if want is None or t == want]
        if f == "Rising":  # resting ones last
            items.sort(key=lambda it: it[1]["status"] == "benched")
        if f == "All":
            order = {"beast": 0, "rising": 1, "egg": 2}
            items.sort(key=lambda it: order[it[2]])
        return items

    def _search_text(self, r):
        v = r["v"]
        L = v.layers or {}
        studies = " ".join(sd["name"] + " " + STRAT_LABELS.get(sd["name"], "") for k in ("plus", "minus", "exits")
                           for sd in L.get(k, []))
        return " ".join((E.bot_nickname(v.key()), v.name, STRAT_LABELS.get(v.name, ""), E.traits_text(v.traits),
                         E.vetoes_text(v.vetoes), E.risk_text(v.risk), v.classic or "", studies, v.origin or "",
                         STATUS_TEXT.get(r["status"], ""), f"#{v.uid}")).lower()

    # ---- drag a card onto a den tab: Hall of Fame (promote) or Library (catch) ----
    def _grid_drag(self, e):
        card = self._grid_at(e) if not getattr(self, "_drag", None) else None
        if card and card[4] is not None and not getattr(self, "_drag", None):
            self._drag_start = getattr(self, "_drag_start", None) or (e.x, e.y, card[4])
        st = getattr(self, "_drag_start", None)
        if st and not getattr(self, "_drag", None) and abs(e.x - st[0]) + abs(e.y - st[1]) > 14:
            self._drag = st[2]
            self.grid.config(cursor="fleur")
            for k in ("Hall of Fame", "Caught"):
                self.grid_chips[k].config(bg="#16a34a", fg="white")
        if getattr(self, "_drag", None):
            g = getattr(self, "_drag_ghost", None)
            if g is None:
                g = self._drag_ghost = tk.Toplevel(self.root)
                g.overrideredirect(True)
                g.attributes("-topmost", True)
                tk.Label(g, text="\u265b " + E.bot_nickname(self._drag.key()) + "  \u2192 drop on Hall of Fame or Library",
                         bg=ACCENT, fg="white", padx=8, pady=3, font=("", 9, "bold")).pack()
            g.geometry(f"+{e.x_root + 14}+{e.y_root + 10}")

    def _grid_drop(self, e):
        v = getattr(self, "_drag", None)
        self._drag = self._drag_start = None
        g = getattr(self, "_drag_ghost", None)
        if g is not None:
            g.destroy()
            self._drag_ghost = None
        if v is None:
            return
        self.grid.config(cursor="")
        self._grid_paint_chips()
        target = self.root.winfo_containing(e.x_root, e.y_root)
        if target is self.grid_chips.get("Hall of Fame"):
            self.promote_variants([v])
        elif target is self.grid_chips.get("Caught"):
            rows = [dict(r, bar_minutes=self.session_bar) for r in self.tourn.board() if r["v"] is v]
            self.catch_rows(E.export_bots(rows, self.version, note="caught by you")["bots"], "caught")
            self.refresh_board()

    @staticmethod
    def round_rect(c, x0, y0, x1, y1, r=12, **kw):
        pts = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
               x0, y1 - r, x0, y0 + r, x0, y0)
        return c.create_polygon(*pts, smooth=True, **kw)

    def _bar(self, c, x, y, w, name, frac, col):
        c.create_text(x, y + 4, anchor="w", text=name, fill=MUTED, font=("", 7, "bold"), tags="card")
        self.round_rect(c, x + 20, y, x + w, y + 8, 4, fill=LINE, outline="", tags="card")
        if frac > 0.02:
            self.round_rect(c, x + 20, y, x + 20 + (w - 20) * min(frac, 1), y + 8, 4, fill=col, outline="", tags="card")

    def _chips(self, c, x, y, xmax, chips):
        cx = x
        for txt, tc in chips:
            t = c.create_text(cx + 5, y, anchor="w", text=txt, fill=tc, font=("", 7, "bold"), tags="card")
            bb = c.bbox(t)
            if bb[2] > xmax:
                c.delete(t)
                break
            bgc = self.round_rect(c, bb[0] - 5, bb[1] - 2, bb[2] + 5, bb[3] + 2, 6, fill=BG, outline="", tags="card")
            c.tag_lower(bgc, t)
            cx = bb[2] + 9

    def grid_refresh(self):
        """Draws the cards (the still parts). Dragons and eggs are animated separately by grid_animate."""
        if not hasattr(self, "grid"):
            return
        if self.moving():  # resizing / dragging the window: draw once it settles
            return self.later(self.grid_refresh, 250)
        c = self.grid
        W = max(c.winfo_width(), 200)
        items = self._grid_items()
        self._grid_paint_chips()
        c.delete("card")
        self.grid_cards = []
        awake = self.running and self.seeded
        gc = self.grid_counts
        self.grid_sum.config(text=(f"\u25d2 {gc['Eggs']} eggs   \u25b2 {gc['Rising']} rising   "
                                   f"\u265b {gc['BEASTS']} BEASTS   \u2605 {gc['Hall of Fame']} in the Hall of Fame"
                                   + (f"   |   generation {max(v.gen for v in self.tourn.vars)}" if self.tourn and self.tourn.vars else "")
                                   + ("" if awake or not self.tourn else "   |   the farm is asleep (press Start)")
                                   + "   |   goal: make money after fees, and more than just holding"))
        try:
            self.pulse_lbl.config(text=self.pulse_text())
        except Exception:
            pass
        f = self.grid_filter.get()
        style = self.farm_style.get()
        try:
            if "Bot Farm" not in self.nb.tab(self.nb.select(), "text"):
                return  # not visible: drawn when you open the tab
        except Exception:
            pass
        if style == "List" and self.grid_list is not None:
            self._fill_grid_list(items)
            return
        if not items:
            msgs = {"Eggs": "No eggs right now. New bots appear here while they learn.",
                    "Rising": "Nobody in the middle right now.",
                    "BEASTS": "No BEASTS yet. A bot becomes one when it keeps winning steadily and beats just holding.\n"
                              "It can take a while, and that's the point: they have to earn it.",
                    "Hall of Fame": "The Hall of Fame is empty so far. Bots that stay winners for hours are saved here.",
                    "Caught": "None of the bots on the farm are in your collection yet. Select some and press Catch!"}
            msg = msgs.get(f) if self.tourn or f == "Hall of Fame" else (
                "Your dragons hatch here when the bot runs with 'Let the bot choose (Wildcard)' ticked.\n"
                "Press Start the bot. Each dragon is one trading bot.")
            c.create_text(W // 2, 90, text=msg or "", fill=MUTED, justify="center", tags="card", font=("", 10))
            c.configure(scrollregion=(0, 0, W, 200))
            return
        big = f == "BEASTS"
        cw, ch, g = (300, 180, 14) if big else (236, 160, 12)
        per = max(1, (W - g) // (cw + g))
        caught = self.library_keys()
        hofk = self.hof_keys()
        sel = set(self.board.selection())
        gold = "#f59e0b"
        vtop, vbot = c.canvasy(0), c.canvasy(max(c.winfo_height(), 300))
        self._grid_drawn = (vtop - (ch + g) * 2, vbot + (ch + g) * 2)  # drawn band (cards outside it are skipped)
        for i, (kind, data, extra) in enumerate(items):
            x = g + (i % per) * (cw + g)
            y = g + (i // per) * (ch + g)
            if y + ch < self._grid_drawn[0] or y > self._grid_drawn[1]:
                continue  # off screen: drawn when you scroll to it
            if kind == "hof":
                self._hof_card(c, x, y, cw, ch, data, extra)
                self.grid_cards.append((x, y, x + cw, y + ch, extra["v"] if extra else None, "hof", "hof", data["name"]))
                continue
            r, t = data, extra
            v, lf = r["v"], r["life"]
            label, col = self.GRID_STATUS.get(r["status"], ("?", MUTED))
            if t == "egg":
                label, col = f"EGG {int(min(1, len(v.eq) / max(1, self.tourn.W)) * 100)}%", "#8b5cf6"
            elif t == "beast":
                label, col = "\u265b BEAST", "#dc2626"
            if not awake:
                label, col = "ASLEEP", "#6b7280"
            rec = E.lifetime_record(lf, self.session_bar)
            rar = E.rarity_for(rec)
            rc = gold if t == "beast" else E.RARITY_COLORS[rar]
            xp = E.xp_for(rec)
            lvl = E.level_for(xp)
            lo_xp, hi_xp = 10 * (lvl - 1) ** 2, 10 * lvl ** 2
            is_sel = str(id(v)) in sel
            den, glow = self.DEN_COLORS[t]
            self.round_rect(c, x - 4, y - 4, x + cw + 4, y + ch + 4, 18, fill="", outline=glow,
                            width=4 if t == "beast" else 2, tags="card")  # den glow: green eggs, blue rising, orange BEASTS
            self.round_rect(c, x, y, x + cw, y + ch, 14, fill=CARD, outline=TEXT if is_sel else rc,
                            width=3 if (is_sel or t == "beast") else (2 if rar != "Common" else 1), tags="card")
            port = 92 if big else 76
            self.round_rect(c, x + 3, y + 3, x + port, y + port, 10, fill=self.DEN_PORTRAIT[t] if DARK else BG, outline="", tags="card")
            c.create_line(x + 16, y + 1, x + cw - 16, y + 1, fill=den, width=3, capstyle="round", tags="card")
            c.create_rectangle(x + cw - 92, y + 10, x + cw - 10, y + 28, fill=col, outline="", tags="card")
            c.create_text(x + cw - 51, y + 19, text=label, fill="white", font=("", 8, "bold"), tags="card")
            nick = E.bot_nickname(v.key())
            if style != "Dragons":  # plain badge instead of an animated dragon
                pc = col if awake else "#6b7280"
                c.create_oval(x + 14, y + 14, x + port - 10, y + port - 10, fill=pc, outline="", tags="card")
                c.create_text((x + 2 + port) / 2, (y + 2 + port) / 2, tags="card", fill="white", font=("", 13, "bold"),
                              text="".join(w[0] for w in nick.split()[:2]) if t != "egg" else "...")
            star = "★ " if v.key() in hofk else ""
            ball = " ◉" if v.key() in caught else ""
            tx = x + port + 8
            c.create_text(tx, y + 40, anchor="w", text=star + nick + ball, fill=gold if t == "beast" else TEXT,
                          font=("", 12 if big else 10, "bold"), tags="card")
            c.create_text(tx, y + 58, anchor="w", text=STRAT_LABELS.get(v.name, v.name), fill=MUTED, font=("", 8), tags="card")
            sub = "hatching..." if t == "egg" else f"every {E.fmt_bar(v.tf * self.session_bar)}  |  {rar}"
            if v.classic:
                sub = f"\u272a Classic: {v.classic}" + ("  \u26a0" if any(cl.get("controversial") and cl["title"] == v.classic
                                                                        for cl in E.CLASSICS) else "")
            c.create_text(tx, y + 72, anchor="w", text=sub, fill=rc, font=("", 8, "bold"), tags="card")
            c.create_text(x + 10, y + 15, anchor="w", text=f"LV {lvl}", fill=rc, font=("", 9, "bold"), tags="card")
            by = y + (port + 12)
            if t == "egg":
                prog = min(1, len(v.eq) / max(1, self.tourn.W))
                self._bar(c, x + 10, by, cw - 10, "HATCH", prog, "#8b5cf6")
                self._bar(c, x + 10, by + 13, cw - 10, "TRADES", min(1, len(v.sim.tbars) / max(1, self.tourn.min_trades)), "#a78bfa")
            else:
                hp = r.get("health", 1.0)
                self._bar(c, x + 10, by, cw - 10, "XP", (xp - lo_xp) / max(1, hi_xp - lo_xp), "#a855f7")
                self._bar(c, x + 10, by + 13, cw - 10, "HP", hp, GREEN if hp > 0.5 else "#f59e0b" if hp > 0.2 else RED)
                self._bar(c, x + 10, by + 26, cw - 10, "PWR", r.get("power", 0) / 10, "#f97316")
            chips = [(pct_txt(r["score"]), GREEN if (r["score"] or 0) > 0 else RED if r["score"] else MUTED),
                     ("vs hold " + pct_txt(r.get("edge")), GREEN if (r.get("edge") or 0) > 0 else MUTED),
                     ("WR " + ("-" if lf["winrate"] is None else f"{lf['winrate'] * 100:.0f}%"), TEXT),
                     (f"{lf['trades']} trades", MUTED)]
            if t == "beast":
                chips.insert(0, (f"{r['stake']:.1f}x size", gold))
            if r.get("layers"):
                L_ = r["layers"]
                bits = [f"+{1 + len(L_.get('plus', []))}" if L_.get("plus") else "", f"-{len(L_['minus'])}" if L_.get("minus") else "",
                        f"x{len(L_['exits'])}" if L_.get("exits") else ""]
                if any(bits):
                    chips.append(("studies " + " ".join(b for b in bits if b), "#a78bfa"))
            badges = self.achievements(v, lf)
            if badges:
                chips.insert(0, (f"\u2726 {len(badges)}", "#facc15"))
            if self.tourn and int(self.tourn.o.get("islands", 1) or 1) > 1:
                chips.append((f"isle {v.island + 1}", MUTED))
            if r.get("vetoes"):
                good = sum(1 for k, s in (r.get("veto_stats") or {}).items() if s[0] >= 3 and s[1] > 0)
                chips.append((f"{len(r['vetoes'])} never-rule{'s' if len(r['vetoes']) != 1 else ''}"
                              + (f", {good} proven" if good else ""), "#38bdf8"))
            self._chips(c, x + 10, y + ch - 17, x + cw - 8, chips)
            self.grid_cards.append((x, y, x + cw, y + ch, v, r["status"], t, v.name))
        nrows = (len(items) + per - 1) // per
        c.configure(scrollregion=(0, 0, W, nrows * (ch + g) + g))
        c.tag_raise("anim")  # keep the animated layer on top so nothing flickers when the cards are redrawn

    def _hof_card(self, c, x, y, cw, ch, b, live):
        gold = "#f59e0b"
        if self.farm_style.get() != "Dragons":
            c.create_oval(x + 14, y + 14, x + 66, y + 66, fill="#a16207", outline="", tags="card")
            c.create_text(x + 40, y + 40, text="\u2605", fill="white", font=("", 18, "bold"), tags="card")
        lt = b.get("lifetime") or {}
        self.round_rect(c, x - 3, y - 3, x + cw + 3, y + ch + 3, 17, fill="", outline="#713f12", width=3, tags="card")
        self.round_rect(c, x, y, x + cw, y + ch, 14, fill=CARD, outline=gold, width=2, tags="card")
        self.round_rect(c, x + 3, y + 3, x + 76, y + 76, 10, fill="#2a2006", outline="", tags="card")
        pill, pc = ("ON THE FARM", "#16a34a") if live else ("RESTING", "#a16207")
        rt = b.get("retest") or {}
        if rt.get("rusty"):
            pill, pc = "RUSTY", "#78350f"
        ok, _miss = self.graduated(b)
        c.create_text(x + cw - 10, y + ch - 12, anchor="e", text="\u2713 graduated" if ok else "not graduated",
                      fill=GREEN if ok else MUTED, font=("", 7, "bold"), tags="card")
        c.create_rectangle(x + cw - 92, y + 10, x + cw - 10, y + 28, fill=pc, outline="", tags="card")
        c.create_text(x + cw - 51, y + 19, text=pill, fill="white", font=("", 8, "bold"), tags="card")
        c.create_text(x + 84, y + 40, anchor="w", text="\u2605 " + E.bot_nickname(E.bot_key(b)), fill=gold,
                      font=("", 10, "bold"), tags="card")
        c.create_text(x + 84, y + 57, anchor="w", text=STRAT_LABELS.get(b["name"], b["name"]), fill=MUTED, font=("", 8), tags="card")
        when = str(b.get("inducted_at", ""))[:10]
        c.create_text(x + 84, y + 71, anchor="w", text=f"since {when}" if when else "champion", fill=gold, font=("", 8, "bold"), tags="card")

        def p(k):
            v = lt.get(k)
            return "-" if v is None else f"{v:+.1f}%"
        c.create_text(x + 10, y + 92, anchor="w", fill=TEXT, font=("", 8), tags="card",
                      text=f"Lifetime {p('return_pct')}   vs holding {p('vs_holding_pct')}")
        hrs = lt.get("hours_winning")
        c.create_text(x + 10, y + 108, anchor="w", fill=MUTED, font=("", 8), tags="card",
                      text=(f"won for {hrs:.1f} h" if hrs is not None else lt.get("source", "")) +
                           (f"   |   win rate {lt['win_rate_pct']:.0f}%" if lt.get("win_rate_pct") is not None else ""))
        extras = ", ".join(x for x in (E.risk_text(b.get("risk")), E.traits_text(b.get("traits"))) if x)
        if extras:
            c.create_text(x + 10, y + 124, anchor="w", fill=MUTED, font=("", 7), tags="card", text=extras[:48])

    def egg_color(self, v):
        if v.royal:
            return "#f59e0b"  # royal egg: descends from bots you saved
        if v.classic:
            return "#94a3b8"  # silver egg: a famous strategy
        if self.settings.get("egg_colors", "By dragon type") == "Purple":
            return "#8b5cf6"
        th = theme_for(v.name)
        return THEMES[th]["body"] if th else "#8b5cf6"

    def _grid_anim_frame(self):
        c = self.grid
        c.delete("anim")
        if not c.winfo_ismapped() or not self.grid_cards or self.farm_style.get() != "Dragons":
            return
        ph = getattr(self, "farm_phase", 0)
        top, bottom = c.canvasy(0), c.canvasy(c.winfo_height())
        awake = self.running and self.seeded
        big = self.grid_filter.get() == "BEASTS"
        for i, (x0, y0, x1, y1, v, st, t, strat) in enumerate(self.grid_cards):
            if y1 < top or y0 > bottom:
                continue  # off screen: skip
            if t == "hof":
                draw_creature(c, strat, x0 + 14, y0 + 22 + math.sin(ph / 3) * 1.5, 50, "#eab308", awake=True,
                            phase=ph // 3, mood="happy", gold=True, crown=True, tags="anim")
                continue
            if t == "egg":
                prog = min(1, len(v.eq) / max(1, self.tourn.W)) if self.tourn else 0
                draw_egg(c, x0 + 20, y0 + 12, 40, self.egg_color(v), prog, ph if awake else 0, tags="anim",
                         royal=v.royal, classic=bool(v.classic))
                continue
            label, col = self.GRID_STATUS.get(st, ("?", MUTED))
            working = awake and st != "benched"
            just = awake and bool(v.log) and v.log[-1][0] == (self.tourn.n if self.tourn else -1)
            bob = 2 * math.sin(ph / 2.4) if working else 0
            if t == "beast":
                fire = working and (just or ph % 30 < 4)
                draw_creature(c, strat, x0 + 12, y0 + 22 + bob, 64 if big else 50, "#dc2626" if awake else "#6b7280", status=st, awake=working,
                            phase=ph, mood="happy", fire=fire, flash=just and ph % 2 == 0, crown=True, tags="anim")
            else:
                draw_creature(c, strat, x0 + 14, y0 + 22 + bob, 50, col if awake else "#6b7280", status=st, awake=working, phase=ph,
                            mood="happy" if st == "active" else "plain", fire=just and v.log[-1][2] == "buy",
                            flash=just and ph % 2 == 0, tags="anim")
            if not working:
                k = ph % 18
                if k < 12:
                    c.create_text(x0 + 66 + k / 3, y0 + 26 - k, text="z" if k < 6 else "Z", fill=MUTED,
                                  font=("", 8 + k // 6, "bold"), tags="anim")
            elif just:
                side = v.log[-1][2]
                c.create_text(x0 + 66, y0 + 24, text="BUY!" if side == "buy" else "SELL!", tags="anim",
                              fill=GREEN if side == "buy" else "#f59e0b", font=("", 8, "bold"))
        promos = getattr(self, "promos", {})
        if promos:
            for x0, y0, x1, y1, v, st, t, strat in self.grid_cards:
                if v is None or id(v) not in promos or y1 < top or y0 > bottom or t == "hof":
                    continue
                kind, start = promos[id(v)]
                age = ph - start
                if age > 28 or age < 0:
                    promos.pop(id(v), None)
                    continue
                cx, cy = x0 + 40, y0 + 42
                if kind == "hatch":  # shell shards fly out, "HATCHED!" floats up
                    col = self.egg_color(v)
                    for k in range(8):
                        a = k * math.pi / 4 + 0.3
                        d = age * 3
                        sx, sy = cx + math.cos(a) * d, cy + math.sin(a) * d + age * age * 0.06
                        c.create_polygon(sx, sy, sx + 6, sy + 2, sx + 2, sy + 7, fill=col, outline="white", tags="anim")
                    self._promo_text(c, (x0 + x1) / 2, y0 + 14 - min(age, 10) * 0.6, "HATCHED!", "#22c55e")
                else:  # roar: fiery rings burst out of the dragon
                    for k in range(3):
                        r_ = min(40, (age - k * 4) * 3)
                        if 0 < r_ < 40:
                            c.create_oval(cx - r_, cy - r_, cx + r_, cy + r_, outline=("#f97316", "#dc2626", "#facc15")[k],
                                          width=3, tags="anim")
                    self._promo_text(c, (x0 + x1) / 2, y0 + 14, "ROAR!", "#f97316", 11 + min(age, 6))
        sel = set(self.board.selection())
        for x0, y0, x1, y1, v, st, t, strat in self.grid_cards:
            if v is not None and str(id(v)) in sel and not (y1 < top or y0 > bottom):
                self.round_rect(c, x0 - 3, y0 - 3, x1 + 3, y1 + 3, 16, fill="", outline=TEXT, width=3, tags="anim")
        hv = self.grid_hover
        if hv is not None:
            x0, y0, x1, y1 = hv
            self.round_rect(c, x0 - 2, y0 - 2, x1 + 2, y1 + 2, 15, fill="", outline=ACCENT, width=2, tags="anim")

    def _promo_text(self, c, x, y, text, color, size=11):
        t = c.create_text(x, y, text=text, fill=color, font=("", size, "bold"), tags="anim")
        bb = c.bbox(t)
        if bb:
            bg = self.round_rect(c, bb[0] - 8, bb[1] - 3, bb[2] + 8, bb[3] + 3, 8, fill=BG, outline=color, width=2, tags="anim")
            c.tag_lower(bg, t)

    def _grid_at(self, e):
        x, y = self.grid.canvasx(e.x), self.grid.canvasy(e.y)
        for card in self.grid_cards:
            if card[0] <= x <= card[2] and card[1] <= y <= card[3]:
                return card
        return None

    def _grid_motion(self, e):
        card = self._grid_at(e)
        self.grid_hover = card[:4] if card else None

    def _grid_click(self, e):
        card = self._grid_at(e)
        if not card or card[4] is None:
            return
        iid = str(id(card[4]))
        if self.board.exists(iid):
            if e.state & 0x0004 or e.state & 0x0001:  # ctrl/shift: several (for Catch! or Export)
                cur = set(self.board.selection())
                cur ^= {iid}
                self.board.selection_set(list(cur))
            else:
                self.board.selection_set(iid)
        self.soon(self._after_select)

    def later(self, fn, ms=150):
        """Runs fn once, ms after the LAST time it was asked for (resizing fires hundreds of events: draw once at the end)."""
        pend = self.__dict__.setdefault("_later", {})
        old = pend.pop(fn.__name__, None)
        if old:
            try:
                self.root.after_cancel(old)
            except Exception:
                pass
        pend[fn.__name__] = self.root.after(ms, lambda: (pend.pop(fn.__name__, None), fn()))

    def soon(self, fn, ms=40):
        """Runs fn once a moment from now, however many times it is asked for meanwhile (keeps clicks snappy)."""
        pend = self.__dict__.setdefault("_soon", {})
        if fn.__name__ in pend:
            return
        pend[fn.__name__] = self.root.after(ms, lambda: (pend.pop(fn.__name__, None), fn()))

    def _after_select(self):
        self.draw_chart()
        if self.farm_style.get() != "Dragons":  # dragons show the selection on their own animated layer
            self.grid_refresh()

    # ---------------- custom egg designer ----------------
    def edit_rules(self, v=None, entry=None):
        """See and change a bot's learned rules. Farm bots: hatch the edited version as its child, or replace it.
        Library bots: save the edited version as a new Library bot."""
        if v is not None:
            g = dict(name=v.name, params=dict(v.params), tf=v.tf, risk=dict(v.risk), traits=dict(v.traits),
                     vetoes=dict(v.vetoes), layers=json.loads(json.dumps(v.layers)), size=v.size_mult,
                     layer_stats=v.layer_stats, veto_stats=v.veto_stats)
            self.open_designer(g, edit=v)
        elif entry is not None:
            self.open_designer(dict(entry), edit=entry)

    def open_designer(self, preset=None, edit=None):
        """Design a bot by hand: strategy and its numbers, pace, own exits and traits. Hatch it on the farm
        (a royal egg) and/or save it to the Library."""
        w = tk.Toplevel(self.root)
        editing = edit is not None
        ed_name = (E.bot_nickname(edit.key()) if hasattr(edit, "key") else
                   (edit.get("nickname") or E.bot_nickname(E.bot_key(edit)))) if editing else ""
        w.title(f"Edit rules: {ed_name}" if editing else "Design an egg")
        w.geometry(f"900x{min(860, self.root.winfo_screenheight() - 120)}")
        sf = ScrollFrame(w, BG, width=880)  # tall form: it scrolls on small screens
        sf.pack(fill="both", expand=True)
        f = ttk.Frame(sf.inner, padding=14)
        f.pack()
        ttk.Label(f, text=f"Edit rules: {ed_name}" if editing else "Design an egg", font=("", 13, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w")
        if editing:
            learned = []
            for title_, txt in E.rule_stack(preset):
                learned.append(f"{title_}: {txt}")
            ttk.Label(f, foreground=MUTED, wraplength=820, justify="left", text=(
                "What it has learned so far (with the evidence for each rule):\n" + "\n".join(learned) +
                "\n\nChange anything below. Its own record stays as it earned it: the edited version hatches as its child "
                "(or replaces it), keeps its bloodline, and has to prove itself.")).grid(row=1, column=0, columnspan=4,
                                                                                      sticky="w", pady=(2, 10))
        else:
            ttk.Label(f, foreground=MUTED, wraplength=560, text=(
                "Write your own rules: pick a strategy and its numbers (for example RSI: buy below 28, sell above 72), how "
                "often it decides, its own exits and habits. It hatches as a royal egg and competes like any other bot, "
                "and its children keep your bloodline.")).grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 10))
        names = [n for n in E.WILDCARD_STRATS]
        labels = {STRAT_LABELS.get(n, n): n for n in names}
        strat = tk.StringVar(value=STRAT_LABELS.get((preset or {}).get("name", "RSI"), "Buy dips / sell peaks"))
        ttk.Label(f, text="Strategy").grid(row=2, column=0, sticky="w")
        sb = ttk.Combobox(f, textvariable=strat, values=list(labels), state="readonly", width=28)
        sb.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
        tip = ttk.Label(f, foreground=MUTED, wraplength=560, justify="left")
        tip.grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 6))
        pbox = ttk.LabelFrame(f, text=" Its numbers ", padding=8)
        pbox.grid(row=4, column=0, columnspan=4, sticky="ew")
        pvars = {}

        def build_params(*_):
            for ch in pbox.winfo_children():
                ch.destroy()
            pvars.clear()
            n = labels[strat.get()]
            tip.config(text=STRAT_TIPS.get(n, ""))
            given = (preset or {}).get("params", {}) if (preset or {}).get("name") == n else {}
            for r, (k, dv) in enumerate(E.STRATS[n]["params"].items()):
                lo, hi = E.STRATS[n]["bounds"][k]
                ttk.Label(pbox, text=PARAM_NAMES.get(k, k)).grid(row=r, column=0, sticky="w")
                var = tk.StringVar(value=str(given.get(k, dv)))
                ttk.Entry(pbox, textvariable=var, width=8).grid(row=r, column=1, padx=6)
                ttk.Label(pbox, text=f"{lo:g} to {hi:g}", foreground=MUTED).grid(row=r, column=2, sticky="w")
                pvars[k] = (var, type(dv), lo, hi)
        sb.bind("<<ComboboxSelected>>", build_params)
        build_params()
        row = 5
        ttk.Label(f, text="Decides every").grid(row=row, column=0, sticky="w", pady=(8, 0))
        paces = {E.fmt_bar(tf * self.session_bar) + (f"  ({tf} bars)" if tf > 1 else "  (every bar)"): tf for tf in E.TIMEFRAMES}
        pace = tk.StringVar(value=next(k for k, v in paces.items() if v == E.clamp_tf((preset or {}).get("tf", 1))))
        ttk.Combobox(f, textvariable=pace, values=list(paces), state="readonly", width=22).grid(
            row=row, column=1, columnspan=2, sticky="w", padx=6, pady=(8, 0))
        row += 1
        risk = (preset or {}).get("risk") or {}
        sl, tp = tk.StringVar(value=str(risk.get("sl", 0) or "")), tk.StringVar(value=str(risk.get("tp", 0) or ""))
        ttk.Label(f, text="Own safety stop %").grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(f, textvariable=sl, width=8).grid(row=row, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(f, text="Own profit target %").grid(row=row, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(f, textvariable=tp, width=8).grid(row=row, column=3, sticky="w", padx=6, pady=(6, 0))
        row += 1
        tbox = ttk.LabelFrame(f, text=" Habits (traits), tick to use ", padding=8)
        tbox.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        given_t = (preset or {}).get("traits") or {}
        tvars = {}
        for r, k in enumerate(k for k in E.TRAITS if k not in ("exit", "moods")):
            spec = E.TRAITS[k]
            on = tk.BooleanVar(value=k in given_t)
            start = spec["lo"] + (spec["hi"] - spec["lo"]) / 4  # a sensible starting value inside the allowed range
            val = tk.StringVar(value=str(given_t.get(k, round(start, 2) if spec["kind"] is float else int(round(start)))))
            ttk.Checkbutton(tbox, text=E.TRAIT_NAMES.get(k, k), variable=on).grid(row=r // 2, column=(r % 2) * 3, sticky="w")
            ttk.Entry(tbox, textvariable=val, width=6).grid(row=r // 2, column=(r % 2) * 3 + 1, padx=4)
            ttk.Label(tbox, text=f"{spec['lo']:g}-{spec['hi']:g}", foreground=MUTED).grid(row=r // 2, column=(r % 2) * 3 + 2,
                                                                                          sticky="w", padx=(0, 12))
            tvars[k] = (on, val)
        rr = (len(tvars) + 1) // 2
        exit_only = tk.BooleanVar(value=bool(given_t.get("exit")))
        ttk.Checkbutton(tbox, text="Sell only through its own exits (stop / target / trailing / max hold)",
                        variable=exit_only).grid(row=rr, column=0, columnspan=6, sticky="w", pady=(4, 0))
        mood_vars = []
        mrow = ttk.Frame(tbox)
        mrow.grid(row=rr + 1, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Label(mrow, text="Only trade when the market is:").pack(side="left")
        gm = int(given_t.get("moods") or 0)
        for i, m in enumerate(E.MOODS):
            var = tk.BooleanVar(value=bool(gm & (1 << i)) if gm else True)
            ttk.Checkbutton(mrow, text=m, variable=var).pack(side="left", padx=(6, 0))
            mood_vars.append(var)
        row += 1
        lbox = ttk.LabelFrame(f, text=" Studies: several reasons to buy, several reasons not to (optional) ", padding=8)
        lbox.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        row += 1
        gl = E.clean_layers((preset or {}).get("layers") or {})
        fams = [STRAT_LABELS.get(n, n) for n in E.WILDCARD_STRATS]
        to_name = {STRAT_LABELS.get(n, n): n for n in E.WILDCARD_STRATS}
        lists = {}
        for col, (key, title, cap) in enumerate((("plus", "Positive studies (also say buy)", E.MAX_PLUS),
                                                 ("minus", "Negative studies (a sell counts against)", E.MAX_MINUS),
                                                 ("exits", "Exit studies (any sell = sell)", E.MAX_EXITS))):
            ttk.Label(lbox, text=f"{title}, up to {cap}").grid(row=0, column=col, sticky="w", padx=(0, 10))
            lb = tk.Listbox(lbox, selectmode="multiple", height=7, exportselection=False, width=26,
                            bg=CARD, fg=TEXT, selectbackground=ACCENT, highlightthickness=0)
            for fam in fams:
                lb.insert("end", fam)
            for sub in gl.get(key, []):
                lbl = STRAT_LABELS.get(sub["name"], sub["name"])
                if lbl in fams:
                    lb.selection_set(fams.index(lbl))
            lb.grid(row=1, column=col, sticky="w", padx=(0, 10))
            lists[key] = (lb, cap)
        need_v = tk.StringVar(value=str(gl.get("need", 1)))
        nrow = ttk.Frame(lbox)
        nrow.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(nrow, text="Buy when at least").pack(side="left")
        ttk.Spinbox(nrow, from_=1, to=1 + E.MAX_PLUS, width=3, textvariable=need_v).pack(side="left", padx=4)
        ttk.Label(nrow, text="positive studies (the main strategy counts as one) say buy, minus one for each negative "
                             "study saying sell.", foreground=MUTED).pack(side="left")
        size_on = tk.BooleanVar(value=bool(gl.get("size")))
        size_t = tk.StringVar(value=str((gl.get("size") or {}).get("target", 0.5)))
        srow = ttk.Frame(lbox)
        srow.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(srow, text="Size trades to the market's swing, aiming for this % move per decision:",
                        variable=size_on).pack(side="left")
        ttk.Entry(srow, textvariable=size_t, width=6).pack(side="left", padx=4)
        hrow = ttk.Frame(lbox)
        hrow.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(hrow, text="Bigger trend must agree:").pack(side="left")
        htf_v = tk.StringVar(value=STRAT_LABELS.get(gl["htf"]["name"], gl["htf"]["name"]) if gl.get("htf") else "(none)")
        ttk.Combobox(hrow, textvariable=htf_v, values=["(none)"] + fams, state="readonly", width=24).pack(side="left", padx=4)
        ttk.Label(hrow, text="on candles").pack(side="left")
        htf_m = tk.StringVar(value=f"{gl['htf']['mult']}x" if gl.get("htf") else "6x")
        ttk.Combobox(hrow, textvariable=htf_m, values=[f"{m}x" for m in E.HTF_MULTS], state="readonly", width=4).pack(side="left", padx=4)
        ttk.Label(hrow, text="longer than its own", foreground=MUTED).pack(side="left")
        lrow = ttk.Frame(lbox)
        lrow.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        lead_on = tk.BooleanVar(value=bool(gl.get("lead")))
        ttk.Checkbutton(lrow, text="Bitcoin leads: BTC moving", variable=lead_on).pack(side="left")
        lead_th = tk.StringVar(value=str((gl.get("lead") or {}).get("th", 0.8)))
        ttk.Entry(lrow, textvariable=lead_th, width=5).pack(side="left", padx=4)
        ttk.Label(lrow, text="% over").pack(side="left")
        lead_lb = tk.StringVar(value=str((gl.get("lead") or {}).get("lb", 10)))
        ttk.Entry(lrow, textvariable=lead_lb, width=4).pack(side="left", padx=4)
        ttk.Label(lrow, text="decisions votes for (up) or against (down) buying other coins", foreground=MUTED).pack(side="left")
        nbox = ttk.LabelFrame(f, text=f" Never-rules (up to {E.MAX_VETOES}), tick to use ", padding=8)
        nbox.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        given_v = (preset or {}).get("vetoes") or {}
        vvars = {}
        for r_, (k, spec) in enumerate(E.VETOES.items()):
            on = tk.BooleanVar(value=k in given_v)
            start = given_v.get(k, spec["lo"] + (spec["hi"] - spec["lo"]) / 3)
            val = tk.StringVar(value=str(int(start) if k == "revenge" else round(start, 2)))
            ttk.Checkbutton(nbox, text=spec["name"], variable=on).grid(row=r_ // 2, column=(r_ % 2) * 3, sticky="w")
            ttk.Entry(nbox, textvariable=val, width=6).grid(row=r_ // 2, column=(r_ % 2) * 3 + 1, padx=4)
            ttk.Label(nbox, text=f"{spec['lo']:g}-{spec['hi']:g}", foreground=MUTED).grid(row=r_ // 2, column=(r_ % 2) * 3 + 2,
                                                                                          sticky="w", padx=(0, 12))
            vvars[k] = (on, val)
        ttk.Label(nbox, foreground=MUTED, wraplength=540, text=(
            "Each never-rule blocks some trades. The farm checks what every blocked trade would have done and keeps "
            "score: rules that save money are passed on to children, rules that keep blocking winners die out.")).grid(
            row=(len(vvars) + 1) // 2, column=0, columnspan=6, sticky="w", pady=(4, 0))
        size = tk.StringVar(value=str((preset or {}).get("size", 1.0)))
        row += 1
        ttk.Label(f, text="Trade size (x normal)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(f, textvariable=size, width=8).grid(row=row, column=1, sticky="w", padx=6, pady=(8, 0))

        def build():
            n = labels[strat.get()]
            try:
                params = {}
                for k, (var, typ, lo, hi) in pvars.items():
                    v = float(var.get())
                    if not lo <= v <= hi:
                        raise ValueError(f"{PARAM_NAMES.get(k, k)} must be {lo:g} to {hi:g}")
                    params[k] = int(round(v)) if typ is int else v
                if E.fix_params(n, dict(params)) != params:
                    raise ValueError("short averages must be below long ones, and buy levels well below sell levels")
                traits = {}
                for k, (on, val) in tvars.items():
                    if on.get():
                        traits[k] = float(val.get())
                if exit_only.get():
                    traits["exit"] = 1
                mask = sum(1 << i for i, v in enumerate(mood_vars) if v.get())
                if 0 < mask < 15:
                    traits["moods"] = mask
                vetoes = {k: float(val.get()) for k, (on, val) in vvars.items() if on.get()}
                if len(vetoes) > E.MAX_VETOES:
                    raise ValueError(f"pick at most {E.MAX_VETOES} never-rules")
                layers = {}
                for key, (lb, cap) in lists.items():
                    picks = [to_name[lb.get(i_)] for i_ in lb.curselection()]
                    if len(picks) > cap:
                        raise ValueError(f"pick at most {cap} {'positive' if key == 'plus' else 'negative' if key == 'minus' else 'exit'} studies")
                    if picks:  # a study it already had keeps the settings it learned
                        had = {sd["name"]: sd for sd in gl.get(key, [])}
                        layers[key] = [dict(name=pn, params=dict(had[pn]["params"] if pn in had else E.STRATS[pn]["params"]))
                                       for pn in picks]
                if layers.get("plus") or layers.get("minus"):
                    layers["need"] = int(float(need_v.get() or 1))
                if size_on.get():
                    layers["size"] = dict(target=float(size_t.get()))
                if htf_v.get() != "(none)":
                    hn = to_name[htf_v.get()]
                    layers["htf"] = dict(name=hn, params=dict(gl["htf"]["params"] if gl.get("htf") and gl["htf"]["name"] == hn
                                                             else E.STRATS[hn]["params"]), mult=int(htf_m.get().rstrip("x")))
                if lead_on.get():
                    layers["lead"] = dict(lb=int(float(lead_lb.get())), th=float(lead_th.get()))
                entry = dict(name=n, params=params, tf=paces[pace.get()], size=min(max(float(size.get() or 1), 0.25), 3.0),
                             risk=E.clean_risk({"sl": float(sl.get() or 0), "tp": float(tp.get() or 0)}),
                             traits=E.clean_traits(traits), vetoes=E.clean_vetoes(vetoes), layers=E.clean_layers(layers),
                             stats={}, notes="edited by you" if editing else "designed by you")
                return entry
            except ValueError as ex:
                messagebox.showerror("Design an egg", f"Please check the numbers: {ex}", parent=w)
                return None

        def hatch(save=False, farm=True):
            entry = build()
            if not entry:
                return
            if farm:
                if not self.tourn or not self.seeded:
                    return messagebox.showinfo("Design an egg", "Start the bot with Wildcard ticked first, or use "
                                                                "'Save to Library' to keep it for later.", parent=w)
                v = self.tourn.add_variant(entry["name"], entry["params"], {c: self.hist[c] for c in self.session_coins},
                                           entry["size"], note="Designed egg:", tf=entry["tf"], risk=entry["risk"], vetoes=entry["vetoes"],
                                           layers=entry["layers"],
                                           traits=entry["traits"], origin="designed")
                if v:
                    if len(self.tourn.vars) > self.tourn.cap:
                        self.tourn.cap = len(self.tourn.vars)
                    self.flush_events()
                    self.refresh_board()
                    self.write(f"♛ Your egg {E.bot_nickname(v.key())} is on the farm: {E.describe_bot(entry, self.session_bar)}", "good")
            if save:
                self.catch_rows([dict(entry, lifetime=dict(bar_minutes=self.session_bar, source="designed by you"))], "designed")
            w.destroy()
        def apply_edit(replace):
            entry = build()
            if not entry:
                return
            src = edit
            if not self.tourn or not self.seeded:
                return messagebox.showinfo("Edit rules", "Start the farm first, or use 'Save to Library'.", parent=w)
            v = self.tourn.add_variant(entry["name"], entry["params"], {c: self.hist[c] for c in self.session_coins},
                                       entry["size"], note="Edited by you:", tf=entry["tf"], risk=entry["risk"],
                                       traits=entry["traits"], vetoes=entry["vetoes"], layers=entry["layers"], origin="edited by you")
            if not v:
                return
            if hasattr(src, "uid"):  # keep the bloodline: the edited bot is the original's child, on its island
                v.parents, v.gen, v.island, v.royal = [src.uid], src.gen + 1, src.island, True
                rec = self.tourn.lineage.get(v.uid)
                if rec:
                    rec.update(parents=[src.uid], gen=v.gen)
                v.layer_stats = {k: [x[0] / 2, x[1] / 2] for k, x in src.layer_stats.items()}
                v.veto_stats = {k: [x[0] / 2, x[1] / 2] for k, x in src.veto_stats.items() if k in v.vetoes}
                if replace and src in self.tourn.vars:
                    self.tourn._remove(src, "replaced by your edit")
            if len(self.tourn.vars) > self.tourn.cap:
                self.tourn.cap = len(self.tourn.vars)
            self.flush_events()
            self.refresh_board()
            self.write(f"\u270e {'Replaced' if replace else 'Hatched an edited child of'} {ed_name}: "
                       f"{E.bot_nickname(v.key())} is on the farm and has to prove itself.", "good")
            w.destroy()
        b = ttk.Frame(f)
        b.grid(row=row + 1, column=0, columnspan=4, sticky="e", pady=(14, 0))
        ttk.Button(b, text="Cancel", command=w.destroy).pack(side="left", padx=4)
        ttk.Button(b, text="Save to Library", command=lambda: hatch(save=True, farm=False)).pack(side="left", padx=4)
        if editing and hasattr(edit, "uid"):
            ttk.Button(b, text="Replace this bot", command=lambda: apply_edit(True)).pack(side="left", padx=4)
            ttk.Button(b, text="Hatch as its child", style="Accent.TButton", command=lambda: apply_edit(False)).pack(side="left", padx=4)
        else:
            ttk.Button(b, text="Hatch & save", command=lambda: hatch(save=True)).pack(side="left", padx=4)
            ttk.Button(b, text="Hatch on the farm", style="Accent.TButton", command=lambda: hatch()).pack(side="left", padx=4)

    # ---------------- auto-resume ----------------
    def auto_resume(self):
        """If the bot was running when CBBF last closed, pick that session back up."""
        if not self.settings.get("auto_resume", True) or not self.cfg.get("was_running") or self.running:
            return
        mode = self.cfg.get("last_mode", "paper")
        saved = E.load_json(E.state_path(mode == "live"))
        if not saved:
            return
        if mode == "live":
            if not self.client:  # wait for the saved login to connect (up to about 30 seconds)
                self._resume_tries = getattr(self, "_resume_tries", 0) + 1
                if self._resume_tries < 30 and E.load_login():
                    self.root.after(1000, self.auto_resume)
                elif self._resume_tries >= 30:
                    self.write("Your LIVE session was running when CBBF closed, but Coinbase didn't connect, so it "
                               "was not resumed. Log in and press Start to resume it.", "warn")
                return
            if not messagebox.askyesno("Resume LIVE session?", "Your LIVE session was running when CBBF closed.\n\n"
                                                               "Resume it now? (You'll be asked to type LIVE.)"):
                return
        if not self.session:
            self.mode_var.set(mode)
            self.wild_var.set(bool(self.cfg.get("last_wild", True)))
            self.toggle_wild()
        self._auto_resume = True
        self.write(f"Picking up where you left off ({'LIVE' if mode == 'live' else 'Practice'} session).", "good")
        self.toggle()
        self._auto_resume = False

    # ---------------- only redraw what you can see ----------------
    def tab_visible(self, widget):
        try:
            return self.nb.select() == str(self.nb.nametowidget(widget.winfo_parent())) or \
                self.nb.select() == str(widget)
        except Exception:
            return True

    def refresh_visible(self):
        """Catches up the tab you just opened (hidden tabs skip their redraws to save effort)."""
        try:
            tab = self.nb.tab(self.nb.select(), "text").strip()
        except Exception:
            return
        if tab == "Trades" and getattr(self, "_trades_dirty", False):
            self._trades_dirty = False
            self.refresh_trades()
        elif tab == "Strategy scoreboard" and getattr(self, "_board_dirty", False):
            self._board_dirty = False
            self.refresh_board()
        elif "Bot Farm" in tab:
            self.grid_refresh()
        elif "Slow farm" in tab:
            self.slow_refresh()

    # ---------------- watchlist ----------------
    def build_watchlist(self, parent):
        bar = tk.Frame(parent, bg=CARD)
        bar.pack(fill="x", padx=6, pady=6)
        self.watch_entry = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.watch_entry, width=10)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.watch_add())
        ttk.Button(bar, text="Add", style="Accent.TButton", command=self.watch_add).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Browse coins...", style="Plain.TButton", command=self.open_coin_browser).pack(side="left", padx=4)
        for txt, cmd, tip in (("Buy...", lambda: self.trade_dialog("buy", self._watch_sel()), "Buy the selected coin by hand."),
                              ("Sell...", lambda: self.trade_dialog("sell", self._watch_sel()), "Sell the selected coin by hand."),
                              ("Price alert...", self.watch_alert, "Get notified when the selected coin goes above or below a price."),
                              ("Let the bots trade it", self.watch_to_bots, "Ticks the coin in COINS so the bots trade it next session."),
                              ("Remove", self.watch_remove, "Take the selected coins off the watchlist.")):
            b = ttk.Button(bar, text=txt, style="Plain.TButton", command=cmd)
            b.pack(side="left", padx=(6 if txt == "Buy..." else 2, 0))
            Tip(b, tip)
        self.watch_note = tk.Label(bar, text="", bg=CARD, fg=MUTED, font=("", 8))
        self.watch_note.pack(side="right")
        self.watch_tree = self.make_tree(parent, ("coin", "price", "chg", "d24", "vol", "own", "alert", "bots"),
                                         (70, 120, 110, 90, 130, 120, 150, 90),
                                         ("Coin", "Price", "Since added", "24h", "24h volume ($)", "You own", "Alert", "Bots trade it"))
        self.watch_tree.configure(selectmode="extended")
        self.watch_tree.bind("<Double-1>", lambda ev: self.show_on_chart(self._watch_sel())
                             if self.on_row(self.watch_tree, ev) and self._watch_sel() else None)
        for btn in ("<Button-3>", "<Button-2>"):
            self.watch_tree.bind(btn, self._watch_menu)
        self.watch_stats = {}
        self.watch_base = dict(self.cfg.get("watch_base") or {})
        self.refresh_watchlist()
        self.root.after(1500, self.start_watch_feed)

    def watchlist(self):
        w = self.cfg.get("watchlist")
        if not isinstance(w, list):
            w = self.cfg["watchlist"] = ["BTC", "ETH", "SOL"]
        return [c for c in w if E.valid_coin(c)]

    def _watch_sel(self):
        sel = self.watch_tree.selection() if hasattr(self, "watch_tree") else ()
        return sel[0] if sel else None

    def watch_add(self, coin=None):
        c = (coin or self.watch_entry.get()).strip().upper()
        if not E.valid_coin(c):
            return messagebox.showinfo("Watchlist", "Type a coin's ticker, like BTC or SOL (or use Browse coins).")
        w = self.watchlist()
        if c not in w:
            w.append(c)
            self.cfg["watchlist"] = w
            E.save_json(CONFIG_PATH, self.cfg)
            self.write(f"Watching {c}.")
        self.watch_entry.set("")
        self.refresh_watchlist()

    def watch_remove(self):
        sel = list(self.watch_tree.selection())
        if not sel:
            return messagebox.showinfo("Watchlist", "Select coins on the watchlist first.")
        self.cfg["watchlist"] = [c for c in self.watchlist() if c not in sel]
        alerts = self.cfg.get("watch_alerts") or {}
        for c in sel:
            alerts.pop(c, None)
        self.cfg["watch_alerts"] = alerts
        E.save_json(CONFIG_PATH, self.cfg)
        self.refresh_watchlist()

    def watch_to_bots(self):
        sel = list(self.watch_tree.selection())
        if not sel:
            return messagebox.showinfo("Watchlist", "Select a coin first.")
        self.add_coins(sel)
        self.write(f"Ticked {', '.join(sel)} for the bots." + (" They join when you start a new session." if self.session else ""))
        self.refresh_watchlist()

    def watch_alert(self):
        c = self._watch_sel()
        if not c:
            return messagebox.showinfo("Price alert", "Select a coin on the watchlist first.")
        px = self.last.get(c)
        cur = (self.cfg.get("watch_alerts") or {}).get(c, {})
        above = simpledialog.askstring("Price alert", f"{c} is at {fmt_px(px)}.\n\nAlert me when it goes ABOVE this price "
                                                      "(leave empty for none):", initialvalue=str(cur.get("above", "")))
        if above is None:
            return
        below = simpledialog.askstring("Price alert", f"Alert me when {c} goes BELOW this price (leave empty for none):",
                                       initialvalue=str(cur.get("below", "")))
        if below is None:
            return
        try:
            a = {k: float(v.replace(",", "").replace("$", "")) for k, v in (("above", above), ("below", below)) if v.strip()}
        except ValueError:
            return messagebox.showerror("Price alert", "Type plain numbers, like 70000.")
        alerts = self.cfg.get("watch_alerts") or {}
        if a:
            alerts[c] = a
        else:
            alerts.pop(c, None)
        self.cfg["watch_alerts"] = alerts
        E.save_json(CONFIG_PATH, self.cfg)
        self.refresh_watchlist()

    def _watch_menu(self, ev):
        iid = self.watch_tree.identify_row(ev.y)
        if not iid:
            return
        self.watch_tree.selection_set(iid)
        m = self._menu()
        m.add_command(label=iid, state="disabled")
        m.add_separator()
        m.add_command(label="Show on chart", command=lambda: self.show_on_chart(iid))
        m.add_command(label="Buy...", command=lambda: self.trade_dialog("buy", iid))
        m.add_command(label="Sell...", command=lambda: self.trade_dialog("sell", iid))
        m.add_command(label="Price alert...", command=self.watch_alert)
        m.add_command(label="Let the bots trade it", command=self.watch_to_bots)
        m.add_separator()
        m.add_command(label="Remove from watchlist", command=self.watch_remove)
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def start_watch_feed(self):
        """A light background price check for the watchlist and anything you own, so prices (and your balances)
        stay live even while the bot is stopped. One request for all coins every few seconds."""
        if getattr(self, "_watch_thread", None) and self._watch_thread.is_alive():
            return
        self.sync_thread_values()

        def loop():
            last_stats = 0.0
            while True:
                try:
                    # plain values only: Tk must never be touched from this thread
                    coins = list(dict.fromkeys(list(self._watch_coins) + [c for c, q in list(self.pos.items()) if q > 0]))
                    if self.running:  # the main feed already covers the session coins
                        coins = [c for c in coins if c not in self.session_coins]
                    prices = E.spot_many(coins, self._quote_now) if coins else {}
                    stats = None
                    if time.time() - last_stats > 300 and self._watch_coins:
                        last_stats = time.time()
                        try:
                            stats = {d["coin"]: d for d in E.list_products(self._quote_now)}
                        except Exception:
                            stats = None
                    if prices or stats:
                        self.q.put(("watch", (prices, stats)))
                except Exception:
                    pass
                time.sleep(20 if self._iconic else 5)
        self._watch_thread = threading.Thread(target=loop, daemon=True)
        self._watch_thread.start()

    def sync_thread_values(self):
        """Copies what background threads need into plain attributes (they must not call Tk)."""
        self._watch_coins = tuple(self.watchlist())
        self._quote_now = self.cur_quote()
        try:
            self._iconic = self.root.state() == "iconic"
        except tk.TclError:
            self._iconic = False

    def on_watch(self, prices, stats):
        ts = time.time()
        for c, px in prices.items():
            self.last[c] = px
            self.tick_px.setdefault(c, deque(maxlen=8000)).append((ts, px))
            if c not in self.watch_base:
                self.watch_base[c] = px
                self.cfg["watch_base"] = self.watch_base
        if stats:
            self.watch_stats = stats
        alerts = self.cfg.get("watch_alerts") or {}
        for c, a in list(alerts.items()):
            px = self.last.get(c)
            if not px:
                continue
            for k, hit in (("above", lambda lim: px >= lim), ("below", lambda lim: px <= lim)):
                if k in a and hit(a[k]):
                    msg = f"{c} is {'above' if k == 'above' else 'below'} {fmt_px(a[k])}: now {fmt_px(px)}"
                    self.write("♪ Price alert: " + msg, "good" if k == "above" else "warn")
                    self.notify("CBBF price alert", msg, sound="notify")
                    self.alert("trades", "Price alert: " + msg)
                    a.pop(k)
            if not a:
                alerts.pop(c, None)
        self.cfg["watch_alerts"] = alerts
        self.refresh_watchlist()
        if not self.running and self.session:
            self.refresh_view()  # keep Total value live while the bot is stopped

    def refresh_watchlist(self):
        if not hasattr(self, "watch_tree"):
            return
        t = self.watch_tree
        sel = t.selection()
        t.delete(*t.get_children())
        alerts = self.cfg.get("watch_alerts") or {}
        ticked = {c for c, v in self.coin_vars.items() if v.get()}
        for c in self.watchlist():
            px = self.last.get(c)
            st = self.watch_stats.get(c, {})
            base = self.watch_base.get(c)
            q = self.pos.get(c, 0)
            a = alerts.get(c, {})
            at = " / ".join(f"{'>' if k == 'above' else '<'} {fmt_px(v)}" for k, v in a.items())
            d24 = st.get("change_24h")
            t.insert("", "end", iid=c, tags=(("up",) if (d24 or 0) > 0 else ("down",) if d24 else ()), values=(
                c, fmt_px(px), "-" if not (px and base) else f"{(px / base - 1) * 100:+.2f}%",
                "-" if d24 is None else f"{d24:+.2f}%",
                "-" if not st.get("volume_usd") else f"{st['volume_usd']:,.0f}",
                "-" if q <= 0 else f"{q:,.6f}".rstrip("0").rstrip(".") + (f" (${q * px:,.0f})" if px else ""),
                at or "-", "yes" if c in (self.session_coins if self.session else ticked) else ""))
        t.tag_configure("up", foreground=GREEN)
        t.tag_configure("down", foreground=RED)
        keep = [i for i in sel if t.exists(i)]
        if keep:
            t.selection_set(keep)
        self.watch_note.config(text=f"{len(self.watchlist())} coins  |  prices every 5 s, even while the bot is stopped")

    # ---------------- portfolio tab ----------------
    def build_portfolio(self, parent):
        top = tk.Frame(parent, bg=CARD)
        top.pack(fill="x", padx=6, pady=(6, 2))
        self.pf_stats = {}
        for i, (k, t) in enumerate((("value", "Total value"), ("invested", "In coins"), ("cash", "Cash"),
                                    ("unreal", "Open profit / loss"), ("real", "Closed profit / loss"), ("fees", "Fees paid"),
                                    ("n", "Positions"))):
            f = tk.Frame(top, bg=CARD)
            f.grid(row=0, column=i, sticky="w", padx=(0, 22))
            tk.Label(f, text=t.upper(), bg=CARD, fg=MUTED, font=("", 7, "bold")).pack(anchor="w")
            l = tk.Label(f, text="-", bg=CARD, fg=TEXT, font=("", 11, "bold"))
            l.pack(anchor="w")
            self.pf_stats[k] = l
        btns = tk.Frame(top, bg=CARD)
        btns.grid(row=0, column=8, sticky="e")
        top.columnconfigure(7, weight=1)
        ttk.Button(btns, text="Buy...", style="Accent.TButton", command=lambda: self.trade_dialog("buy")).pack(side="left")
        ttk.Button(btns, text="Sell...", style="Plain.TButton", command=lambda: self.trade_dialog("sell")).pack(side="left", padx=4)
        self.pf_alloc = tk.Canvas(parent, height=26, bg=CARD, highlightthickness=0)
        self.pf_alloc.pack(fill="x", padx=6, pady=(2, 4))
        self.pf_alloc.bind("<Configure>", lambda e: self.later(self.draw_alloc, 180))
        self.pf_stops = tk.Label(parent, text="", bg=CARD, fg=MUTED, anchor="w", font=("", 8))
        self.pf_stops.pack(fill="x", padx=8)
        self.show_watch = tk.BooleanVar(value=bool(self.cfg.get("pf_watch", True)))
        bar = tk.Frame(parent, bg=CARD)
        bar.pack(fill="x", padx=6)
        ttk.Checkbutton(bar, text="Also show coins I'm watching but don't own", variable=self.show_watch,
                        style="Card.TCheckbutton", command=lambda: (self.cfg.__setitem__("pf_watch", self.show_watch.get()),
                                                                    self.refresh_portfolio(force=True))).pack(side="left")
        tk.Label(bar, text="Click a coin's name to see its chart.", bg=CARD, fg=MUTED, font=("", 8)).pack(side="right")
        self.pf_scroll = ScrollFrame(parent, CARD, width=600)
        self.pf_scroll.pack(fill="both", expand=True, padx=2, pady=(2, 2))
        self.pf_rows, self.pf_order = {}, []

    def _pf_row(self, c):
        box = self.pf_scroll.inner
        r = tk.Frame(box, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        badge = tk.Canvas(r, width=40, height=40, bg=CARD, highlightthickness=0)
        badge.grid(row=0, column=0, rowspan=2, padx=8, pady=6)
        draw_coin_badge(badge, 4, 4, 32, c)
        name = tk.Label(r, text=c, bg=CARD, fg=TEXT, font=("", 11, "bold"), cursor="hand2")
        name.grid(row=0, column=1, sticky="w")
        name.bind("<Button-1>", lambda e: self.show_on_chart(c))
        sub = tk.Label(r, text="", bg=CARD, fg=MUTED, font=("", 8))
        sub.grid(row=1, column=1, sticky="w")
        cols = {}
        for j, (k, t) in enumerate((("value", "Worth now"), ("pl", "Profit / loss"), ("avg", "Avg cost / now"),
                                    ("day", "24h"), ("stake", "Staking"))):
            tk.Label(r, text=t, bg=CARD, fg=MUTED, font=("", 7, "bold")).grid(row=0, column=2 + j, sticky="w", padx=(14, 0))
            l = tk.Label(r, text="-", bg=CARD, fg=TEXT, font=("", 9, "bold") if k in ("value", "pl") else ("", 9))
            l.grid(row=1, column=2 + j, sticky="w", padx=(14, 0))
            cols[k] = l
        cols["stake"].config(cursor="hand2")
        cols["stake"].bind("<Button-1>", lambda e: self.staking_info(c))
        share = tk.Canvas(r, width=90, height=8, bg=CARD, highlightthickness=0)
        share.grid(row=1, column=7, padx=(14, 0))
        b = tk.Frame(r, bg=CARD)
        b.grid(row=0, column=8, rowspan=2, sticky="e", padx=8)
        ttk.Button(b, text="Buy", width=5, command=lambda: self.trade_dialog("buy", c)).pack(side="left")
        ttk.Button(b, text="Sell", width=5, command=lambda: self.trade_dialog("sell", c)).pack(side="left", padx=(4, 0))
        r.columnconfigure(7, weight=1)
        return dict(frame=r, sub=sub, share=share, **cols)

    def refresh_portfolio(self, force=False):
        if not hasattr(self, "pf_rows"):
            return
        held = [c for c, q in self.pos.items() if q > 0]
        watch = [c for c in (self.session_coins or [c for c, v in self.coin_vars.items() if v.get()]) if c not in held]
        held.sort(key=lambda c: -self.pos[c] * self.position_price(c))
        order = held + (watch if self.show_watch.get() else [])
        if order != self.pf_order or force:
            for c in list(self.pf_rows):
                if c not in order:
                    self.pf_rows.pop(c)["frame"].destroy()
            for c in order:
                if c not in self.pf_rows:
                    self.pf_rows[c] = self._pf_row(c)
                self.pf_rows[c]["frame"].pack_forget()
            for c in order:
                self.pf_rows[c]["frame"].pack(fill="x", padx=4, pady=3)
            self.pf_order = order
            if not order:
                if not hasattr(self, "pf_empty"):
                    self.pf_empty = tk.Label(self.pf_scroll.inner, bg=CARD, fg=MUTED, pady=18, text=(
                        "You don't own any coins right now.\nThe bot buys when a strategy sees a good moment, or press Buy."))
                self.pf_empty.pack(fill="x")
            elif hasattr(self, "pf_empty"):
                self.pf_empty.pack_forget()
        eq = self.equity()
        invested = sum(q * self.position_price(c) for c, q in self.pos.items() if q > 0)
        unreal = sum(q * self.position_price(c) - self.cost.get(c, 0) for c, q in self.pos.items() if q > 0)
        real = 0.0
        for t in self.trades:
            try:
                real += float(t.get("pl") or 0)
            except (TypeError, ValueError):
                pass
        st = self.pf_stats
        st["value"].config(text=f"${eq:,.2f}")
        st["invested"].config(text=f"${invested:,.2f}")
        st["cash"].config(text=f"${self.cash:,.2f}")
        st["unreal"].config(text=f"{unreal:+,.2f}", fg=GREEN if unreal >= 0 else RED)
        st["real"].config(text=f"{real:+,.2f}", fg=GREEN if real >= 0 else RED)
        st["n"].config(text=str(len(held)))
        st["fees"].config(text=f"${self.fees_paid:,.2f}", fg=MUTED)
        stop, take, src = self.account_stops()
        trail = float(self.settings.get("trail_stop", 0) or 0)
        self.pf_stops.config(text=(f"Safety stop: {'off' if not stop else f'-{stop * 100:.1f}%'} (set by {src[0]})   |   "
                                   f"Lock in profit: {'off' if not take else f'+{take * 100:.1f}%'} (set by {src[1]})"
                                   + (f"   |   Trailing stop: {trail:g}%" if trail else "")))
        n24 = int(1440 // self.session_bar)
        for c in order:
            w = self.pf_rows[c]
            q = self.pos.get(c, 0)
            px = self.position_price(c)
            h = self.hist.get(c, [])
            day = f"{(px / h[-n24 - 1] - 1) * 100:+.2f}%" if len(h) > n24 and px else "-"
            w["day"].config(text=day, fg=(GREEN if day.startswith("+") else RED) if day != "-" else MUTED)
            kind = STAKING.get(c, ("?", ""))[0]
            apy = float(self.cfg.get("stake_apy", {}).get(c, 0) or 0)
            if q > 0:
                val, basis = q * px, self.cost.get(c, 0)
                pl = val - basis
                w["sub"].config(text=f"{q:,.6f}".rstrip("0").rstrip(".") + f" {c}  |  " + MOOD_TEXT.get(E.market_mood(h)[0], "-"))
                w["value"].config(text=f"${val:,.2f}")
                w["pl"].config(text=f"{pl:+,.2f} ({pl / basis * 100 if basis else 0:+.1f}%)", fg=GREEN if pl >= 0 else RED)
                w["avg"].config(text=f"{fmt_px(basis / q)} / {fmt_px(px)}")
                stake = (f"{kind}: ~${val * apy / 100:,.2f}/yr at {apy:g}%" if apy and kind == "Yes"
                         else f"{kind} (details)" if kind != "?" else "unknown (details)")
                share = val / eq if eq else 0
            else:
                w["sub"].config(text="watching  |  " + MOOD_TEXT.get(E.market_mood(h)[0], "-"))
                w["value"].config(text="-")
                w["pl"].config(text="-", fg=MUTED)
                w["avg"].config(text=f"now {fmt_px(px)}" if px else "-")
                stake = f"{kind} (details)" if kind != "?" else "unknown (details)"
                share = 0
            w["stake"].config(text=stake, fg=GREEN if kind == "Yes" else MUTED)
            cv = w["share"]
            cv.delete("all")
            cv.create_rectangle(0, 0, 90, 8, fill=LINE, outline="")
            cv.create_rectangle(0, 0, 90 * min(share, 1), 8, fill=coin_color(c), outline="")
        self.draw_alloc()

    def draw_alloc(self):
        c = self.pf_alloc
        c.delete("all")
        W = c.winfo_width()
        eq = self.equity()
        if W < 50 or eq <= 0:
            return
        parts = [(k, q * self.position_price(k)) for k, q in self.pos.items() if q > 0]
        parts.sort(key=lambda x: -x[1])
        parts.append(("Cash", self.cash))
        x = 0
        for name, v in parts:
            wd = W * max(v, 0) / eq
            if wd <= 0:
                continue
            col = LINE if name == "Cash" else coin_color(name)
            c.create_rectangle(x, 2, x + wd, 14, fill=col, outline=CARD)
            if wd > 48:
                c.create_text(x + 4, 20, anchor="w", text=f"{name} {v / eq * 100:.0f}%", fill=MUTED, font=("", 7))
            x += wd

    def staking_info(self, c):
        kind, note = STAKING.get(c, ("?", "Not in the built-in list. Check whether this coin uses proof of stake "
                                            "and whether your exchange offers staking for it."))
        w = tk.Toplevel(self.root)
        w.title(f"Staking: {c}")
        f = ttk.Frame(w, padding=16)
        f.pack()
        ttk.Label(f, text=f"{c}: can it be staked? {kind}", font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(f, text=note, wraplength=460, justify="left").pack(anchor="w", pady=(6, 10))
        ttk.Label(f, foreground=MUTED, wraplength=460, justify="left", text=(
            "Staking means locking coins to help run the network in exchange for a reward. Rates change often and "
            "differ by exchange and country, so CBBF doesn't guess them: type the rate you're offered below to see "
            "estimated earnings for what you hold. Coins the bot is trading are NOT staked. Staked coins usually "
            "can't be sold instantly (there can be an unstaking wait), so don't stake coins the bot needs to trade.")
        ).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=(10, 0))
        ttk.Label(row, text="Reward rate you're offered (% per year)").pack(side="left")
        apy = tk.StringVar(value=str(self.cfg.get("stake_apy", {}).get(c, "")))
        ttk.Entry(row, textvariable=apy, width=7).pack(side="left", padx=6)
        out = ttk.Label(f, text="", font=("", 10, "bold"))
        out.pack(anchor="w", pady=(8, 0))

        def calc(*_):
            try:
                r = float(apy.get())
            except ValueError:
                return out.config(text="")
            val = self.pos.get(c, 0) * self.position_price(c)
            out.config(text=(f"On your ${val:,.2f} of {c}: about ${val * r / 100:,.2f} a year, "
                             f"${val * r / 1200:,.2f} a month (before fees, if the price stayed the same)."
                             if val else f"You don't hold {c} right now; $1,000 would earn about ${10 * r:,.2f} a year."))
        apy.trace_add("write", calc)
        calc()

        def save():
            try:
                r = float(apy.get() or 0)
                assert 0 <= r < 100
            except Exception:
                return messagebox.showerror("Staking", "Type a yearly rate like 3.5", parent=w)
            self.cfg.setdefault("stake_apy", {})[c] = r
            E.save_json(CONFIG_PATH, self.cfg)
            w.destroy()
            self.refresh_portfolio(force=True)
        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(12, 0))
        ttk.Button(b, text="Close", command=w.destroy).pack(side="left", padx=6)
        ttk.Button(b, text="Save rate", style="Accent.TButton", command=save).pack(side="left")

    # ---------------- buying and selling by hand ----------------
    def trade_dialog(self, side, coin=None):
        if not self.session:
            live = self.mode_var.get() == "live"
            if not messagebox.askyesno("Trade by hand", f"Open a {'LIVE' if live else 'Practice'} session to trade by hand? "
                                                        "The bot stays off until you press Start."):
                return
            ticked = [c for c, v in self.coin_vars.items() if v.get()] or ["BTC"]
            if coin and coin not in ticked:
                ticked.append(coin)
            if not self.begin_session(ticked):
                return
            self.update_status()
        coins = list(dict.fromkeys(list(self.session_coins) + self.watchlist() + [c for c, q in self.pos.items() if q > 0]
                                   + ([coin] if coin else [])))
        if side == "sell":
            coins = [c for c in coins if self.pos.get(c, 0) > 0]
            if not coins:
                return messagebox.showinfo("Sell", "You don't own any coins right now.")
        coin = coin if coin in coins else coins[0]
        w = tk.Toplevel(self.root)
        w.title("Buy" if side == "buy" else "Sell")
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack()
        mode = "LIVE: real money" if self.live else "Practice: pretend money"
        ttk.Label(f, text=f"{'Buy' if side == 'buy' else 'Sell'}  ({mode})", font=("", 12, "bold"),
                  foreground=RED if self.live else TEXT).grid(row=0, column=0, columnspan=3, sticky="w")
        cv = tk.Canvas(f, width=40, height=40, bg=BG, highlightthickness=0)
        cv.grid(row=1, column=0, rowspan=2, pady=8)
        cvar = tk.StringVar(value=coin)
        cb = ttk.Combobox(f, textvariable=cvar, values=coins, state="readonly", width=8)
        cb.grid(row=1, column=1, sticky="w", padx=8)
        info = ttk.Label(f, text="", foreground=MUTED)
        info.grid(row=2, column=1, columnspan=2, sticky="w", padx=8)
        amt = tk.StringVar()
        if side == "buy":
            ttk.Label(f, text=f"Amount ({self.quote})").grid(row=3, column=0, sticky="w", pady=(8, 0))
            amt.set(f"{self.trade_size(coin):.2f}")
            ttk.Entry(f, textvariable=amt, width=12).grid(row=3, column=1, sticky="w", padx=8, pady=(8, 0))
            quick = ttk.Frame(f)
            quick.grid(row=4, column=1, columnspan=2, sticky="w", padx=8)
            for p in (10, 25, 50, 100):
                ttk.Button(quick, text=f"{p}% of cash", width=10,
                           command=lambda p=p: amt.set(f"{self.cash * p / 100:.2f}")).pack(side="left", padx=(0, 4))
        else:
            ttk.Label(f, text="How much").grid(row=3, column=0, sticky="w", pady=(8, 0))
            amt.set("100")
            quick = ttk.Frame(f)
            quick.grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=(8, 0))
            for p in (25, 50, 75, 100):
                ttk.Radiobutton(quick, text=f"{p}%", value=str(p), variable=amt).pack(side="left", padx=(0, 6))
        est = ttk.Label(f, text="", font=("", 10, "bold"))
        est.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

        def update(*_):
            c = cvar.get()
            cv.delete("all")
            draw_coin_badge(cv, 4, 4, 32, c)
            px = self.last.get(c) or self.position_price(c)
            q = self.pos.get(c, 0)
            info.config(text=f"Price {fmt_px(px)}  |  you own {q:,.6f}".rstrip("0").rstrip(".") + f"  |  cash ${self.cash:,.2f}")
            fee, slip = E.cost_model(dict(self.settings, order_type="market"))
            try:
                a = float(amt.get())
            except ValueError:
                return est.config(text="")
            if side == "buy":
                est.config(text=f"About {a * (1 - fee) / (px * (1 + slip)) if px else 0:,.6f} {c} after fees" if px else "")
            else:
                est.config(text=f"About ${q * a / 100 * px * (1 - fee) * (1 - slip):,.2f} after fees" if px else "")
        cb.bind("<<ComboboxSelected>>", update)
        amt.trace_add("write", update)
        update()

        def go():
            c = cvar.get()
            px = self.last.get(c)
            if not px:
                return messagebox.showerror("Trade", f"No live price for {c} yet. Start the bot so prices come in.", parent=w)
            if c in self.pending:
                return messagebox.showinfo("Trade", f"An order for {c} is still being filled. Try again in a moment.", parent=w)
            try:
                a = float(amt.get())
                assert a > 0
            except Exception:
                return messagebox.showerror("Trade", "Type an amount above zero.", parent=w)
            if side == "buy":
                if a > self.cash + 1e-9:
                    return messagebox.showerror("Buy", f"That's more than your cash (${self.cash:,.2f}).", parent=w)
                if self.live and a < MIN_LIVE_ORDER:
                    return messagebox.showerror("Buy", f"Live orders must be at least ${MIN_LIVE_ORDER:.0f}.", parent=w)
                if self.live and not messagebox.askyesno("Buy", f"Place a REAL market order to buy ${a:,.2f} of {c}?", parent=w):
                    return
                self.manual_buy(c, a, px)
            else:
                frac = min(a, 100) / 100
                if self.live and not messagebox.askyesno("Sell", f"Place a REAL market order to sell {frac * 100:.0f}% of your {c}?",
                                                         parent=w):
                    return
                self.manual_sell(c, frac, px)
            w.destroy()
        b = ttk.Frame(f)
        b.grid(row=6, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(b, text="Cancel", command=w.destroy).pack(side="left", padx=6)
        ttk.Button(b, text="Buy" if side == "buy" else "Sell", style="Accent.TButton" if side == "buy" else "Stop.TButton",
                   command=go).pack(side="left")

    def manual_buy(self, c, usd, px):
        self.pending_who[c] = "you (by hand)"
        if self.live:
            self.cash -= usd
            self.pending.add(c)
            self.executor.submit(self.live_buy, c, usd, "manual buy")
            return
        fee, slip = E.cost_model(dict(self.settings, order_type="market"))
        fill = px * (1 + slip)
        qty = usd * (1 - fee) / fill
        self.fees_paid += usd * fee
        self.cash -= usd
        self.pos[c] = self.pos.get(c, 0) + qty
        self.cost[c] = self.cost.get(c, 0) + usd
        self.log_trade("buy", c, qty, fill, usd, None, "manual buy")

    def manual_sell(self, c, frac, px):
        held = self.pos.get(c, 0)
        if held <= 0:
            return
        qty = held if frac >= 0.999 else held * frac
        self.pending_who[c] = "you (by hand)"
        if self.live:
            self.pending.add(c)
            self.executor.submit(self.live_sell, c, qty, "manual")
            return
        fee, slip = E.cost_model(dict(self.settings, order_type="market"))
        fill = px * (1 - slip)
        proceeds = qty * fill * (1 - fee)
        self.fees_paid += qty * fill * fee
        basis = self.cost.get(c, 0) * (qty / held)
        self.cash += proceeds
        self.pos[c] = held - qty
        self.cost[c] = self.cost.get(c, 0) - basis
        if self.pos[c] < 1e-12:
            self.pos[c], self.cost[c] = 0, 0
        self.log_trade("sell", c, qty, fill, proceeds, proceeds - basis, "manual")

    # ---------------- animated bot farm strip ----------------
    def build_farm_strip(self, parent):
        self.farm = tk.Canvas(parent, height=46, bg=CARD, highlightthickness=0)
        self.farm.pack(fill="x", padx=4, pady=(0, 4))
        self.farm_phase = 0
        self.farm_hits = []  # (x0, y0, x1, y1, variant) for clicks
        self.farm_hover = None
        self.farm.bind("<Motion>", self._farm_motion)
        self.farm.bind("<Leave>", lambda e: setattr(self, "farm_hover", None))
        self.farm.bind("<Button-1>", self._farm_click)
        self.farm.bind("<Double-1>", lambda e: (self._farm_click(e), self.bot_detail(quiet=True)) if self._farm_at(e.x, e.y) else None)
        for btn in ("<Button-3>", "<Button-2>"):
            self.farm.bind(btn, lambda e: self.bot_menu(e, self._farm_at(e.x, e.y)))
        # (animated by the shared clock in animate_all)

    def _farm_at(self, x, y):
        for x0, y0, x1, y1, v in self.farm_hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return v
        return None

    def _farm_motion(self, e):
        self.farm_hover = self._farm_at(e.x, e.y)

    def _farm_click(self, e):
        v = self._farm_at(e.x, e.y)
        if v and self.board.exists(str(id(v))):
            self.board.selection_set(str(id(v)))
            self.board.see(str(id(v)))

    def animate_all(self):
        """One clock for every animation, so all the dragons move in step. Only the view you can see is drawn,
        nothing is drawn while the window is minimised, and Settings > Animations can slow it or switch it off."""
        mode = self.settings.get("animations", "Full")
        delay = {"Full": 160, "Light": 480}.get(mode, 1000)
        self.sync_thread_values()
        try:
            if mode != "Off" and self.root.state() != "iconic" and not self.moving():
                self.farm_phase = getattr(self, "farm_phase", 0) + 1
                tab = self.nb.index(self.nb.select()) if hasattr(self, "nb") else 0
                if tab == 0:
                    self._grid_anim_frame()
                elif self.nb.tab(tab, "text").strip() == "Strategy scoreboard":
                    self._draw_farm()
        except Exception:
            pass
        self.root.after(delay, self.animate_all)

    def _draw_farm(self):
        c = self.farm
        if not c.winfo_ismapped() or self.farm_style.get() != "Dragons":
            return
        c.delete("all")
        ph = self.farm_phase
        W = max(c.winfo_width(), 100)
        self.farm_hits = []
        if not self.tourn:
            msg = "Your bot farm appears here when Wildcard is running." if not self.running else "Waking the bots up..."
            c.create_text(W // 2, 22, text=msg, fill=MUTED)
            c.config(height=46)
            return
        rows = self.tourn.board()
        size, gap = (26, 6) if len(rows) <= 60 else (18, 4)
        per_row = max(1, (W - 8) // (size + gap))
        nrows = (len(rows) + per_row - 1) // per_row
        want_h = nrows * (size + 18) + 6
        if int(c.cget("height")) != want_h:
            c.config(height=want_h)
        amber = "#fbbf24" if DARK else "#d97706"
        hofk = self.hof_keys()
        colors = {"active": GREEN, "slowed": amber, "standby": ACCENT, "benched": "#6b7280", "warming up": "#8b5cf6"}
        awake_now = self.running and self.seeded
        for i, r in enumerate(rows):
            v = r["v"]
            st = r["status"]
            col = colors.get(st, MUTED)
            x0 = 6 + (i % per_row) * (size + gap)
            y0 = 12 + (i // per_row) * (size + 18)
            working = awake_now and st in ("active", "slowed", "standby", "warming up")
            just_traded = bool(v.log) and v.log[-1][0] == self.tourn.n and awake_now
            bob = round(2 * math.sin(ph / 2.2)) if working else 0
            y = y0 + bob
            mood = "happy" if st == "active" else "plain"
            draw_creature(c, v.name, x0, y, size, col, status=st, awake=working, phase=ph, mood=mood,
                        fire=just_traded and v.log[-1][2] == "buy", flash=just_traded and ph % 2 == 0)
            if not working:
                k = ph % 18
                if k < 12:
                    c.create_text(x0 + size + 1 + k / 3, y + 2 - k, text="z" if k < 6 else "Z", fill=MUTED,
                                  font=("", 7 + k // 6, "bold"))
            if just_traded:
                side = v.log[-1][2]
                c.create_text(x0 + size / 2, y - 9, text="$" if side == "sell" else "+", fill=GREEN if side == "buy" else amber,
                              font=("", 9, "bold"))
            if v.key() in hofk:
                c.create_text(x0 + size - 1, y + 5, text="★", fill="#f59e0b", font=("", 8))
            sel = self.board.selection()
            if sel and sel[0] == str(id(v)):
                c.create_rectangle(x0 - 3, y0 - 8, x0 + size + 3, y0 + size + 5, outline=TEXT, dash=(2, 2))
            self.farm_hits.append((x0 - 2, y0 - 8, x0 + size + 2, y0 + size + 4, v))
        hv = self.farm_hover
        if hv is not None and hv in self.tourn.vars:
            d = self.tourn.info.get(id(hv), {})
            nick = E.bot_nickname(hv.key())
            txt = (f"{nick}: {friendly_label(hv.name, hv.params, hv.tf, self.session_bar)}  |  "
                   f"{STATUS_TEXT.get(d.get('status'), d.get('status', ''))}  |  recent {pct_txt(d.get('score'))}  |  "
                   f"{'working' if (awake_now and d.get('status') != 'benched') else 'sleeping'}")
            t = c.create_text(6, 1, text=txt, anchor="nw", fill=TEXT, font=("", 8))
            bb = c.bbox(t)
            c.create_rectangle(bb[0] - 3, bb[1] - 1, bb[2] + 3, bb[3] + 1, fill=CARD, outline=LINE)
            c.tag_raise(t)

    # ---------------- cached look-ups (no file reading on every frame) ----------------
    def _cached(self, path, loader):
        try:
            m = os.path.getmtime(path)
        except OSError:
            m = None
        cache = self.__dict__.setdefault("_file_cache", {})
        if path not in cache or cache[path][0] != m:
            cache[path] = (m, loader())
        return cache[path][1]

    def library(self):
        return self._cached(E.collection_path(), E.load_collection)

    def library_keys(self):
        return {E.bot_key(b) for b in self.library()}

    def hof(self):
        return self._cached(E.hof_path(), E.load_hof)

    def hof_keys(self):
        return {E.bot_key(b) for b in self.hof()}

    # ---------------- Bot Collection ----------------
    def catch_rows(self, rows, source):
        """rows: tournament board rows or bot-file entries. Returns the caught entries."""
        entries = []
        for r in rows:
            if "v" in r:
                entries += E.export_bots([dict(r, bar_minutes=self.session_bar)], self.version)["bots"]
            else:
                entries += E.export_bots([r], self.version)["bots"]
        new, ups, caught = E.catch(entries, source)
        for b in caught:
            shiny = " ✨ SHINY" if b.get("shiny") else ""
            self.write(f"Caught {b['nickname']}{shiny}: {b['rarity']}, level {b['level']} "
                       f"({friendly_label(b['name'], b['params'])}).", "good")
        if ups:
            self.write(f"{ups} bot{'s' if ups != 1 else ''} in your collection levelled up.", "good")
        if getattr(self, "col_win", None) is not None and self.col_win.winfo_exists():
            self.col_refresh()
        return caught

    def catch_selected(self):
        vs = self.selected_variants()
        if not vs:
            return messagebox.showinfo("Catch", "Select one or more bots on the scoreboard (or in the farm above it) first.")
        ids = {id(v) for v in vs}
        self.catch_rows([r for r in self.tourn.board() if id(r["v"]) in ids], "scoreboard")

    ROSTERS = ("farm", "team", "hof")

    def roster_mode(self):
        return "live" if (self.live if self.session else self.mode_var.get() == "live") else "paper"

    def roster(self, mode=None):
        mode = mode or self.roster_mode()
        r = self.cfg.get(f"roster_{mode}", "farm")
        return r if r in self.ROSTERS else "farm"

    def roster_labels(self):
        return {"farm": "Whole farm", "team": f"My team ({len(E.team())}/{E.TEAM_MAX})",
                "hof": f"Hall of Fame only ({len(self.hof())})"}

    def set_roster(self):
        lab = {v: k for k, v in self.roster_labels().items()}
        r = lab.get(self.roster_var.get(), "farm")
        if r == "team" and not E.team():
            messagebox.showinfo("Team", "Your team is empty. Open the Bot Library (Ctrl+B), select up to "
                                        f"{E.TEAM_MAX} bots and press 'Add to team' (or right-click a bot).")
            r = self.roster()
        elif r == "hof" and not self.hof():
            messagebox.showinfo("Hall of Fame", "Your Hall of Fame is empty. Right-click any bot (farm, scoreboard or "
                                                "Library) and choose 'Add to Hall of Fame'.")
            r = self.roster()
        self.cfg[f"roster_{self.roster_mode()}"] = r
        E.save_json(CONFIG_PATH, self.cfg)
        if r != "farm" and not self.wild_var.get():
            self.wild_var.set(True)
            self.toggle_wild()
        self.update_team_label()
        if self.session and self.tourn:
            self.write("The change of who trades applies when Wildcard next starts (File > Reset session).", "warn")

    def update_team_label(self):
        if not hasattr(self, "roster_box"):
            return
        labels = self.roster_labels()
        self.roster_box.config(values=[labels[k] for k in self.ROSTERS])
        self.roster_var.set(labels[self.roster()])

    # ---------------- Hall of Fame: only what you pick ----------------
    def promote_variants(self, vs):
        if not vs or not self.tourn:
            return
        ids = {id(v) for v in vs}
        rows = [dict(r, bar_minutes=self.session_bar) for r in self.tourn.board() if id(r["v"]) in ids]
        entries = E.export_bots(rows, self.version, note="picked by you")["bots"]
        self.catch_rows(entries, "hall of fame")  # Hall of Fame bots are always in the Library too
        E.induct(entries, self.version)
        self.write(f"\u2605 Added {', '.join(E.bot_nickname(v.key()) for v in vs)} to the Hall of Fame.", "good")
        self.after_hof_change()

    def promote_entries(self, entries):
        if not entries:
            return
        E.induct(E.export_bots(entries, self.version)["bots"], self.version)
        self.write(f"\u2605 Added {', '.join(b.get('nickname') or E.bot_nickname(E.bot_key(b)) for b in entries)} "
                   f"to the Hall of Fame.", "good")
        self.after_hof_change()

    def demote_keys(self, keys, names=""):
        keys = set(keys)
        if not keys:
            return
        snap = self.lib_snapshot()
        E.remove_from_hof(keys)
        self.write(f"Removed {names or str(len(keys)) + ' bots'} from the Hall of Fame (still in your Library).")
        self.after_hof_change()
        self.offer_undo(f"Removed {names or str(len(keys)) + ' bots'} from the Hall of Fame.", snap)

    def after_hof_change(self):
        self.update_team_label()
        self.refresh_board()
        if getattr(self, "col_win", None) is not None and self.col_win.winfo_exists():
            self.col_refresh()
        if getattr(self, "hof_win", None) is not None and self.hof_win.winfo_exists():
            self.hof_win.destroy()
            self.open_hof()

    # ---------------- right-click menus ----------------
    def _menu(self):
        return tk.Menu(self.root, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT, activeforeground="white")

    def bot_menu(self, e, v):
        """Right-click menu for a bot on the farm."""
        if v is None or not self.tourn or v not in self.tourn.vars:
            return
        if self.board.exists(str(id(v))) and str(id(v)) not in self.board.selection():
            self.board.selection_set(str(id(v)))
        chosen = self.selected_variants() or [v]
        many = len(chosen) > 1
        name = f"{len(chosen)} bots" if many else E.bot_nickname(v.key())
        in_hof = v.key() in self.hof_keys()
        in_lib = v.key() in self.library_keys()
        m = self._menu()
        m.add_command(label=name, state="disabled")
        m.add_separator()
        if not many:
            m.add_command(label="Details...", command=lambda: self.bot_detail(v))
            m.add_command(label="Show on chart", command=lambda: (self.board.selection_set(str(id(v))) if self.board.exists(str(id(v))) else None,
                self.chart_bot.set(True), self.show_on_chart(
                next((c for n, c, _, _ in reversed(v.log)), self.session_coins[0] if self.session_coins else "Account value"))))
        m.add_command(label="\u25c9 Save to Library" if not in_lib or many else "\u25c9 Update in Library",
                      command=lambda: self.catch_rows([r for r in self.tourn.board() if r["v"] in chosen], "right-click"))
        if in_hof and not many:
            m.add_command(label="\u2715 Remove from Hall of Fame", command=lambda: self.demote_keys([v.key()], E.bot_nickname(v.key())))
        else:
            m.add_command(label="\u2605 Add to Hall of Fame", command=lambda: self.promote_variants(chosen))
        m.add_command(label="Add to my team", command=lambda: self._team_from_variants(chosen))
        m.add_separator()
        if not many:
            m.add_command(label="\u270e See / edit its rules...", command=lambda: self.edit_rules(v))
            m.add_command(label="Backtest this bot...", command=lambda: self.open_backtest(v))
        if len(chosen) == 2:
            m.add_command(label="Compare these two...", command=lambda: self.compare_bots(chosen[0], chosen[1]))
        elif not many and self.selected_variants() and self.selected_variants()[0] is not v:
            other = self.selected_variants()[0]
            m.add_command(label=f"Compare with {E.bot_nickname(other.key())}...", command=lambda: self.compare_bots(v, other))
        m.add_separator()
        m.add_command(label="Trade size +", command=lambda: [self._size(x, 0.25) for x in chosen])
        m.add_command(label="Trade size -", command=lambda: [self._size(x, -0.25) for x in chosen])
        m.add_command(label="Export...", command=self.export_bots)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _size(self, v, d):
        v.size_mult = round(min(max(v.size_mult + d, 0.25), 3.0), 2)
        self.tourn._rate()
        self.refresh_board()

    def _team_from_variants(self, vs):
        caught = self.catch_rows([r for r in self.tourn.board() if r["v"] in vs], "team")
        size, full = E.set_team([E.bot_key(b) for b in caught], True)
        self.write(f"Team: {size}/{E.TEAM_MAX}." + (f" {full} didn't fit (the team is full)." if full else ""))
        self.update_team_label()

    def entry_menu(self, e, entries, where):
        """Right-click menu for saved bots (Library cards or Hall of Fame rows)."""
        if not entries:
            return
        hofk = self.hof_keys()
        m = self._menu()
        name = f"{len(entries)} bots" if len(entries) > 1 else (entries[0].get("nickname") or E.bot_nickname(E.bot_key(entries[0])))
        m.add_command(label=name, state="disabled")
        m.add_separator()
        out = [b for b in entries if E.bot_key(b) not in hofk]
        inn = [b for b in entries if E.bot_key(b) in hofk]
        if out:
            m.add_command(label="\u2605 Add to Hall of Fame", command=lambda: self.promote_entries(out))
        if inn:
            m.add_command(label="\u2715 Remove from Hall of Fame", command=lambda: self.demote_keys(
                [E.bot_key(b) for b in inn], ", ".join(b.get("nickname") or E.bot_nickname(E.bot_key(b)) for b in inn)))
        if where == "library":
            m.add_command(label="Add to team", command=lambda: (E.set_team([E.bot_key(b) for b in entries], True), self.col_refresh()))
            m.add_command(label="Remove from team", command=lambda: (E.set_team([E.bot_key(b) for b in entries], False), self.col_refresh()))
        m.add_command(label="Add to farm (they compete and breed)", command=lambda: self._add_entries_to_run(entries))
        if len(entries) == 1:
            m.add_command(label="\u270e See / edit its rules...", command=lambda: self.edit_rules(entry=entries[0]))
            m.add_command(label="Backtest it...", command=lambda: self.open_backtest(None))
        if where == "library" and len(entries) == 2:
            m.add_command(label="\u265b Breed these two", command=self._col_breed)
        m.add_separator()
        if where == "library":
            m.add_command(label="Release from Library", command=self._col_release)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _add_entries_to_run(self, entries):
        if not self.tourn or not self.seeded:
            return messagebox.showinfo("Add bots", "Start the bot with Wildcard ticked first.")
        closes = {c: self.hist[c] for c in self.session_coins}
        have, n = {v.key() for v in self.tourn.vars}, 0
        for b in entries:
            if E.bot_key(b) not in have and self.tourn.add_variant(
                    b["name"], b["params"], closes, b.get("size", 1.0), note="Added:", tf=b.get("tf", 1), risk=b.get("risk"),
                    traits=b.get("traits"), vetoes=b.get("vetoes"), layers=b.get("layers"), origin="library"):
                n += 1
        self.flush_events()
        self.refresh_board()
        self.write(f"Added {n} bots to this run." if n else "Those bots are already on the farm.")

    def open_collection(self):
        if getattr(self, "col_win", None) is not None and self.col_win.winfo_exists():
            return self.col_win.lift()
        w = self.col_win = tk.Toplevel(self.root)
        w.title("Bot Library")
        w.geometry("1020x680")
        top = ttk.Frame(w, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        self.col_head = ttk.Label(top, text="Bot Library", font=("", 13, "bold"))
        self.col_head.pack(anchor="w")
        ttk.Label(top, foreground=MUTED, wraplength=980, text=(
            "Catch bots from the scoreboard, the Bot Lab or the Hall of Fame. Rarity comes from how much they beat "
            "holding, their win rate and how long they kept winning; catching a bot again levels it up. Pick a team "
            f"of up to {E.TEAM_MAX} and tick 'Trade with my team' to run Wildcard with just them.")).pack(anchor="w", pady=(2, 6))
        bar = ttk.Frame(top)
        bar.pack(fill="x")
        ttk.Label(bar, text="Show").pack(side="left")
        self.col_filter = tk.StringVar(value="All")
        cb = ttk.Combobox(bar, textvariable=self.col_filter, values=["All", "Team"] + list(reversed(E.RARITIES)),
                          state="readonly", width=10)
        cb.pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="Sort by").pack(side="left")
        self.col_sort = tk.StringVar(value="Rarity")
        sb = ttk.Combobox(bar, textvariable=self.col_sort, values=["Rarity", "Level", "vs holding", "Newest", "Name"],
                          state="readonly", width=10)
        sb.pack(side="left", padx=4)
        for box in (cb, sb):
            box.bind("<<ComboboxSelected>>", lambda e: self.col_refresh())
        self.col_canvas = tk.Canvas(w, bg=BG, highlightthickness=0)
        ys = ttk.Scrollbar(w, orient="vertical", command=self.col_canvas.yview)
        self.col_canvas.configure(yscrollcommand=ys.set)
        btns = ttk.Frame(w, padding=10)
        btns.pack(side="bottom", fill="x")
        ys.pack(side="right", fill="y")
        self.col_canvas.pack(fill="both", expand=True, padx=(12, 0))
        self.col_canvas.bind("<Configure>", lambda e: self.later(self.col_draw, 180))
        self.col_canvas.bind("<Button-1>", self._col_click)
        self.col_canvas.bind("<Double-1>", lambda e: (self._col_click(e), self._col_team(None)))
        for btn in ("<Button-3>", "<Button-2>"):
            self.col_canvas.bind(btn, self._col_right)
        self.col_canvas.bind("<MouseWheel>", lambda e: self.col_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.col_canvas.bind("<Button-4>", lambda e: self.col_canvas.yview_scroll(-1, "units"))
        self.col_canvas.bind("<Button-5>", lambda e: self.col_canvas.yview_scroll(1, "units"))
        self.col_sel = set()
        for txt, cmd, tip in (("Add to team", lambda: self._col_team(True), "Your team trades together when "
                               "'Trade with my team' is ticked. Double-click a card to add or remove it."),
                              ("Remove from team", lambda: self._col_team(False), ""),
                              ("\u2605 Add to Hall of Fame", lambda: self.promote_entries(self._col_chosen()) if self._col_chosen()
                               else messagebox.showinfo("Hall of Fame", "Click a card first.", parent=self.col_win),
                               "Your Hall of Fame holds only the bots you pick. 'Bots trading: Hall of Fame only' "
                               "trades with just them."),
                              ("Remove from Hall of Fame", lambda: self.demote_keys([E.bot_key(b) for b in self._col_chosen()]),
                               "They stay in your Library."),
                              ("Add to farm", self._col_add_run, "Drop the selected bots onto the farm: they compete and "
                               "breed, and their children hatch from royal eggs."),
                              ("Breed pair", self._col_breed, "Select exactly two bots and breed a child from them right "
                               "now. It starts as a royal egg on the farm."),
                              ("Export...", self._col_export, "Save the selected bots (or all) to a bot file to share."),
                              ("Release", self._col_release, "Remove the selected bots from your collection.")):
            b = ttk.Button(btns, text=txt, command=cmd, style="Accent.TButton" if txt == "Add to team" else "TButton")
            b.pack(side="left", padx=(0, 6))
            if tip:
                Tip(b, tip)
        self.col_team_lbl = ttk.Label(btns, foreground=MUTED)
        self.col_team_lbl.pack(side="left", padx=10)
        ttk.Button(btns, text="Close", command=w.destroy).pack(side="right")
        self.col_refresh()

    def col_refresh(self):
        self.col_bots = E.load_collection()
        keys = {E.bot_key(b) for b in self.col_bots}
        self.col_sel &= keys
        counts = {r: sum(1 for b in self.col_bots if b.get("rarity") == r) for r in E.RARITIES}
        shiny = sum(1 for b in self.col_bots if b.get("shiny"))
        self.col_head.config(text=f"Bot Library: {len(self.col_bots)} saved  |  {len(self.hof())} in the Hall of Fame  |  "
                                  + "  ".join(f"{n} {r}" for r, n in reversed(list(counts.items())) if n)
                                  + (f"  |  ✨ {shiny} shiny" if shiny else ""))
        n_team = sum(1 for b in self.col_bots if b.get("team"))
        self.col_team_lbl.config(text=f"Team: {n_team}/{E.TEAM_MAX}")
        if hasattr(self, "roster_box"):
            self.update_team_label()
        self.col_draw()

    def col_draw(self):
        c = self.col_canvas
        c.delete("all")
        bots = list(self.col_bots)
        f = self.col_filter.get()
        if f == "Team":
            bots = [b for b in bots if b.get("team")]
        elif f in E.RARITIES:
            bots = [b for b in bots if b.get("rarity") == f]
        srt = self.col_sort.get()
        rk = {r: i for i, r in enumerate(E.RARITIES)}
        if srt == "Rarity":
            bots.sort(key=lambda b: (-rk.get(b.get("rarity"), 0), -int(b.get("level") or 1)))
        elif srt == "Level":
            bots.sort(key=lambda b: -int(b.get("level") or 1))
        elif srt == "vs holding":
            bots.sort(key=lambda b: -float((b.get("lifetime") or {}).get("vs_holding_pct") or -999))
        elif srt == "Newest":
            bots.sort(key=lambda b: str(b.get("caught_at", "")), reverse=True)
        else:
            bots.sort(key=lambda b: str(b.get("nickname", "")))
        W = max(c.winfo_width(), 300)
        cw, ch, g = 230, 150, 12
        per = max(1, (W - g) // (cw + g))
        self.col_cards = []
        if not bots:
            c.create_text(W // 2, 80, fill=MUTED, justify="center", text=(
                "No bots here yet.\nSelect bots on the scoreboard and press 'Catch!', or catch Bot Lab passes and "
                "Hall of Fame bots.\nHall of Fame bots are caught automatically."))
        for i, b in enumerate(bots):
            x = g + (i % per) * (cw + g)
            y = g + (i // per) * (ch + g)
            k = E.bot_key(b)
            rc = E.RARITY_COLORS.get(b.get("rarity"), "#9ca3af")
            sel = k in self.col_sel
            c.create_rectangle(x, y, x + cw, y + ch, fill=CARD, outline=TEXT if sel else rc, width=3 if sel else 2)
            c.create_rectangle(x, y, x + cw, y + 26, fill=rc, outline=rc)
            c.create_text(x + 8, y + 13, anchor="w", fill="white", font=("", 10, "bold"),
                          text=("✨ " if b.get("shiny") else "") + b.get("nickname", "?"))
            c.create_text(x + cw - 8, y + 13, anchor="e", fill="white", font=("", 9, "bold"), text=f"Lv {b.get('level', 1)}")
            draw_creature(c, b["name"], x + 8, y + 40, 42, rc, awake=True, phase=0, mood="happy", gold=bool(b.get("shiny")))
            lt = b.get("lifetime") or {}

            def p(key):
                v = lt.get(key)
                return "-" if v is None else f"{v:+.1f}%"
            lines = [f"{STRAT_LABELS.get(b['name'], b['name'])}",
                     f"{b.get('rarity', 'Common')}  |  XP {b.get('xp', 0)}",
                     f"vs holding {p('vs_holding_pct')}",
                     f"win rate {'-' if lt.get('win_rate_pct') is None else str(round(lt['win_rate_pct'])) + '%'}",
                     (E.risk_text(b.get("risk")) or "no own stops")
                     + (f" +{len(b.get('traits') or {})} traits" if b.get("traits") else "")]
            for j, line in enumerate(lines):
                c.create_text(x + 56, y + 36 + j * 16, anchor="w", text=line, fill=TEXT if j == 0 else MUTED,
                              font=("", 9, "bold") if j == 0 else ("", 8))
            settings = "/".join(f"{v:g}" for v in b["params"].values()) + (f", pace x{b.get('tf', 1)}" if b.get("tf", 1) > 1 else "")
            c.create_text(x + 8, y + ch - 12, anchor="w", text=settings, fill=MUTED, font=("", 7))
            tags_txt = (["TEAM"] if b.get("team") else []) + (["\u2605 HALL OF FAME"] if k in self.hof_keys() else [])
            if tags_txt:
                c.create_text(x + cw - 8, y + ch - 12, anchor="e", text="  ".join(tags_txt), fill="#f59e0b", font=("", 8, "bold"))
            self.col_cards.append((x, y, x + cw, y + ch, k))
        rows = (len(bots) + per - 1) // per
        c.configure(scrollregion=(0, 0, W, rows * (ch + g) + g))

    def _col_click(self, e):
        x, y = self.col_canvas.canvasx(e.x), self.col_canvas.canvasy(e.y)
        for x0, y0, x1, y1, k in self.col_cards:
            if x0 <= x <= x1 and y0 <= y <= y1:
                if e.state & 0x0004 or e.state & 0x0001:  # ctrl / shift: add to the selection
                    self.col_sel ^= {k}
                else:
                    self.col_sel = {k}
                break
        else:
            self.col_sel = set()
        self.col_draw()

    def _col_right(self, e):
        x, y = self.col_canvas.canvasx(e.x), self.col_canvas.canvasy(e.y)
        for x0, y0, x1, y1, k in self.col_cards:
            if x0 <= x <= x1 and y0 <= y <= y1:
                if k not in self.col_sel:
                    self.col_sel = {k}
                    self.col_draw()
                return self.entry_menu(e, self._col_chosen(), "library")

    def _col_chosen(self, all_if_none=False):
        bots = [b for b in self.col_bots if E.bot_key(b) in self.col_sel]
        return bots or (list(self.col_bots) if all_if_none else [])

    def _col_team(self, on):
        bots = self._col_chosen()
        if not bots:
            return messagebox.showinfo("Team", "Click a card first (Ctrl-click for several).", parent=self.col_win)
        if on is None:  # double-click toggles
            on = not bots[0].get("team")
        size, full = E.set_team([E.bot_key(b) for b in bots], on)
        if full:
            messagebox.showinfo("Team", f"A team has at most {E.TEAM_MAX} bots. Remove one first.", parent=self.col_win)
        self.col_refresh()

    def _col_add_run(self):
        if not self.tourn or not self.seeded:
            return messagebox.showinfo("Bot Collection", "Start the bot with Wildcard ticked first.", parent=self.col_win)
        bots = self._col_chosen()
        if not bots:
            return messagebox.showinfo("Bot Collection", "Click a card first.", parent=self.col_win)
        closes = {c: self.hist[c] for c in self.session_coins}
        have, n = {v.key() for v in self.tourn.vars}, 0
        for b in bots:
            if E.bot_key(b) in have:
                continue
            if self.tourn.add_variant(b["name"], b["params"], closes, b.get("size", 1.0), note=f"{b.get('nickname')} joined:",
                                      tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), vetoes=b.get("vetoes"), layers=b.get("layers"), origin="collection"):
                n += 1
        self.flush_events()
        self.refresh_board()
        self.write(f"Added {n} bots from your collection to this run." if n else "Those bots are already competing.")

    def _col_breed(self):
        bots = self._col_chosen()
        if len(bots) != 2:
            return messagebox.showinfo("Breed", "Select exactly two bots (Ctrl-click) to breed them.", parent=self.col_win)
        if not self.tourn or not self.seeded:
            return messagebox.showinfo("Breed", "Start the bot with Wildcard ticked first.", parent=self.col_win)
        closes = {c: self.hist[c] for c in self.session_coins}
        parents = []
        for b in bots:
            v = next((x for x in self.tourn.vars if x.key() == E.bot_key(b)), None) or self.tourn.add_variant(
                b["name"], b["params"], closes, b.get("size", 1.0), note="Parent joined:", tf=b.get("tf", 1),
                risk=b.get("risk"), traits=b.get("traits"), vetoes=b.get("vetoes"), layers=b.get("layers"), origin="library")
            if v:
                parents.append(v)
        if len(parents) != 2:
            return
        child = self.tourn.breed_pair(parents[0], parents[1], closes)
        self.flush_events()
        self.refresh_board()
        self.write(f"♛ A royal egg joined the farm: {E.bot_nickname(child.key())}, child of "
                   f"{bots[0].get('nickname', '?')} and {bots[1].get('nickname', '?')}.", "good")

    def _col_export(self):
        bots = self._col_chosen(all_if_none=True)
        if not bots:
            return
        p = filedialog.asksaveasfilename(title="Export bots", defaultextension=".json", parent=self.col_win,
                                         initialfile="my-bot-collection.json", filetypes=[("Bot file", "*.json")])
        if p:
            payload = E.export_bots(bots, self.version)
            for out, b in zip(payload["bots"], bots):
                out.update({k: b[k] for k in E.COLLECT_FIELDS if k in b and k != "team"})
            E.save_bots_file(p, payload)
            self.write(f"Exported {len(bots)} collected bots to {p}")

    def _col_release(self):
        bots = self._col_chosen()
        if not bots:
            return messagebox.showinfo("Release", "Click a card first.", parent=self.col_win)
        names = ", ".join(b.get("nickname", "?") for b in bots[:5]) + ("..." if len(bots) > 5 else "")
        if messagebox.askyesno("Release", f"Release {names}? They leave your collection.", parent=self.col_win):
            snap = self.lib_snapshot()
            E.release([E.bot_key(b) for b in bots])
            self.col_sel = set()
            self.col_refresh()
            self.offer_undo(f"Released {names}.", snap)

    # ---------------- bot detail card ----------------
    @staticmethod
    def on_row(tree, e):
        """True when a double-click landed on a row (not on a column heading, which sorts, or empty space)."""
        return tree.identify_region(e.x, e.y) in ("cell", "tree") and bool(tree.identify_row(e.y))

    def _family_strip(self, parent, v, win):
        """Parents and children as little clickable portraits."""
        box = ttk.LabelFrame(parent, text=" Family (click to open) ", padding=(8, 4))
        box.pack(fill="x", pady=(8, 0))
        alive = {x.uid: x for x in self.tourn.vars}
        kids = [x for x in self.tourn.vars if v.uid in x.parents][:6]
        people = [("parent", u) for u in v.parents] + [("child", x.uid) for x in kids]
        if not people:
            ttk.Label(box, text="A founder: no parents on record, no children yet.", foreground=MUTED).pack(anchor="w")
            return
        for role, uid in people:
            x = alive.get(uid)
            cell = tk.Frame(box, bg=CARD, padx=4, pady=2)
            cell.pack(side="left", padx=(0, 6))
            cv = tk.Canvas(cell, width=38, height=38, bg=CARD, highlightthickness=0)
            cv.pack(side="left")
            if x is not None:
                st = self.tourn.info.get(id(x), {}).get("status", "warming up")
                draw_creature(cv, x.name, 4, 6, 30, self.GRID_STATUS.get(st, ("", "#8b5cf6"))[1], status=st, awake=True,
                              phase=0, tags=())
                name = E.bot_nickname(x.key())
            else:
                cv.create_text(19, 19, text="\u2620", fill=MUTED, font=("", 16))
                name = f"#{uid} (gone)"
            head = "\u25b2 Parent" if role == "parent" else "\u25bc Child"
            lab = tk.Label(cell, text=head + "\n" + name, bg=CARD,
                           fg=TEXT if x is not None else MUTED, justify="left", font=("", 8, "bold"),
                           cursor="hand2" if x is not None else "")
            lab.pack(side="left")
            if x is not None:
                for wd in (cell, cv, lab):
                    wd.bind("<Button-1>", lambda e, x=x: self.bot_detail(x, quiet=True))

    def bot_detail(self, v=None, quiet=False):
        v = v or self.selected_variant()
        if not v or not self.tourn:
            if quiet:
                return None
            return messagebox.showinfo("Bot details", "Click a bot on the farm or the scoreboard first.")
        w = tk.Toplevel(self.root)
        w.title(STRAT_LABELS.get(v.name, v.name))
        w.geometry(f"820x{min(880, self.root.winfo_screenheight() - 80)}")
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        title = friendly_label(v.name, v.params, v.tf, self.session_bar)
        ttk.Label(f, text=("★ " if v.key() in self.hof_keys() else "") + E.bot_nickname(v.key()) + ":  " + title,
                  font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(f, text=STRAT_TIPS.get(v.name, ""), foreground=MUTED, wraplength=720).pack(anchor="w", pady=(2, 8))
        stats = ttk.Frame(f)
        stats.pack(fill="x")
        info_lbls = {}
        for i, key in enumerate(("Status", "Recent gain", "vs holding (recent)", "Lifetime return", "Lifetime vs holding",
                                 "Win rate", "Trades", "Worst drop", "Time winning", "Best market", "Own stops",
                                 "Family", "Traits", "Fees paid", "Health / power", "Never-rules", "Twin test", "Skips coins",
                                 "Badges", "Island", "Backtests")):
            ttk.Label(stats, text=key, foreground=MUTED).grid(row=i // 3, column=(i % 3) * 2, sticky="w", padx=(0, 6), pady=2)
            l = ttk.Label(stats, text="-", font=("", 10, "bold"), wraplength=230)
            l.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=(0, 22), pady=2)
            info_lbls[key] = l
        self._family_strip(f, v, w)
        stack = ttk.LabelFrame(f, text=" Rule stack (each layer evolves on its own) ", padding=(8, 4))
        stack.pack(fill="x", pady=(8, 0))
        stack_lbls = []
        for i in range(5):
            a = ttk.Label(stack, text="", font=("", 9, "bold"), width=19)
            a.grid(row=i, column=0, sticky="nw")
            b = ttk.Label(stack, text="", wraplength=600, justify="left")
            b.grid(row=i, column=1, sticky="w", pady=1)
            stack_lbls.append((a, b))
        crow = ttk.Frame(f)
        crow.pack(fill="x", pady=(10, 2))
        ttk.Label(crow, text="Entries and exits on").pack(side="left")
        coin = tk.StringVar(value=self.session_coins[0] if self.session_coins else "")
        cb = ttk.Combobox(crow, textvariable=coin, values=self.session_coins, state="readonly", width=8)
        cb.pack(side="left", padx=6)
        ttk.Label(crow, text="▲ buy   ▼ sell   (last 300 decisions)", foreground=MUTED).pack(side="left")
        cv = tk.Canvas(f, height=140, bg=CARD, highlightthickness=0)
        cv.pack(fill="both", expand=True)

        def paint(_=None):
            if not w.winfo_exists():
                return
            lf = self.tourn.life(v)
            d = self.tourn.info.get(id(v), {})
            st = STATUS_TEXT.get(d.get("status"), d.get("status", "removed")) if v in self.tourn.vars else "Removed"
            hrs = v.win_steps * self.session_bar / 60
            parents = self.tourn.lineage.get(v.uid, {}).get("parents", [])
            fam = {"starter": "starter bot", "classic": f"famous strategy: {v.classic}", "bred": f"bred from #{' and #'.join(map(str, parents))}",
                   "tweaked": f"tweaked copy of #{parents[0]}" if parents else "tweaked copy"}.get(v.origin, v.origin or "-")
            vals = {"Status": st, "Recent gain": pct_txt(d.get("score")), "vs holding (recent)": pct_txt(d.get("edge")),
                    "Lifetime return": pct_txt(lf["ret"]), "Lifetime vs holding": pct_txt(lf["edge"]),
                    "Win rate": "-" if lf["winrate"] is None else f"{lf['winrate'] * 100:.0f}% ({lf['wins']}W/{lf['losses']}L)",
                    "Trades": str(lf["trades"]), "Worst drop": f"{lf['maxdd'] * 100:.1f}%",
                    "Time winning": f"{hrs:.1f} h" if hrs >= 1 else f"{hrs * 60:.0f} min",
                    "Best market": MOOD_TEXT.get(lf["best_mood"], "not sure yet") if lf["best_mood"] else "not sure yet",
                    "Own stops": E.risk_text(v.risk) or "none", "Family": f"#{v.uid}, {fam}, generation {v.gen}",
                    "Health / power": f"{self.tourn.health(v) * 100:.0f}% health, power {v.power:.1f} / 10",
                    "Never-rules": E.vetoes_text(v.vetoes, v.veto_stats) or "none",
                    "Twin test": "not run yet" if v.twin is None else
                    (f"passed ({v.twin * 100:+.1f}% vs holding on older prices)" if v.twin > 0 else
                     f"failed ({v.twin * 100:+.1f}%): maybe just lucky, counts for less"),
                    "Skips coins": ", ".join(c_ for c_, x in v.coin_pl.items() if x[0] >= 3 and x[1] < -1.5) or "none",
                    "Badges": ", ".join(f"{n}" for n, _ in self.achievements(v, lf)) or "none yet",
                    "Island": f"{v.island + 1} of {int(self.tourn.o.get('islands', 1) or 1)}",
                    "Backtests": ("; ".join(f"{b_['ret']:+.1f}% ({b_['edge']:+.1f}% vs hold, {b_['segments_passed']}/{b_['segments']} parts)"
                                            for b_ in v.backtests[-2:]) if v.backtests else "none saved"),
                    "Traits": (E.traits_text(v.traits) or "none yet") + (
                        "  |  " + next((cl["note"] for cl in E.CLASSICS if cl["title"] == v.classic), "") if v.classic else ""), "Fees paid": f"${v.sim.fees:,.2f}"}
            for k, l in info_lbls.items():
                l.config(text=vals[k])
            for (a, b), (title, txt) in zip(stack_lbls, E.rule_stack(v)):
                a.config(text=title)
                b.config(text=txt)
            cv.delete("all")
            c = coin.get()
            h = self.hist.get(c, [])[-300:]
            W, H = cv.winfo_width(), cv.winfo_height()
            if len(h) < 2 or W < 60:
                cv.create_text(W // 2, H // 2, text="No price history yet", fill=MUTED)
                return
            L = len(self.hist.get(c, []))
            first_n = self.tourn.n - (len(h) - 1)
            marks = [(n - first_n, side, px) for n, cc, side, px in v.log if cc == c and n >= first_n]
            lo, hi = min(h + [m[2] for m in marks]), max(h + [m[2] for m in marks])
            pad = (hi - lo) * 0.08 or hi * 0.001 or 1
            lo, hi = lo - pad, hi + pad
            left = 64

            def X(i):
                return left + i * (W - left - 8) / (len(h) - 1)

            def Y(p):
                return H - 14 - (p - lo) / (hi - lo) * (H - 28)
            for k in range(4):
                p = lo + (hi - lo) * k / 3
                cv.create_line(left, Y(p), W - 8, Y(p), fill=LINE)
                cv.create_text(left - 6, Y(p), text=f"{p:,.2f}" if p >= 1 else f"{p:,.5f}", anchor="e", fill=MUTED, font=("", 8))
            cv.create_line(*[z for i, p in enumerate(h) for z in (X(i), Y(p))], fill=ACCENT, width=2)
            for i, side, px in marks:
                x, y = X(i), Y(px)
                pts = (x, y + 3, x - 6, y + 13, x + 6, y + 13) if side == "buy" else (x, y - 3, x - 6, y - 13, x + 6, y - 13)
                cv.create_polygon(*pts, fill=GREEN if side == "buy" else RED, outline="")
            cv.create_text(W - 10, 4, anchor="ne", fill=MUTED, font=("", 8),
                           text=f"{len(marks)} trades on {c} shown, one bar = {E.fmt_bar(self.session_bar)}")
            _ = L
        cb.bind("<<ComboboxSelected>>", paint)
        cv.bind("<Configure>", paint)
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))

        def export_one():
            p = filedialog.asksaveasfilename(title="Export bot", defaultextension=".json", parent=w,
                                             initialfile=f"{v.name.lower().replace(' ', '-')}-bot.json", filetypes=[("Bot file", "*.json")])
            if p:
                rows = [dict(r, bar_minutes=self.session_bar) for r in self.tourn.board() if r["v"] is v]
                E.save_bots_file(p, E.export_bots(rows, self.version))
                self.write(f"Exported {title} to {p}")

        def to_hof():
            self.promote_variants([v])
            paint()

        def size(d):
            v.size_mult = round(min(max(v.size_mult + d, 0.25), 3.0), 2)
            self.tourn._rate()
            self.refresh_board()
            self.write(f"{title} trade size is now {v.size_mult:.2f}x.")
        ttk.Button(b, text="Export this bot...", command=export_one).pack(side="left")
        ttk.Button(b, text="\u270e Edit rules...", command=lambda: self.edit_rules(v)).pack(side="left", padx=(6, 0))
        ttk.Button(b, text="Backtest...", command=lambda: self.open_backtest(v)).pack(side="left", padx=(6, 0))
        ttk.Button(b, text="Add to Hall of Fame", command=to_hof).pack(side="left", padx=6)
        ttk.Button(b, text="Catch!", style="Accent.TButton", command=lambda: self.catch_rows(
            [r for r in self.tourn.board() if r["v"] is v], "detail card")).pack(side="left", padx=(0, 6))
        ttk.Button(b, text="Size -", command=lambda: size(-0.25)).pack(side="left")
        ttk.Button(b, text="Size +", command=lambda: size(0.25)).pack(side="left", padx=(4, 0))
        ttk.Button(b, text="Show on main chart", command=lambda: (
            self.board.selection_set(str(id(v))) if self.board.exists(str(id(v))) else None,
            self.chart_bot.set(True), self.show_on_chart(coin.get() or "Account value"))).pack(side="left", padx=6)
        ttk.Button(b, text="Close", command=w.destroy).pack(side="right")

        def tick():
            if w.winfo_exists():
                paint()
                w.after(5000, tick)
        w.after(100, tick)

    # ---------------- family tree ----------------
    def open_family(self):
        if not self.tourn:
            return messagebox.showinfo("Family tree", "Start the bot with Wildcard ticked first.")
        w = tk.Toplevel(self.root)
        w.title("Bot family tree")
        w.geometry("900x560")
        ttk.Label(w, padding=(10, 8, 10, 2), wraplength=860, foreground=MUTED, text=(
            "Every bot's family: starters at the top, their tweaked and bred children underneath. A bred bot has two "
            "parents; it is listed under the first and names the second. Bold = alive, ★ = Hall of Fame. "
            "Removed bots are kept for a while so you can see which lines died out.")).pack(anchor="w")
        top = ttk.Frame(w, padding=(10, 0))
        top.pack(fill="x")
        alive_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Only families with living bots", variable=alive_only, command=lambda: fill()).pack(side="left")
        mid = ttk.Frame(w, padding=10)
        mid.pack(fill="both", expand=True)
        t = ttk.Treeview(mid, columns=("status", "ret", "origin", "gen"), show="tree headings")
        sb = ttk.Scrollbar(mid, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.heading("#0", text="Bot")
        t.column("#0", width=430)
        for col, txt, wd in (("status", "Status", 200), ("ret", "Return", 80), ("origin", "How it was made", 140), ("gen", "Gen", 40)):
            t.heading(col, text=txt)
            t.column(col, width=wd, anchor="w" if col in ("status", "origin") else "e")
        t.tag_configure("alive", font=("", 9, "bold"))
        t.tag_configure("dead", foreground=MUTED)

        def fill():
            t.delete(*t.get_children())
            recs = {r["uid"]: r for r in self.tourn.family()}
            kids = {}
            for r in recs.values():
                p = r["parents"][0] if r["parents"] and r["parents"][0] in recs else None
                kids.setdefault(p, []).append(r)

            def has_alive(r):
                return r["alive"] or any(has_alive(k) for k in kids.get(r["uid"], []))

            def add(parent_iid, r):
                if alive_only.get() and not has_alive(r):
                    return
                lab = friendly_label(r["name"], {}, r["tf"], self.session_bar).replace(" ()", "")
                extra = f"  (+ #{r['parents'][1]})" if len(r["parents"]) > 1 else ""
                st = STATUS_TEXT.get(r["status"], r["status"]) if r["alive"] else r["status"]
                iid = t.insert(parent_iid, "end", text=f"#{r['uid']}  " + ("★ " if r.get("inducted") else "") + r["label"] + extra,
                               values=(st, pct_txt(r.get("ret")), r["origin"], r["gen"]),
                               tags=("alive" if r["alive"] else "dead",), open=r["alive"] or has_alive(r))
                _ = lab
                for k in sorted(kids.get(r["uid"], []), key=lambda x: x["uid"]):
                    add(iid, k)
            for r in sorted(kids.get(None, []), key=lambda x: x["uid"]):
                add("", r)
        fill()
        b = ttk.Frame(w, padding=10)
        b.pack(fill="x")
        ttk.Button(b, text="Refresh", command=fill).pack(side="left")
        ttk.Button(b, text="Close", command=w.destroy).pack(side="right")

    # ---------------- Hall of Fame ----------------
    def open_hof(self):
        if getattr(self, "hof_win", None) is not None and self.hof_win.winfo_exists():
            return self.hof_win.lift()
        w = self.hof_win = tk.Toplevel(self.root)
        w.title("Hall of Fame")
        w.geometry("940x520")
        top = ttk.Frame(w, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Hall of Fame", font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(top, foreground=MUTED, wraplength=900, text=(
            "Only the bots YOU pick are here: right-click any bot on the farm, scoreboard or in the Library and choose "
            "'Add to Hall of Fame' (right-click here to remove). Choose 'Bots trading: Hall of Fame only' to trade "
            "with just these, for example on your real account. Past wins don't guarantee future ones.")).pack(anchor="w", pady=(2, 0))
        mid = ttk.Frame(w, padding=(10, 4))
        mid.pack(fill="both", expand=True)
        t = self.make_tree(mid, ("strategy", "edge", "ret", "win", "hours", "best", "stops", "when"),
                           (250, 85, 80, 70, 70, 110, 120, 120),
                           ("Strategy", "vs holding", "Return", "Win rate", "Hrs winning", "Best market", "Own stops", "Added"))
        t.configure(selectmode="extended")
        state = {"bots": []}

        def right(e):
            iid = t.identify_row(e.y)
            if not iid:
                return
            if iid not in t.selection():
                t.selection_set(iid)
            self.entry_menu(e, [state["bots"][int(i)] for i in t.selection()], "hof")
        for btn in ("<Button-3>", "<Button-2>"):
            t.bind(btn, right)

        def fill():
            t.delete(*t.get_children())
            state["bots"] = E.load_hof()
            for i, b in enumerate(state["bots"]):
                lf = b.get("lifetime") or {}

                def p(k):
                    v = lf.get(k)
                    return "-" if v is None else f"{v:+.1f}%"
                bar = lf.get("bar_minutes")
                t.insert("", "end", iid=str(i), values=(
                    friendly_label(b["name"], b["params"], b.get("tf", 1), bar), p("vs_holding_pct"), p("return_pct"),
                    "-" if lf.get("win_rate_pct") is None else f"{lf['win_rate_pct']:.0f}%",
                    "-" if lf.get("hours_winning") is None else f"{lf['hours_winning']:.1f}",
                    MOOD_TEXT.get(lf.get("best_market"), lf.get("best_market") or "-"),
                    E.risk_text(b.get("risk")) or "-", str(b.get("inducted_at", ""))[:16].replace("T", " ")))
            status.config(text=f"{len(state['bots'])} bots  |  {E.hof_path()}")

        def chosen():
            sel = [state["bots"][int(i)] for i in t.selection()]
            return sel or state["bots"]

        def export():
            bots = chosen()
            if not bots:
                return messagebox.showinfo("Hall of Fame", "The Hall of Fame is empty so far.", parent=w)
            p = filedialog.asksaveasfilename(title="Export Hall of Fame bots", defaultextension=".json", parent=w,
                                             initialfile="hall-of-fame-bots.json", filetypes=[("Bot file", "*.json")])
            if p:
                E.save_bots_file(p, E.export_bots(bots, self.version))
                self.write(f"Exported {len(bots)} Hall of Fame bots to {p}")

        def add_run():
            if not self.tourn or not self.seeded:
                return messagebox.showinfo("Hall of Fame", "Start the bot with Wildcard ticked first.", parent=w)
            closes = {c: self.hist[c] for c in self.session_coins}
            have, n = {v.key() for v in self.tourn.vars}, 0
            for b in [state["bots"][int(i)] for i in t.selection()] or state["bots"][:10]:
                if E.bot_key(b) in have:
                    continue
                v = self.tourn.add_variant(b["name"], b["params"], closes, b.get("size", 1.0), note="Hall of Fame:",
                                           tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), vetoes=b.get("vetoes"), layers=b.get("layers"), origin="hall of fame")
                if v:
                    v.inducted, n = True, n + 1
            self.flush_events()
            self.refresh_board()
            self.write(f"Added {n} Hall of Fame bots to this run." if n else "Those bots are already competing.")

        def remove():
            sel = [state["bots"][int(i)] for i in t.selection()]
            if not sel or not messagebox.askyesno("Hall of Fame", f"Remove {len(sel)} bots from the Hall of Fame?", parent=w):
                return
            snap = self.lib_snapshot()
            E.remove_from_hof([E.bot_key(b) for b in sel])
            fill()
            self.offer_undo(f"Removed {len(sel)} bots from the Hall of Fame.", snap)
        b = ttk.Frame(w, padding=10)
        b.pack(fill="x")
        ttk.Button(b, text="Export...", style="Accent.TButton", command=export).pack(side="left")
        ttk.Button(b, text="Add to this run", command=add_run).pack(side="left", padx=6)
        ttk.Button(b, text="Remove", command=remove).pack(side="left")
        ttk.Button(b, text="Catch", command=lambda: self.catch_rows(chosen(), "hall of fame")).pack(side="left", padx=(6, 0))
        ttk.Button(b, text="Refresh", command=fill).pack(side="left", padx=6)
        ttk.Button(b, text="Close", command=w.destroy).pack(side="right")
        status = ttk.Label(b, foreground=MUTED)
        status.pack(side="right", padx=10)
        fill()

    # ---------------- Bot Lab ----------------
    def open_lab(self):
        if getattr(self, "lab_win", None) is not None and self.lab_win.winfo_exists():
            return self.lab_win.lift()
        w = self.lab_win = tk.Toplevel(self.root)
        w.title("Bot Lab")
        w.geometry("1000x640")
        top = ttk.Frame(w, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Bot Lab: breed bots on past prices, then test them on prices they never saw",
                  font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(top, foreground=MUTED, wraplength=960, justify="left", text=(
            "1. Downloads history for your ticked coins.  2. Runs the full Wildcard tournament (tweaking, crossbreeding, "
            "retiring) on the older part.  3. Takes its best bots and replays them, unchanged, on the newer part they never "
            "saw.  A bot passes only if it made money there AND beat just holding. Running several rounds with different "
            "random seeds shows whether a result is real or luck. Fees and spread are included.")).pack(anchor="w", pady=(2, 8))
        opts = ttk.Frame(top)
        opts.pack(fill="x")
        coins = [c for c, v in self.coin_vars.items() if v.get()]
        ttk.Label(opts, text="Coins: " + (", ".join(coins) or "none ticked")).grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Label(opts, text="Candle size").grid(row=0, column=1, sticky="e")
        gran = tk.StringVar(value=self.cfg.get("lab_gran", "15 min"))
        ttk.Combobox(opts, textvariable=gran, values=list(LAB_CANDLES), state="readonly", width=8).grid(row=0, column=2, padx=(4, 14))
        ttk.Label(opts, text="Candles").grid(row=0, column=3, sticky="e")
        count = tk.StringVar(value=str(self.cfg.get("lab_count", 3000)))
        ttk.Combobox(opts, textvariable=count, values=["1000", "2000", "3000", "5000"], width=6).grid(row=0, column=4, padx=(4, 14))
        ttk.Label(opts, text="Rounds").grid(row=0, column=5, sticky="e")
        rounds = tk.StringVar(value=str(self.cfg.get("lab_rounds", 3)))
        ttk.Combobox(opts, textvariable=rounds, values=["1", "2", "3", "5", "8"], width=4).grid(row=0, column=6, padx=(4, 14))
        ttk.Label(opts, text="Learn on").grid(row=0, column=7, sticky="e")
        split = tk.StringVar(value=self.cfg.get("lab_split", "70%"))
        ttk.Combobox(opts, textvariable=split, values=["60%", "70%", "80%"], state="readonly", width=5).grid(row=0, column=8, padx=4)
        span = ttk.Label(top, foreground=MUTED)
        span.pack(anchor="w", pady=(4, 0))

        def show_span(*_):
            try:
                mins = LAB_CANDLES[gran.get()] / 60 * int(count.get())
                frac = int(split.get().rstrip("%")) / 100
                span.config(text=f"About {mins / 1440:.1f} days of history: the bots learn on the first {mins * frac / 1440:.1f} "
                                 f"days and are tested on the last {mins * (1 - frac) / 1440:.1f}.")
            except (ValueError, KeyError):
                span.config(text="")
        for var in (gran, count, split):
            var.trace_add("write", show_span)
        show_span()
        run_row = ttk.Frame(w, padding=(12, 4))
        run_row.pack(fill="x")
        self.lab_btn = ttk.Button(run_row, text="Run the Lab", style="Accent.TButton", command=lambda: start())
        self.lab_btn.pack(side="left")
        self.lab_bar = ttk.Progressbar(run_row, length=300, mode="determinate", maximum=1.0)
        self.lab_bar.pack(side="left", padx=10)
        self.lab_status = ttk.Label(run_row, text="", foreground=MUTED)
        self.lab_status.pack(side="left")
        self.lab_summary = tk.Label(w, text="", bg=BG, fg=TEXT, anchor="w", justify="left", padx=12, font=("", 10, "bold"),
                                    wraplength=960)
        self.lab_summary.pack(fill="x", pady=(4, 2))
        mid = ttk.Frame(w, padding=(12, 0))
        mid.pack(fill="both", expand=True)
        t = self.lab_tree = self.make_tree(
            mid, ("strategy", "pace", "stops", "train", "test", "edge", "dd", "trades", "verdict", "round"),
            (220, 65, 200, 75, 85, 80, 70, 55, 140, 45),
            ("Strategy", "Pace", "Own stops and traits", "Learned on", "Unseen prices", "vs holding", "Worst drop", "Trades", "Verdict", "Round"))
        t.configure(selectmode="extended")
        t.tag_configure("pass", foreground=GREEN)
        t.tag_configure("fail", foreground=MUTED)
        t.tag_configure("meh", foreground="#fbbf24" if DARK else "#b45309")
        self.lab_rows = []
        b = ttk.Frame(w, padding=12)
        b.pack(fill="x")

        def picked(only_passed=True):
            sel = [self.lab_rows[int(i)] for i in t.selection()]
            return sel or [r for r in self.lab_rows if r["passed"] or not only_passed]

        def export():
            rows = picked()
            if not rows:
                return messagebox.showinfo("Bot Lab", "No bots passed. Select rows to export them anyway.", parent=w)
            p = filedialog.asksaveasfilename(title="Export Lab bots", defaultextension=".json", parent=w,
                                             initialfile="lab-passed-bots.json", filetypes=[("Bot file", "*.json")])
            if p:
                E.save_bots_file(p, E.export_bots(rows, self.version, note=self.lab_note))
                self.write(f"Exported {len(rows)} Bot Lab bots to {p}")

        def to_hof():
            rows = picked()
            if not rows:
                return messagebox.showinfo("Bot Lab", "No bots passed. Select rows to add them anyway.", parent=w)
            n = E.induct(E.export_bots(rows, self.version, note=self.lab_note)["bots"], self.version)
            self.write(f"Added {n} Bot Lab bots to the Hall of Fame.", "good")

        def to_run():
            if not self.tourn or not self.seeded:
                return messagebox.showinfo("Bot Lab", "Start the bot with Wildcard ticked first.", parent=w)
            closes = {c: self.hist[c] for c in self.session_coins}
            n = 0
            for r in picked():
                if self.tourn.add_variant(r["name"], r["params"], closes, r.get("size", 1.0), note="From the Bot Lab:",
                                          tf=r["tf"], risk=r.get("risk"), traits=r.get("traits"), vetoes=r.get("vetoes"), layers=r.get("layers"), origin="bot lab"):
                    n += 1
            self.flush_events()
            self.refresh_board()
            self.write(f"Added {n} Bot Lab bots to this run. Their pace counts in your bar length "
                       f"({E.fmt_bar(self.session_bar)}), not the Lab's candle size.")
        ttk.Button(b, text="Export passed...", style="Accent.TButton", command=export).pack(side="left")
        ttk.Button(b, text="Add passed to Hall of Fame", command=to_hof).pack(side="left", padx=6)
        ttk.Button(b, text="Add passed to this run", command=to_run).pack(side="left")
        ttk.Button(b, text="Catch passed", command=lambda: self.catch_rows(picked(), "bot lab") if picked() else
                   messagebox.showinfo("Bot Lab", "No bots passed. Select rows to catch them anyway.", parent=w)).pack(side="left", padx=6)
        ttk.Label(b, text="Select rows to use them instead of the passed ones.", foreground=MUTED).pack(side="left", padx=10)
        ttk.Button(b, text="Close", command=w.destroy).pack(side="right")

        def start():
            if getattr(self, "lab_busy", False):
                return
            cs = [c for c, v in self.coin_vars.items() if v.get()]
            if not cs:
                return messagebox.showinfo("Bot Lab", "Tick at least one coin first.", parent=w)
            try:
                n_c, n_r = int(count.get()), int(rounds.get())
                assert 300 <= n_c <= 8000 and 1 <= n_r <= 10
            except Exception:
                return messagebox.showerror("Bot Lab", "Candles must be 300 to 8000 and rounds 1 to 10.", parent=w)
            self.cfg.update(lab_gran=gran.get(), lab_count=n_c, lab_rounds=n_r, lab_split=split.get())
            E.save_json(CONFIG_PATH, self.cfg)
            frac = int(split.get().rstrip("%")) / 100
            self.lab_busy = True
            self.lab_btn.config(state="disabled", text="Running...")
            t.delete(*t.get_children())
            self.lab_rows = []
            self.lab_summary.config(text="")
            self.lab_note = f"Bot Lab, {', '.join(cs)}, {gran.get()} candles"
            s = dict(self.settings)
            threading.Thread(target=self._lab_work, args=(cs, LAB_CANDLES[gran.get()], n_c, n_r, frac, s, self.cur_quote()),
                             daemon=True).start()

    def _lab_work(self, coins, gran, count, rounds, frac, settings, quote):
        try:
            closes, extra = {}, {}
            for i, c in enumerate(coins):
                self.q.put(("lab", ("progress", (f"Downloading {c} history...", 0.1 * i / len(coins)))))
                d = E.fetch_ohlcv(c, gran, count, quote)
                closes[c], extra[c] = d["c"], dict(h=d["h"], l=d["l"], v=d["v"])
            L = min(len(v) for v in closes.values())
            if L < 300:
                return self.q.put(("lab", ("fail", f"Only {L} candles came back. Try a bigger candle size or fewer coins.")))
            seen, rows, sums = set(), [], []
            for r in range(rounds):
                def prog(stage, d, n, r=r):
                    part = (0.75 if stage == "train" else 0.25) * d / max(n, 1) + (0 if stage == "train" else 0.75)
                    self.q.put(("lab", ("progress", (f"Round {r + 1} of {rounds}: "
                                                     f"{'evolving bots' if stage == 'train' else 'testing on unseen prices'}...",
                                                     0.1 + 0.9 * (r + part) / rounds))))
                res = E.run_lab(closes, settings, train_frac=frac, seed=1000 + r, progress=prog, extra=extra)
                sums.append(res)
                for row in res["rows"]:
                    k = E.bot_key(row)
                    if k in seen:
                        continue
                    seen.add(k)
                    row["round"] = r + 1
                    rows.append(row)
            self.q.put(("lab", ("done", (rows, sums, L, gran))))
        except Exception as e:
            self.q.put(("lab", ("fail", f"{e.__class__.__name__}: {e}")))

    def on_lab_msg(self, kind, data):
        if kind != "progress":
            self.lab_busy = False
        if getattr(self, "lab_win", None) is None or not self.lab_win.winfo_exists():
            if kind == "done":
                rows = data[0]
                self.write(f"Bot Lab finished: {sum(r['passed'] for r in rows)} of {len(rows)} bots passed on unseen prices.")
            return
        if kind == "progress":
            text, frac = data
            self.lab_status.config(text=text)
            self.lab_bar["value"] = frac
            return
        self.lab_btn.config(state="normal", text="Run the Lab again")
        if kind == "fail":
            self.lab_status.config(text=data, foreground=RED)
            self.lab_bar["value"] = 0
            return
        rows, sums, L, gran = data
        self.lab_bar["value"] = 1.0
        rows.sort(key=lambda r: (not r["passed"], -r["test_edge"]))
        self.lab_rows = rows
        t = self.lab_tree
        bar_min = gran / 60
        for i, r in enumerate(rows):
            tag = "pass" if r["passed"] else "meh" if r["verdict"] == "Lost less than holding" else "fail"
            t.insert("", "end", iid=str(i), tags=(tag,), values=(
                friendly_label(r["name"], r["params"]), E.fmt_bar(r["tf"] * bar_min),
                ", ".join(x for x in (E.risk_text(r.get("risk")), E.traits_text(r.get("traits"))) if x) or "-",
                pct_txt(r["train_ret"]), pct_txt(r["test_ret"]), pct_txt(r["test_edge"]), f"{r['test_dd'] * 100:.1f}%",
                r["test_trades"], r["verdict"], r["round"]))
            r["lifetime"] = dict(return_pct=round(r["test_ret"] * 100, 2), vs_holding_pct=round(r["test_edge"] * 100, 2),
                                 worst_drop_pct=round(r["test_dd"] * 100, 2), trades=r["test_trades"],
                                 bar_minutes=bar_min, source="Bot Lab unseen-data test")
        n_pass = sum(r["passed"] for r in rows)
        hold = sum(s["test_hold"] for s in sums) / len(sums)
        wild = sum(s["wildcard"]["ret"] for s in sums) / len(sums)
        days = (L - sums[0]["split"]) * bar_min / 1440
        self.lab_summary.config(
            text=f"{n_pass} of {len(rows)} bots passed.  On the unseen last {days:.1f} days: just holding "
                 f"{hold * 100:+.1f}%, the full Wildcard system {wild * 100:+.1f}% (average of {len(sums)} rounds). "
                 + ("A bot that passes in several rounds is more trustworthy than a one-off."
                    if len(sums) > 1 else "Run more rounds to see if results hold up."),
            fg=GREEN if n_pass else TEXT)
        self.lab_status.config(text="Done.", foreground=MUTED)
        self.write(f"Bot Lab: {n_pass} of {len(rows)} bots passed on unseen prices (holding {hold * 100:+.1f}%).")

    # ---------------- bot picks coins ----------------
    def scout_then_start(self):
        self.write(f"Scouting Coinbase for {self.settings['auto_coins']} busy coins that are moving...")
        self.btn.config(state="disabled")

        def work():
            try:
                picks = E.scout_coins(E.list_products(self.cur_quote()), int(self.settings["auto_coins"]))
                self.q.put(("scouted", (picks, None)))
            except Exception as e:
                self.q.put(("scouted", ([], str(e))))
        threading.Thread(target=work, daemon=True).start()

    def on_scouted(self, picks, err):
        self.btn.config(state="normal")
        if err or not picks:
            self.write(f"Couldn't scout coins ({err or 'nothing liquid enough'}). Using your ticked coins.", "warn")
        else:
            for c, v in self.coin_vars.items():
                v.set(False)
            self.add_coins(picks)
            self.write(f"The bot picked {', '.join(picks)}.", "good")
        self._scouted = True
        self.toggle()

    def show_shortcuts(self):
        self.info_window("Keyboard shortcuts", (
            "Ctrl+Enter    Start / stop the bot\n"
            "Ctrl+L        Bot Lab\n"
            "Ctrl+H        Hall of Fame\n"
            "Ctrl+K        Browse coins\n"
            "Ctrl+B        Bot Library\n"
            "Ctrl+E        Design an egg\n"
            "Ctrl+,        Settings\n"
            "F5            Refresh the Trades tab\n\n"
            "Scoreboard: double-click a strategy for its detail card and chart. Select a strategy and pick a coin in "
            "the chart menu to see that bot's entries and exits on the main chart.\n"
            "Chart: hover for exact values, click the range buttons (30 ticks to All), drag the bar under the chart "
            "to make it taller."))

    # ---------------- settings / export / updates ----------------
    def open_settings(self):
        if getattr(self, "settings_win", None) is not None and self.settings_win.winfo_exists():
            return self.settings_win.lift()
        w = self.settings_win = tk.Toplevel(self.root)
        w.title("Settings")
        w.resizable(False, False)
        outer = ttk.Frame(w, padding=10)
        outer.pack()
        nb = ttk.Notebook(outer)
        nb.pack()
        ents = {}
        for title, blurb, fields in SETTING_GROUPS:
            f = ttk.Frame(nb, padding=10)
            nb.add(f, text=f"  {title}  ")
            ttk.Label(f, text=blurb, foreground=MUTED, wraplength=620).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
            lpos, lr, lc = {}, 1, 0  # Learning tab: switches two per row
            for r, (key, label, typ) in enumerate(fields, start=1):
                if typ is bool:
                    lpos[r] = (lr, lc)
                    lr, lc = (lr + 1, 0) if lc else (lr, 1)
                else:
                    if lc:
                        lr, lc = lr + 1, 0
                    lpos[r] = (lr, 0)
                    lr += 1
            if title != "Learning":
                lpos = {r: (r, 0) for r in range(1, len(fields) + 1)}
            for r, (key, label, typ) in enumerate(fields, start=1):
                r_ = r
                r = lpos[r_][0]
                if typ == "head":
                    hf = tk.Frame(f, bg=BG)
                    hf.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(10 if r > 1 else 0, 2))
                    tk.Label(hf, text=label.upper(), bg=BG, fg=ACCENT, font=("", 8, "bold")).pack(side="left")
                    tk.Frame(hf, bg=LINE, height=1).pack(side="left", fill="x", expand=True, padx=(8, 0), pady=(2, 0))
                    continue
                cur = self.settings.get(key)
                if typ is bool:
                    var = tk.BooleanVar(value=bool(cur))
                    if title == "Learning":
                        ttk.Checkbutton(f, text=label, variable=var).grid(row=r, column=lpos[r_][1], sticky="w", pady=2,
                                                                          padx=(0, 16))
                    else:
                        ttk.Checkbutton(f, text=label, variable=var).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)
                    ents[key] = (var, bool)
                    continue
                ttk.Label(f, text=label, wraplength=360, justify="left").grid(row=r, column=0, sticky="w", pady=3)
                if typ == "bar":
                    var = tk.StringVar(value=BAR_LABELS.get(float(cur), "1 min"))
                    ttk.Combobox(f, textvariable=var, values=list(BAR_LABELS.values()), state="readonly", width=12).grid(
                        row=r, column=1, padx=(10, 0), sticky="w")
                elif typ == "eggs":
                    var = tk.StringVar(value=cur if cur in ("By dragon type", "Purple") else "By dragon type")
                    ttk.Combobox(f, textvariable=var, values=["By dragon type", "Purple"], state="readonly", width=15).grid(
                        row=r, column=1, padx=(10, 0), sticky="w")
                elif typ == "chars":
                    var = tk.StringVar(value=cur if cur in ("Themed dragons", "Classic dragons") else "Themed dragons")
                    ttk.Combobox(f, textvariable=var, values=["Themed dragons", "Classic dragons"], state="readonly", width=15).grid(
                        row=r, column=1, padx=(10, 0), sticky="w")
                elif typ == "anim":
                    var = tk.StringVar(value=cur if cur in ("Full", "Light", "Off") else "Full")
                    ttk.Combobox(f, textvariable=var, values=["Full", "Light", "Off"], state="readonly", width=8).grid(
                        row=r, column=1, padx=(10, 0), sticky="w")
                elif typ == "order":
                    var = tk.StringVar(value=ORDER_LABELS.get(cur, ORDER_LABELS["market"]))
                    ttk.Combobox(f, textvariable=var, values=list(ORDER_LABELS.values()), state="readonly", width=34).grid(
                        row=r, column=1, padx=(10, 0), sticky="w")
                else:
                    var = tk.StringVar(value="" if cur is None else str(cur))
                    ttk.Entry(f, textvariable=var, width=44 if typ is str else 8,
                              show="*" if key == "alert_tg_token" else "").grid(row=r, column=1, padx=(10, 0), sticky="w")
                ents[key] = (var, typ)
            if title == "Learning":
                learn = [k for k, _, t_ in fields if t_ is bool]

                def set_all(on, ents=ents, learn=learn):
                    for k in learn:
                        ents[k][0].set(on if on is not None else bool(E.DEFAULT_SETTINGS.get(k, True)))
                    if on is None:
                        for k in ("islands", "classic_rate"):
                            ents[k][0].set(str(E.DEFAULT_SETTINGS[k]))
                qb = ttk.Frame(f)
                qb.grid(row=lr + 1, column=0, columnspan=2, sticky="w", pady=(12, 0))
                ttk.Button(qb, text="All on", command=lambda: set_all(True)).pack(side="left")
                ttk.Button(qb, text="All off", command=lambda: set_all(False)).pack(side="left", padx=6)
                ttk.Button(qb, text="Recommended", style="Accent.TButton", command=lambda: set_all(None)).pack(side="left")
                ttk.Label(qb, text="  All off = the bots only tweak their numbers and pace.", foreground=MUTED).pack(side="left")
            if title == "Alerts":
                def test(ents=ents):
                    trial = dict(self.settings)
                    for k in ("alert_discord", "alert_tg_token", "alert_tg_chat"):
                        trial[k] = ents[k][0].get().strip()
                    if not E.alerts_configured(trial):
                        return messagebox.showinfo("Alerts", "Fill in a Discord webhook, or a Telegram token and chat ID, first.", parent=w)

                    def work():
                        probs = E.send_alert(trial, "CBBF test alert: alerts are working.")
                        self.q.put(("call", lambda: messagebox.showinfo(
                            "Alerts", "\n".join(probs) if probs else "Sent. Check your phone.", parent=w)))
                    threading.Thread(target=work, daemon=True).start()
                ttk.Button(f, text="Send a test alert", command=test).grid(row=len(fields) + 1, column=0, sticky="w", pady=(10, 0))
            if title == "Orders":
                ttk.Label(f, foreground=MUTED, wraplength=520, justify="left", text=(
                    "Limit orders sit on the order book at the best price (post-only), so they pay the lower maker fee "
                    "and no spread, but they only fill if the market comes to them. Safety stops and Sell everything "
                    "always use market orders so they happen right away.")).grid(row=len(fields) + 1, column=0, columnspan=2,
                                                                                 sticky="w", pady=(10, 0))
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Strategy details  ")
        ttk.Label(f, text="Starting numbers for each strategy. Wildcard tweaks these on its own.",
                  foreground=MUTED).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        pents, items = {}, [(name, k, dv) for name, spec in E.STRATS.items() for k, dv in spec["params"].items()]
        half = (len(items) + 1) // 2
        for i, (name, k, dv) in enumerate(items):
            r, col = 1 + i % half, 0 if i < half else 2
            ttk.Label(f, text=f"{STRAT_LABELS[name]}: {PARAM_NAMES.get(k, k)}").grid(row=r, column=col, sticky="w", pady=1,
                                                                                    padx=(0 if col == 0 else 18, 0))
            e = ttk.Entry(f, width=7)
            e.insert(0, str(self.settings["params"][name][k]))
            e.grid(row=r, column=col + 1, padx=(8, 0))
            pents[(name, k)] = (e, type(dv))
        misc = ttk.Frame(outer)
        misc.pack(fill="x", pady=(8, 0))
        notify = tk.BooleanVar(value=bool(self.settings["notify"]))
        ttk.Checkbutton(misc, text="Notifications (important events)", variable=notify).pack(side="left")
        ntr = tk.BooleanVar(value=bool(self.settings.get("notify_trades", False)))
        ttk.Checkbutton(misc, text="...also every trade", variable=ntr).pack(side="left", padx=(8, 0))
        snd = tk.BooleanVar(value=bool(self.settings.get("chime", True)))
        ttk.Checkbutton(misc, text="CBBF sounds", variable=snd).pack(side="left", padx=(8, 0))
        vol = tk.IntVar(value=int(self.settings.get("chime_volume", 60)))
        ttk.Scale(misc, from_=5, to=100, variable=vol, length=80).pack(side="left", padx=(4, 0))
        sb = ttk.Button(misc, text="\u25b6", width=3, command=lambda: E.play_sound(
            ("trade", "notify", "hof", "problem")[getattr(self, "_demo", 0) % 4], vol.get()) or
            setattr(self, "_demo", getattr(self, "_demo", 0) + 1))
        sb.pack(side="left", padx=(4, 0))
        Tip(sb, "Hear the sounds: each click plays the next one (trade, notice, Hall of Fame, problem).")
        srow = ttk.Frame(outer)
        srow.pack(fill="x", pady=(4, 0))
        ttk.Label(srow, text="Play a sound for:", foreground=MUTED).pack(side="left")
        off = set(self.settings.get("sounds_off") or [])
        snd_vars = {}
        for kind, txt in (("trade", "trades (blip)"), ("notify", "notices (chime)"), ("hof", "Hall of Fame (fanfare)"),
                          ("problem", "problems (low tone)")):
            snd_vars[kind] = tk.BooleanVar(value=kind not in off)
            ttk.Checkbutton(srow, text=txt, variable=snd_vars[kind]).pack(side="left", padx=(8, 0))
            ttk.Button(srow, text="\u25b6", width=2, command=lambda k=kind: E.play_sound(k, vol.get())).pack(side="left")
        keep = tk.BooleanVar(value=bool(self.settings.get("keep_awake", True)))
        ttk.Checkbutton(misc, text="Keep the computer awake while the bot runs", variable=keep).pack(side="left", padx=12)

        def save():
            try:
                new = {}
                for k, (var, typ) in ents.items():
                    v = var.get()
                    if typ is bool:
                        new[k] = bool(v)
                    elif typ == "bar":
                        new[k] = next(b for b, l in BAR_LABELS.items() if l == v)
                    elif typ == "order":
                        new[k] = next(o for o, l in ORDER_LABELS.items() if l == v)
                    elif typ is str or typ in ("anim", "chars", "eggs"):
                        new[k] = v.strip()
                    else:
                        new[k] = typ(float(v)) if typ is int else float(v)
                params = {n: dict(p) for n, p in self.settings["params"].items()}
                for (n, k), (e, typ) in pents.items():
                    params[n][k] = typ(float(e.get())) if typ is int else float(e.get())
                    lo, hi = E.STRATS[n]["bounds"][k]
                    assert lo <= params[n][k] <= hi
                assert 0 < new["trade_pct"] <= 100 and new["refresh"] >= 1 and new["evolve_every"] >= 1 and 0 < new["max_coin_pct"] <= 100
                assert new["bar_minutes"] in BAR_CHOICES and min(new["stop_loss"], new["take_profit"], new["daily_loss"]) >= 0
                assert 1.0 <= new["winner_boost"] <= 3.0 and new["add_every"] >= 1 and new["slow_every"] >= 1 and new["fail_drop"] >= 0
                assert 14 <= new["max_bots"] <= 200 and new["retire_hours"] >= 0 and 0 <= new["auto_coins"] <= 15
                assert 1 <= new["islands"] <= 6 and new["grad_hours"] >= 0 and 0 <= new["classic_rate"] <= 100
                assert new["trade_hours"] == "" or parse_hours(new["trade_hours"]) is not None
                assert new["hof_hours"] > 0 and new["hof_min_trades"] >= 0 and new["max_trades_hour"] >= 0
                assert 0 <= new["trail_stop"] < 100 and 0 <= new["slippage"] < 5 and 0 <= new["maker_fee_pct"] < 5
                assert 3 <= new["limit_wait"] <= 600 and 0 <= new["fee_pct"] < 5
                for n, p in params.items():
                    assert E.fix_params(n, dict(p)) == p
            except Exception:
                return messagebox.showerror("Settings", "One of the numbers isn't valid. Reminders: islands 1 to 6; trading "
                                                        "hours like 9-17 or 22-6 (or empty); winner trade size must be "
                                                        "between 1 and 3; farm size must be 14 to 200; the bot can pick 0 to 15 "
                                                        "coins; limit wait is 3 to 600 seconds; percentages can't be negative; "
                                                        "strategy numbers must stay in their normal range, short averages below "
                                                        "long ones and buy levels well below sell levels.", parent=w)
            if self.session and new["bar_minutes"] != self.session_bar:
                messagebox.showinfo("Settings", "The new decision speed starts after you reset the session (File > Reset session).", parent=w)
            if new["bar_minutes"] < 1 and float(self.settings["bar_minutes"]) >= 1:
                messagebox.showinfo("Fast bars", "Short decision speeds mean many more trades. Each round trip costs about "
                                                 f"{2 * E.cost_model(dict(self.settings, **new))[0] * 100:.1f}% in fees, so "
                                                 "a move has to beat that to make money. Consider Limit orders and the "
                                                 "trades-per-hour brake (Settings > Orders).", parent=w)
            self.settings.update(new)
            self.settings["params"], self.settings["notify"] = params, notify.get()
            self.settings["keep_awake"] = keep.get()
            self.settings["notify_trades"], self.settings["chime"] = ntr.get(), snd.get()
            self.settings["chime_volume"] = int(vol.get())
            self.settings["sounds_off"] = [k for k, v in snd_vars.items() if not v.get()]
            CHARACTER_MODE["mode"] = self.settings.get("characters", "Themed dragons")
            self.grid_refresh()
            self.apply_awake()
            self.auto_coin_var.set(int(self.settings.get("auto_coins", 0)) > 0)
            match = [n for n, p in E.STYLES.items() if all(abs(self.settings[k] - v) < 1e-9 for k, v in p.items())]
            self.settings["style"] = match[0] if match else "Custom"
            self.style_var.set(self.settings["style"])
            if self.tourn:
                self.tourn.o.update(E.tournament_opts(self.settings))
                self.tourn.cap = int(self.settings["max_bots"])
                self.tourn.family_cap = max(5, -(-self.tourn.cap // 4))
                self.tourn.retire_steps = self.retire_steps(self.session_bar)
            self.cfg["settings"] = self.settings
            E.save_json(CONFIG_PATH, self.cfg)
            self.write("Settings saved.")
            w.destroy()
        row = ttk.Frame(outer)
        row.pack(anchor="e", pady=(10, 0))
        ttk.Button(row, text="Cancel", command=w.destroy).pack(side="left", padx=6)
        ttk.Button(row, text="Save", style="Accent.TButton", command=save).pack(side="left")

    def open_data_folder(self):
        os.makedirs(E.HOME_DIR, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(E.HOME_DIR)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", E.HOME_DIR])
        except Exception:
            messagebox.showinfo("Data folder", E.HOME_DIR)

    def export_csv(self):
        if not self.trades and not self.shadow_trades:
            return messagebox.showinfo("Export", "No trades yet.")
        rows = list(self.trades)
        if self.shadow_trades and messagebox.askyesno(
                "Export", f"Also include the bots' {len(self.shadow_trades)} test trades?\n\n"
                          "They are marked BOT in the mode column and never touched your account."):
            rows = sorted(rows + self.shadow_trades, key=lambda r: str(r.get("time", "")))
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="trades.csv")
        if p:
            E.export_trades_csv(p, rows)
            self.write(f"Exported {len(rows)} trades to {p}")

    def check_update(self, silent=False):
        if getattr(self, "_upd_busy", False):
            return
        self._upd_busy = True
        if not silent:
            self.write("Checking GitHub for a newer version...")

        def work():
            try:
                base = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_BRANCH}"
                remote = E.http_get(f"{base}/VERSION", 10, retries=1).decode().strip()
                notes = ""
                try:
                    notes = E.changelog_since(E.http_get(f"{base}/CHANGELOG.md", 10).decode("utf-8", "replace"), self.version)
                except Exception:
                    pass
                self.q.put(("update", ("found" if E.version_tuple(remote) > E.version_tuple(self.version) else "none",
                                       (remote, notes, silent))))
            except Exception as e:
                self.q.put(("update", ("error", (str(e), silent))))
        threading.Thread(target=work, daemon=True).start()

    def auto_check_update(self):
        if time.time() - float(self.cfg.get("last_update_check", 0)) > 12 * 3600:
            self.cfg["last_update_check"] = time.time()
            try:
                E.save_json(CONFIG_PATH, self.cfg)
            except OSError:
                pass
            self.check_update(silent=True)

    def on_update_msg(self, kind, data):
        self._upd_busy = False
        if kind in ("error", "none") and not getattr(self, "update_info", None):
            self.upd_btn.config(text="\u27f3 Check for updates")
        if kind == "error":
            msg, silent = data
            if not silent:
                messagebox.showerror("Update", f"Couldn't check for updates: {msg}")
            return
        if kind == "none":
            if not data[2]:
                messagebox.showinfo("Update", f"You have the latest version ({self.version}).")
            return
        if kind == "found":
            remote, notes, silent = data
            self.update_info = (remote, notes)
            self.upd_btn.config(text=f"\u2b06 Update to {remote}", bg=GREEN, fg="white", activebackground=GREEN,
                                activeforeground="white")
            self.write(f"Version {remote} is available. Click 'Update' at the top to see what's new.", "good")
            if not silent:
                self.show_update()
            return
        if kind == "installed":
            written, backup = data
            self.upd_btn.config(text="\u27f3 Restart to finish updating", bg=ACCENT, fg="white")
            self.write(f"Updated {len(written)} files. The old version is kept in {backup}.", "good")
            if messagebox.askyesno("Update installed", "The update is installed. Restart CBBF now to use it?\n\n"
                                                       "(Your session is saved first. Tools > Undo last update goes back.)"):
                self.restart_app()
            return
        if kind == "install_fail":
            messagebox.showerror("Update", f"The update didn't install, nothing was changed: {data}")

    def update_clicked(self):
        if getattr(self, "update_info", None):
            return self.show_update()
        self.upd_btn.config(text="\u27f3 Checking...")
        self.check_update()

    def show_update(self):
        if not getattr(self, "update_info", None):
            return self.check_update()
        remote, notes = self.update_info
        w = tk.Toplevel(self.root)
        w.title("Update available")
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"CBBF {remote} is available", font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(f, text=f"You have {self.version}. Here is what's new:", foreground=MUTED).pack(anchor="w", pady=(2, 6))
        t = tk.Text(f, width=86, height=18, wrap="word", relief="flat", padx=8, pady=6)
        t.insert("1.0", notes or "(No release notes found.)")
        t.config(state="disabled")
        t.pack(fill="both", expand=True)
        ttk.Label(f, foreground=MUTED, wraplength=640, text=(
            f"Downloads from github.com/{UPDATE_REPO}. Your settings, sessions, trades, saved bots and login are not "
            "touched. The current version is backed up first, and Tools > Undo last update puts it back.")).pack(anchor="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(10, 0))

        def accept():
            if self.running and not messagebox.askyesno("Update", "The bot is running. It keeps running while the update "
                                                                  "downloads, and stops when the app restarts. Continue?", parent=w):
                return
            w.destroy()
            self.write("Downloading the update...")

            def work():
                try:
                    data = E.http_get(f"https://github.com/{UPDATE_REPO}/archive/refs/heads/{UPDATE_BRANCH}.zip", 60, retries=1)
                    self.q.put(("update", ("installed", E.install_update(data, HERE, self.version))))
                except Exception as e:
                    self.q.put(("update", ("install_fail", str(e))))
            threading.Thread(target=work, daemon=True).start()
        ttk.Button(b, text="Accept and install", style="Accent.TButton", command=accept).pack(side="right")
        ttk.Button(b, text="Later", command=w.destroy).pack(side="right", padx=6)

    def undo_update(self):
        bk = E.latest_backup(HERE)
        if not bk:
            return messagebox.showinfo("Undo update", "There is no earlier version saved.")
        if not messagebox.askyesno("Undo update", f"Put back the version saved in\n{bk}?\n\nThe app restarts afterwards."):
            return
        try:
            E.restore_backup(HERE, bk)
            shutil.move(bk, bk + "-restored")
        except Exception as e:
            return messagebox.showerror("Undo update", f"Couldn't restore: {e}")
        self.restart_app()

    def restart_app(self):
        self.running = False
        self.awake.off()
        try:
            self.save_state()
            E.save_json(CONFIG_PATH, self.cfg)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable, os.path.join(HERE, "main.py")])


def _report_crash():
    import traceback
    text = traceback.format_exc()
    path = os.path.join(E.HOME_DIR, "crash.log")
    try:
        os.makedirs(E.HOME_DIR, exist_ok=True)
        with open(path, "a") as f:
            f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ---\n{text}")
    except OSError:
        path = "(could not save a log file)"
    try:
        messagebox.showerror("CBBF hit an error", f"Something went wrong:\n\n{text[-900:]}\n\nSaved to: {path}")
    except Exception:
        pass
    _stay_open(f"{text}\nThis was also saved to: {path}")


if __name__ == "__main__":
    try:
        root = tk.Tk()
        root.report_callback_exception = lambda *a: __import__("traceback").print_exception(*a)
        App(root)
        root.mainloop()
    except SystemExit:
        raise
    except BaseException:
        _report_crash()
