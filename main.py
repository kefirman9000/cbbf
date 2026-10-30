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
    ("Basics", "Your account's money and safety rules. All % are of the money you started this session with "
               "(the 'Practice money' / 'Money to trade with' box). Not sure? Pick a Risk style on the main screen "
               "instead (Cautious, Balanced, Adventurous): it fills these five in for you. With Wildcard and "
               "'Let the winning bots set stops' (Orders tab) on, the bots may use their own learned Safety stop and "
               "Lock in profit; these numbers are then the fallback.", [
        ("trade_pct", "Trade size: each buy spends this % of your starting money. Example: 10 with $1,000 = $100 per buy "
                      "(winning bots can scale this up to their trade-size multiplier)", float),
        ("max_coin_pct", "Limit per coin: never have more than this % of your starting money in any single coin. "
                         "Example: 50 with $1,000 = at most $500 in BTC", float),
        ("stop_loss", "Safety stop: automatically sell a coin if it falls this % below the price you paid. "
                      "Example: 5 = sell if a $100 position drops to $95 (0 = off)", float),
        ("take_profit", "Lock in profit: automatically sell a coin once it is up this % on what you paid. "
                        "Example: 10 = sell when a $100 position reaches $110 (0 = off)", float),
        ("daily_loss", "Daily loss limit: stop trading for the rest of the day if the account falls this % since "
                       "midnight. Open coins are kept, not sold (0 = off)", float),
    ]),
    ("Wildcard", "How the bot treats strategies that are winning, shaky or failing.", [
        ("beat_hold", "A strategy only counts as Winning if it also beats just holding the coins", bool),
        ("winner_boost", "Biggest trade size a top winner can earn (1 = normal, 2.5 = 2.5 times)", float),
        ("add_every", "While a winner is holding a coin, add more every N decisions", int),
        ("slow_every", "Shaky strategies may only trade every N decisions", int),
        ("fail_drop", "Stop a strategy at once if it falls this % from its peak (0 = off)", float),
        ("evolve_every", "Review and replace strategies every N decisions", int),
        ("max_bots", "Most strategies competing at once (14 to 50)", int),
        ("retire_hours", "Replace a strategy that hasn't been winning for this many hours (0 = never)", float),
        ("crossbreed", "New strategies can mix the settings of two winners (crossbreeding)", bool),
        ("regime_aware", "Favour strategies that do well in the current market mood (trending, choppy...)", bool),
        ("auto_coins", "Let the bot pick this many coins when Wildcard starts (0 = use the ticked coins)", int),
    ]),
    ("Hall of Fame", "Long-term winners are saved automatically so you keep the bots that really work.", [
        ("hof_hours", "Hours a strategy must spend Winning (in total) to get in", float),
        ("hof_min_trades", "...and at least this many finished (sold) trades", int),
        ("hof_load", "Bring the 10 best Hall of Fame bots back each time Wildcard starts", bool),
        ("auto_catch", "Hall of Fame bots also go into your Bot Collection automatically", bool),
        ("team_locked", "Trading with my team: keep exactly those bots (untick to let the team breed new bots)", bool),
    ]),
    ("Orders", "How orders are placed, and the extra costs Practice mode pretends to pay.", [
        ("order_type", "Order type", "order"),
        ("limit_wait", "Limit orders: seconds to wait for a fill", int),
        ("limit_fallback", "Limit orders: if nothing fills in time, use a market order instead", bool),
        ("trail_stop", "Trailing stop: sell if a coin falls this % from its high since buying (0 = off)", float),
        ("max_trades_hour", "Brake: at most this many strategy trades per hour (0 = no limit). Safety stops always run", int),
        ("bot_stops", "Wildcard: let the winning bots set the Safety stop and Lock-in-profit levels they learned "
                      "(your Basics numbers are used when they don't)", bool),
        ("fee_pct", "Market order fee used in Practice mode (%)", float),
        ("maker_fee_pct", "Limit order (maker) fee used in Practice mode (%)", float),
        ("slippage", "Spread and slippage on market orders in Practice mode (%)", float),
    ]),
    ("Timing", "How often the bot looks at prices.", [
        ("bar_minutes", "How often strategies decide (15 sec is the fastest)", "bar"),
        ("refresh", "Check prices every N seconds (1 or more)", int),
    ]),
    ("Alerts", "Get a message on your phone when something happens. Leave blank to turn off.", [
        ("alert_discord", "Discord webhook link (Server settings > Integrations > Webhooks)", str),
        ("alert_tg_token", "Telegram bot token (from @BotFather)", str),
        ("alert_tg_chat", "Telegram chat ID", str),
        ("alert_trades", "Alert on every trade the account makes", bool),
        ("alert_hof", "Alert when a bot joins the Hall of Fame", bool),
        ("alert_problems", "Alert on problems (daily limit hit, order failures, prices down)", bool),
    ]),
]
SETTING_FIELDS = [f for _, _, fields in SETTING_GROUPS for f in fields]
STATUS_TEXT = {"active": "Winning (following)", "slowed": "Shaky (slowed)", "standby": "Good (on standby)",
               "benched": "Stopped", "warming up": "Learning..."}
STATUS_TAG = {"active": "win", "slowed": "shaky", "standby": "standby", "benched": "stopped", "warming up": "learning"}
ORDER_LABELS = {"market": "Market (fills now, full fee)", "limit": "Limit / maker (lower fee, may not fill)"}
STRAT_LABELS = {
    "SMA crossover": "Trend follower", "RSI": "Buy dips / sell peaks", "Momentum": "Ride the rise",
    "Bollinger bands": "Buy low / sell high", "Dollar-cost averaging": "Steady buyer (DCA)",
    "Breakout": "Breakout chaser", "MACD": "Momentum shift", "Mean reversion": "Snap-back",
    "EMA crossover": "Quick trend (EMA)", "Stochastic": "Range bounce", "Trend + dip": "Dips in an uptrend",
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
    "ATR channel": "Buys a push above the average plus a few typical bar ranges (from highs and lows), sells a drop "
                   "below it.",
}
PARAM_NAMES = {"fast": "short average", "slow": "long average", "period": "lookback", "lo": "buy below",
               "hi": "sell above", "n": "lookback", "thr": "move needed %", "k": "band width", "every": "buy every N decisions",
               "signal": "signal smoothing", "min_gap": "smallest gap %", "trend": "trend lookback",
               "vmult": "volume x normal", "m": "range multiple"}
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


def draw_dragon(c, x, y, size, color, awake=True, phase=0, mood="plain", fire=False, flash=False, gold=False, tags=()):
    """A small dragon on a Tk canvas, inside a size x size box at (x, y). Awake dragons flap their wings
    and look around; sleeping ones curl up with their eyes shut. fire = a little puff of flame."""
    k = size / 26.0

    def mk(kind, *a, **kw):
        return getattr(c, "create_" + kind)(*a, tags=tags, **kw)

    def P(*pts):
        return [v * k + (x if i % 2 == 0 else y) for i, v in enumerate(pts)]
    belly = "#fde68a" if gold else "#f5f5f4"
    edge = "white" if flash else ""
    flap = (math.sin(phase / 1.6) * 5) if awake else 0
    # tail
    mk('line', *P(6, 20, 1, 22, -1, 17, 1, 14), fill=color, width=max(1, 2 * k), smooth=True)
    mk('polygon', *P(1, 14, -2, 12, 2, 11), fill=color, outline="")
    # far wing (flaps)
    if awake:
        mk('polygon', *P(9, 12, 3, 2 - flap, 8, 5 - flap / 2, 14, 10), fill=color, outline=edge, stipple="gray75")
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
    mk('line', *P(18 + look, 4 + hy, 17 + look, 0 + hy), fill=belly, width=max(1, 1.5 * k))
    mk('line', *P(21 + look, 3 + hy, 21 + look, -1 + hy), fill=belly, width=max(1, 1.5 * k))
    # eye
    if awake:
        mk('oval', *P(21 + look, 5 + hy, 23.5 + look, 7.5 + hy), fill="white", outline="")
        mk('oval', *P(22 + look, 5.6 + hy, 23.3 + look, 7 + hy), fill="#111827", outline="")
    else:
        mk('line', *P(20.5, 6.5 + hy, 23.5, 6.5 + hy), fill="#111827", width=max(1, 1.2 * k))
    if mood == "happy" and awake:
        mk('line', *P(23 + look, 9 + hy, 25 + look, 8.5 + hy), fill="#111827", width=1)
    # near wing
    if awake:
        mk('polygon', *P(10, 13, 6, 3 + flap, 12, 6 + flap / 2, 16, 12), fill=color, outline="#111827", width=1)
    if fire:
        f = (phase % 3)
        mk('polygon', *P(26 + look, 7 + hy, 31 + f, 5 + hy, 30 + f, 7 + hy, 33 + f, 8 + hy, 26 + look, 9 + hy),
                         fill="#f97316", outline="#fde047")


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
        root.after(1500, self.maybe_setup)
        root.after(4000, self.auto_check_update)
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
        self.hx = {}              # coin -> {'h','l','v'} lined up with self.hist (highs, lows, volumes per bar)
        self.bar_hl = {}          # coin -> [high, low] of the ticks inside the bar being built
        self.cur_vol = {}         # coin -> latest volume per bar from Coinbase candles
        self.tick_px = {}         # coin -> deque of (time, price) for the tick chart
        self.brake_note = 0.0

    def save_state(self):
        if not self.session:
            return
        E.save_json(E.state_path(self.live), dict(
            start_cash=self.start_cash, cash=self.cash, pos=self.pos, cost=self.cost,
            trades=self.trades[-5000:], shadow=self.shadow_trades[-1000:], curve=self.curve[-1500:], bench_units=self.bench_units,
            coins=self.session_coins, bar_n=self.bar_n, bar=self.session_bar, quote=self.quote, high=self.high))

    def load_state(self, s):
        self.start_cash, self.cash = s["start_cash"], s["cash"]
        self.pos, self.cost = s["pos"], s["cost"]
        self.trades, self.curve = s["trades"], [tuple(x) for x in s["curve"]]
        self.shadow_trades = [r for r in s.get("shadow", []) if isinstance(r, dict)]
        self.bench_units, self.session_coins = s["bench_units"], s["coins"]
        self.bar_n, self.session_bar = s["bar_n"], s.get("bar", 5)
        self.quote = s.get("quote", "USD")
        self.high = {c: float(v) for c, v in (s.get("high") or {}).items() if isinstance(v, (int, float))}

    def on_close(self):
        if self.running and not messagebox.askyesno(
                "Quit", "The bot is running. Quitting stops it (open positions stay open).\n\nQuit anyway?"):
            return
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

        header = tk.Frame(root, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        header.pack(fill="x")
        tk.Label(header, text="CBBF", bg=CARD, fg=TEXT, font=("", 14, "bold")).pack(side="left", padx=14, pady=10)
        self.mode_pill = tk.Label(header, text="PAPER", bg=GREEN, fg="white", font=("", 9, "bold"), padx=10, pady=2)
        self.mode_pill.pack(side="left")
        self.theme_btn = tk.Button(header, text="Light mode" if DARK else "Dark mode", command=lambda: self.set_dark(not DARK),
                                   relief="flat", bd=0, bg=LINE, fg=TEXT, activebackground=LINE, activeforeground=TEXT,
                                   padx=10, pady=3, cursor="hand2")
        self.theme_btn.pack(side="right", padx=(0, 14))
        self.mute_btn = tk.Button(header, text="\U0001F515" if self.cfg.get("muted") else "\U0001F514", command=self.toggle_mute,
                                  relief="flat", bd=0, bg=CARD, fg=TEXT, activebackground=CARD, cursor="hand2", font=("", 11))
        self.mute_btn.pack(side="right", padx=(0, 8))
        Tip(self.mute_btn, "Mute or unmute all notifications. Sounds are off unless you turn them on in Settings.")
        self.upd_btn = tk.Button(header, text="Update", command=self.show_update, relief="flat", bd=0, bg=GREEN,
                                 fg="white", activebackground=GREEN, activeforeground="white", padx=10, pady=3,
                                 cursor="hand2", font=("", 9, "bold"))
        self.conn_lbl = tk.Label(header, text="Not logged in", bg=CARD, fg=MUTED)
        self.conn_lbl.pack(side="right", padx=14)
        self.net_lbl = tk.Label(header, text="\u25cf", bg=CARD, fg=MUTED, font=("", 11))
        self.net_lbl.pack(side="right")
        self.net_tip = Tip(self.net_lbl, "Price connection: not started yet.")
        self.tick_lbl = tk.Label(header, text="", bg=CARD, fg=MUTED)
        self.tick_lbl.pack(side="right", padx=14)
        self.err_lbl = tk.Label(header, text="", bg=CARD, fg=RED, cursor="hand2", font=("", 9, "bold"))
        self.err_lbl.bind("<Button-1>", lambda e: self.show_diagnostics())

        self.headline_lbl = tk.Label(root, text="", bg="#1e3a5f" if DARK else "#eff6ff", fg="#bfdbfe" if DARK else "#1e3a8a", anchor="w", justify="left",
                                     padx=14, pady=7, wraplength=1000, font=("", 10))
        self.headline_lbl.pack(fill="x")

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        self.left_scroll = ScrollFrame(body, BG, width=262)
        self.left_scroll.pack(side="left", fill="y", padx=(0, 10))
        left = self.left_scroll.inner
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # --- account card
        f, c = self.card(left, "ACCOUNT")
        f.pack(fill="x", pady=(0, 8))
        self.mode_var = tk.StringVar(value="paper")
        seg = tk.Frame(c, bg=CARD)
        seg.pack(fill="x")
        r1 = ttk.Radiobutton(seg, text="Practice", variable=self.mode_var, value="paper", style="Seg.TRadiobutton",
                             command=self.on_mode_change)
        r1.pack(side="left", expand=True, fill="x")
        r2 = ttk.Radiobutton(seg, text="Live", variable=self.mode_var, value="live", style="Seg.TRadiobutton",
                             command=self.on_mode_change)
        r2.pack(side="left", expand=True, fill="x")
        Tip(r1, "Practice uses pretend money. Nothing real happens, so it's completely safe.")
        Tip(r2, "Live trades REAL money on your Coinbase account. Needs a login.")
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
        self.team_var = tk.BooleanVar(value=bool(self.cfg.get("team_mode", False)) and bool(E.team()))
        self.team_cb = ttk.Checkbutton(c, text="Trade with my team", variable=self.team_var, style="Card.TCheckbutton",
                                       command=self.toggle_team_mode)
        self.team_cb.pack(anchor="w", padx=(18, 0))
        Tip(self.team_cb, "Wildcard runs only the bots on your team from the Bot Collection (Ctrl+B). "
                          "Settings > Wildcard decides whether the team may breed new bots or stays exactly as picked.")
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

        self.btn = ttk.Button(left, text="Start the bot", style="Accent.TButton", command=self.toggle)
        self.btn.pack(fill="x", pady=(2, 4))
        ttk.Button(left, text="Sell everything now", style="Plain.TButton", command=self.sell_all).pack(fill="x")
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
            lbl = tk.Label(c, text="-", bg=CARD, fg=TEXT, font=("", 15, "bold"))
            lbl.pack(anchor="w")
            self.tiles[key] = lbl
            Tip(lbl, {"equity": "Cash plus the current value of every coin you own.",
                      "pl": "How much you've made or lost since you started.",
                      "cash": "Money not currently invested in a coin.",
                      "hold": "How far the bot is ahead of (or behind) what you'd have made by buying once and doing nothing.",
                      "day": "How much your total value has changed since today began."}[key])
        self.verdict_lbl = tk.Label(right, text="", bg=BG, fg=MUTED, anchor="w", font=("", 10, "bold"))
        self.verdict_lbl.pack(fill="x", pady=(6, 0))

        split = tk.PanedWindow(right, orient="vertical", bg=BG, sashwidth=6, bd=0, sashrelief="flat")
        split.pack(fill="both", expand=True, pady=(8, 0))
        f, c = self.card(split, None)
        self.build_chart_controls(c)
        charts = tk.Frame(c, bg=CARD)
        charts.pack(fill="both", expand=True)
        self.chart = tk.Canvas(charts, height=190, bg=CARD, highlightthickness=0)
        self.chart2 = tk.Canvas(charts, height=190, bg=CARD, highlightthickness=0)
        self.chart.pack(side="left", fill="both", expand=True)
        for cv in (self.chart, self.chart2):
            cv.bind("<Configure>", lambda e: self.draw_chart())
            cv.bind("<Motion>", lambda e, cv=cv: self.chart_hover(e, cv))
            cv.bind("<Leave>", lambda e: (self.hover.clear(), self.draw_chart()))
        self.apply_split()
        split.add(f, minsize=150, height=int(self.cfg.get("chart_h", 250)), stretch="never")
        nbf = tk.Frame(split, bg=BG)
        split.add(nbf, minsize=180, stretch="always")
        self.split = split
        nb = self.nb = ttk.Notebook(nbf)
        nb.pack(fill="both", expand=True, pady=(8, 0))
        t0, t1, t2, t3, t4, t5 = (ttk.Frame(nb) for _ in range(6))
        nb.add(t0, text="  \U0001F409 Bot Farm  ")
        nb.add(t1, text="  Portfolio  ")
        nb.add(t4, text="  Live prices  ")
        nb.add(t5, text="  Trades  ")
        nb.add(t2, text="  Strategy scoreboard  ")
        nb.add(t3, text="  What the bot did  ")
        self.build_grid_tab(t0)
        self.build_portfolio(t1)
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
                ("Collection...", self.open_collection, "Your caught bots, their rarities and levels, and your team. (Ctrl+B)"),
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
        self.board.bind("<Double-1>", lambda e: self.bot_detail())
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
        self.board.bind("<<TreeviewSelect>>", lambda e: (self.draw_chart(), self.grid_refresh()))
        self.trade_tree = self.make_tree(
            t5, ("time", "mode", "side", "coin", "qty", "usd", "price", "pl", "who", "why"),
            (125, 55, 55, 55, 105, 80, 90, 80, 200, 170),
            ("Time", "Mode", "Action", "Coin", "Coins moved", "Value ($)", "Price", "Profit / loss", "Strategy behind it", "Why"))
        self.trade_tree.tag_configure("buy", foreground=GREEN)
        self.trade_tree.tag_configure("sell", foreground=RED)
        self.trade_tree.tag_configure("bot", foreground=MUTED)
        self.show_sizes()
        self.toggle_wild()
        self.refresh_trades()
        self.update_status()
        self.refresh_view()
        for seq, fn in (("<Control-Return>", self.toggle), ("<Control-l>", self.open_lab), ("<Control-h>", self.open_hof),
                        ("<Control-k>", self.open_coin_browser), ("<Control-b>", self.open_collection), ("<Control-comma>", self.open_settings),
                        ("<F5>", self.refresh_trades)):
            root.bind_all(seq, lambda e, f=fn: (f(), "break")[1])

    def build_menu(self):
        bar = tk.Menu(self.root)
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label="Export trades CSV...", command=self.export_csv)
        m.add_command(label="Export bots...", command=self.export_bots)
        m.add_command(label="Import bots...", command=self.import_bots)
        m.add_command(label="Open data folder", command=self.open_data_folder)
        m.add_command(label="Reset session", command=self.reset)
        m.add_separator()
        m.add_checkbutton(label="Dark mode", variable=self.dark_var, command=lambda: self.set_dark(self.dark_var.get()))
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
        m.add_command(label="Bot Collection...", command=self.open_collection, accelerator="Ctrl+B")
        m.add_command(label="Browse Coinbase coins...", command=self.open_coin_browser, accelerator="Ctrl+K")
        m.add_command(label="Settings...", command=self.open_settings, accelerator="Ctrl+,")
        m.add_command(label="Install add-ons (setup)...", command=self.open_setup)
        m.add_command(label="Sell everything now", command=self.sell_all)
        m.add_separator()
        m.add_command(label="Check for updates", command=self.check_update)
        m.add_command(label="Undo last update", command=self.undo_update)
        bar.add_cascade(label="Tools", menu=m)
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label="Welcome screen", command=self.show_welcome)
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
        st.map("Stop.TButton", background=[("active", "#b91c1c")])
        st.configure("Plain.TButton", padding=5)
        st.configure("Treeview", foreground=TEXT)
        st.configure("Treeview.Heading", foreground=TEXT)
        st.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        st.configure("TNotebook.Tab", background=LINE, foreground=TEXT)
        st.map("TNotebook.Tab", background=[("selected", CARD)])
        st.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT)
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
        if not self.tourn:
            return messagebox.showinfo("Export bots", "Start the bot with Wildcard ticked first.")
        chosen = set(id(v) for v in self.selected_variants())
        rows = self.tourn.board()
        if chosen:
            rows = [r for r in rows if id(r["v"]) in chosen]
        else:
            rows = [r for r in rows if r["status"] != "benched"] or rows
        if not rows:
            return messagebox.showinfo("Export bots", "There are no strategies to export yet.")
        p = filedialog.asksaveasfilename(title="Export bots", defaultextension=".json", initialfile="my-bots.json",
                                         filetypes=[("Bot file", "*.json")])
        if not p:
            return
        try:
            version = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")).read().strip()
        except Exception:
            version = ""
        rows = [dict(r, bar_minutes=self.session_bar) for r in rows]
        E.save_bots_file(p, E.export_bots(rows, version))
        self.write(f"Exported {len(rows)} bot{'s' if len(rows) != 1 else ''} to {p}")

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
                                          risk=d.get("risk"), traits=d.get("traits"), origin="imported") is None:
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
                                   risk=d.get("risk"), traits=d.get("traits"), origin="saved")
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
                t.insert("", "end", tags=("bot",) if is_bot else ("buy" if side == "buy" else "sell",), values=(
                    str(r.get("time", "")).replace("T", " "), str(r.get("mode", "")).upper(),
                    side.upper() if side in ("buy", "sell") else str(r.get("side", "")).upper(),
                    r.get("coin", ""),
                    f"{number(r.get('qty')):.6f}".rstrip("0").rstrip("."),
                    f"{number(r.get('usd')):,.2f}", f"{number(r.get('price')):,.2f}",
                    "" if pl in ("", None) else f"{number(pl):+,.2f}", r.get("who", ""),
                    REASON_TEXT.get(r.get("reason"), r.get("reason", ""))))
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
        t.bind("<Double-1>", lambda e: add_sel())

    def ui_running(self, on):
        self.root.after(50, self.grid_refresh)
        self.btn.config(text="Stop the bot" if on else "Start the bot", style="Stop.TButton" if on else "Accent.TButton")
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
        self.mode_pill.config(text=("LIVE" if live else "PAPER") + ("  ●  RUNNING" if self.running else ""),
                              bg=RED if live else GREEN)
        if self.logging_in:
            self.conn_lbl.config(text="Logging in...", fg=MUTED)
        elif self.client:
            self.conn_lbl.config(text=f"Coinbase connected  |  {self.cur_quote()} available ${self.avail():,.2f}", fg=GREEN)
        else:
            self.conn_lbl.config(text="Not logged in" + ("  (needed for Live)" if live else ""), fg=MUTED)
        self.login_btn.config(text="Log out" if self.client else "Log in to Coinbase")
        self.budget_lbl.config(text=f"Money to trade with ({self.cur_quote()})" if live else f"Practice money (pretend {self.cur_quote()})")

    def on_mode_change(self):
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

    def toast(self, title, msg):
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
            w.after(6000, w.destroy)
        except Exception:
            pass

    def toggle_mute(self):
        self.cfg["muted"] = not self.cfg.get("muted", False)
        E.save_json(CONFIG_PATH, self.cfg)
        self.mute_btn.config(text="\U0001F515" if self.cfg["muted"] else "\U0001F514")
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
                    ("4 hr", 14400), ("1 day", 86400), ("All", 0))
    CHART_TYPES = ("Line", "Area", "Candles")
    CANDLE_SECS = (5, 10, 15, 30, 60, 120, 300, 900, 1800, 3600, 7200, 14400)

    def build_chart_controls(self, parent):
        bar = tk.Frame(parent, bg=CARD)
        bar.pack(fill="x", pady=(0, 4))
        self.chart_view = tk.StringVar(value="Account value")
        self.view_box = ttk.Combobox(bar, textvariable=self.chart_view, width=14, state="readonly",
                                     values=["Account value"])
        self.view_box.pack(side="left")
        self.view_box.bind("<<ComboboxSelected>>", lambda e: self.draw_chart())
        Tip(self.view_box, "What the chart shows: your account's value, or one coin's price with buy/sell markers. "
                           "With Split on, the account stays on the left and this picks the coin on the right.")
        self.chart_type = tk.StringVar(value=self.cfg.get("chart_type", "Line"))
        tb = ttk.Combobox(bar, textvariable=self.chart_type, values=self.CHART_TYPES, width=8, state="readonly")
        tb.pack(side="left", padx=(6, 0))
        tb.bind("<<ComboboxSelected>>", lambda e: (self.cfg.__setitem__("chart_type", self.chart_type.get()), self.draw_chart()))
        Tip(tb, "Line, Area (filled) or Candles. Candles are for coin prices; each candle's size follows the time range.")
        self.chart_range = tk.StringVar(value=self.cfg.get("chart_range", "All"))
        self.range_btns = {}
        rb = tk.Frame(bar, bg=CARD)
        rb.pack(side="left", padx=8)
        for label, _ in self.CHART_RANGES:
            b = tk.Label(rb, text=label, bg=CARD, fg=MUTED, padx=5, pady=1, cursor="hand2", font=("", 8))
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, l=label: self.set_range(l))
            self.range_btns[label] = b
        self.chart_split = tk.BooleanVar(value=bool(self.cfg.get("chart_split", False)))
        self.chart_pct = tk.BooleanVar(value=bool(self.cfg.get("chart_pct", False)))
        self.chart_hold = tk.BooleanVar(value=bool(self.cfg.get("chart_hold", True)))
        self.chart_marks = tk.BooleanVar(value=bool(self.cfg.get("chart_marks", True)))
        self.chart_bot = tk.BooleanVar(value=bool(self.cfg.get("chart_bot", True)))
        self.chart_grid = tk.BooleanVar(value=bool(self.cfg.get("chart_grid", True)))
        for var, txt, tip in ((self.chart_grid, "Grid", "Background grid lines."),
                              (self.chart_bot, "Bot trades",
                               "On a coin chart: the buys (hollow up arrows) and sells (hollow down arrows) of the "
                               "bot selected in the farm or scoreboard, or the best one if none is selected."),
                              (self.chart_marks, "My trades", "Filled arrows where your account bought and sold."),
                              (self.chart_hold, "Holding", "The grey line on the account chart: what just holding would be worth."),
                              (self.chart_pct, "%", "Show change in % instead of dollars."),
                              (self.chart_split, "Split", "Two charts side by side: your account and a coin.")):
            cb = ttk.Checkbutton(bar, text=txt, variable=var, style="Card.TCheckbutton", command=self.chart_opts_changed)
            cb.pack(side="right", padx=(5, 0))
            Tip(cb, tip)
        self.hover = {}
        self.paint_range_btns()

    def apply_split(self):
        if self.chart_split.get():
            self.chart2.pack(side="left", fill="both", expand=True, padx=(8, 0))
            if self.chart_view.get() == "Account value":
                coins = self.session_coins or [c for c, v in self.coin_vars.items() if v.get()]
                if coins:
                    self.chart_view.set(coins[0])
        else:
            self.chart2.pack_forget()

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
                     ("chart_bot", self.chart_bot), ("chart_grid", self.chart_grid), ("chart_split", self.chart_split)):
            self.cfg[k] = bool(v.get())
        self.apply_split()
        self.draw_chart()

    def update_view_choices(self):
        coins = list(self.session_coins or [c for c, v in self.coin_vars.items() if v.get()])
        vals = coins if self.chart_split.get() else ["Account value"] + coins
        if list(self.view_box.cget("values")) != vals:
            self.view_box.config(values=vals)
        if self.chart_view.get() not in vals:
            self.chart_view.set(vals[0] if vals else "Account value")

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
        """-> (series [(name, [(t, y)], color, width)], markers [(t, y, side, filled, label)], is_money, coin or None)"""
        series, marks = [], []
        if view == "Account value" or view not in (self.session_coins or []):
            if view != "Account value" and view in self.coin_vars:  # not running yet: show its history if any
                pass
            pts = self.curve
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
            return series, marks, True, None
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
        return series, marks, False, c

    def draw_chart(self):
        try:
            self.update_view_choices()
        except Exception:
            pass
        if self.chart_split.get():
            self.draw_one(self.chart, "Account value")
            self.draw_one(self.chart2, self.chart_view.get())
        else:
            self.draw_one(self.chart, self.chart_view.get())

    def draw_one(self, c, view):
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        try:
            series, marks, money, coin = self.chart_data(view)
        except Exception:
            series, marks, money, coin = [], [], True, None
        main = series[-1][1] if series else []
        if len(main) < 2 or w < 80:
            c.create_text(w // 2, h // 2, text="The chart fills in once prices start coming in", fill=MUTED)
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
        pct = self.chart_pct.get()
        kind = self.chart_type.get() if coin else ("Area" if self.chart_type.get() == "Area" else "Line")
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
            step = min(self.CANDLE_SECS, key=lambda s: abs(s - span / 70))
            step = max(step, self.session_bar * 60 if len(main_seg) and (main_seg[-1][0] - main_seg[0][0]) / max(len(main_seg), 1) >= self.session_bar * 50 else 0)
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
        left, right, top, bot = 64, 10, 18, 18

        def X(t):
            return left + (t - t0) / (t1 - t0) * (w - left - right)

        def Y(v):
            return h - bot - (v - lo) / (hi - lo) * (h - top - bot)

        def fmt(v):
            if pct:
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
                    lines.append(f"{name}: {fmt(v) if pct else ('$' if money or coin else '') + fmt(v)}")
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
        self.root.title(f"CBBF - {'RUNNING ' if self.running else ''}{pl:+,.2f}{nxt}" if self.session else "CBBF")
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
        self.refresh_prices()
        self.draw_chart()
        self.headline_lbl.config(text=self.headline())

    def refresh_board(self):
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
        for r in self.tourn.board():
            sc = "-" if r["score"] is None else f"{r['score'] * 100:+.1f}%"
            cons = "-" if r["cons"] is None else f"{int(r['cons'] * 100)}%"
            size = f"{r['stake']:.2f}x".replace(".00x", "x") if r["status"] in ("active", "slowed") else "-"
            status = STATUS_TEXT.get(r["status"], r["status"])
            if r["status"] == "benched" and r.get("failing"):
                status = "Stopped (dropped fast)"
            elif r["status"] == "slowed" and r.get("behind"):
                status = "Shaky (not beating holding)"
            lf = r["life"]
            star = "\u2605 " if r["v"].inducted else ""
            tags = (STATUS_TAG.get(r["status"], "learning"),) + (("hof",) if r["v"].inducted else ())
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
            self.ui_running(False)
            self.save_state()
            if self.session:
                pl, hold = self.equity() - self.start_cash, self.bench_equity() - self.start_cash
                self.write(f"Stopped. Profit/loss {pl:+,.2f} (just holding would be {hold:+,.2f}).")
            return
        if (not self.session and self.wild_var.get() and int(self.settings.get("auto_coins", 0)) > 0
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
        if self.halted_day == date.today():
            if not messagebox.askyesno("Daily limit", "The daily max-loss limit was hit today. Resume anyway?"):
                return
            self.halted_day, self.day_eq = None, self.equity()
        self.wild = self.wild_var.get()
        self.active = [n for n, v in self.strat_vars.items() if v.get()]
        if self.wild and self.tourn is None:
            self.seeded = False
        if self.session and self.live and not self.client:
            return messagebox.showerror("Live mode", "Log in to Coinbase first.")
        self.running = True
        self.ui_running(True)
        self.write(f"Started ({'LIVE' if self.live else 'paper'}, {'wildcard' if self.wild else ', '.join(self.active)}, "
                   f"{E.fmt_bar(self.session_bar)} bars, {self.settings.get('order_type', 'market')} orders).")
        self.worker_args = (list(coins), self.session_bar, self.wild, self.settings["fee_pct"] / 100,
                            self.settings["trade_pct"] / 100, self.settings["evolve_every"])
        self.launch_worker()

    def begin_session(self, coins):
        live = self.mode_var.get() == "live"
        saved = E.load_json(E.state_path(live))
        resume = False
        if saved:
            ans = messagebox.askyesnocancel(
                "Resume session?",
                f"A saved {'live' if live else 'paper'} session exists (cash ${saved['cash']:,.2f}, "
                f"{len(saved['trades'])} trades).\n\nYes = resume it\nNo = start fresh\nCancel = abort")
            if ans is None:
                return False
            resume = ans
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
            return False
        self.session = True
        self.save_state()
        # Refresh immediately when starting or resuming, before the first price-feed tick.
        self.refresh_view()
        return True

    def reset(self):
        if self.trades or self.pos:
            msg = "Clear this session and its saved state?"
            if self.live and any(q > 0 for q in self.pos.values()):
                msg += "\n\nLive positions the app bought will stay on your Coinbase account but won't be tracked."
            if not messagebox.askyesno("Reset", msg):
                return
        self.running = False
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
                d = E.fetch_ohlcv(c, gran, 600, self.quote)
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
        if wild:
            tourn = E.Tournament(coins, self.start_cash, fee, pct, evolve, params=self.settings["params"],
                                 opts=E.tournament_opts(self.settings), cap=int(self.settings["max_bots"]),
                                 retire_steps=self.retire_steps(bar_min))
            if L > E.WARM:
                tourn.warmup(hist, extra=hx)
            squad = E.team() if self.cfg.get("team_mode") else []
            if squad:
                for v in list(tourn.vars):
                    tourn._remove(v, "benched for your team")
                for b in squad:
                    tourn.add_variant(b["name"], b["params"], hist, b.get("size", 1.0), note=f"Team: {b.get('nickname')}",
                                      tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), origin="team")
                if self.settings.get("team_locked", True):
                    tourn.retire_steps, tourn.evolve_every = 0, 10 ** 9
                self.q.put(("log", (f"Trading with your team: {', '.join(b.get('nickname', '?') for b in squad)}"
                                    + (" (locked)." if self.settings.get("team_locked", True) else " (they may breed)."), "good")))
            saved = [] if squad else E.load_strategies()
            for d in saved:
                tourn.add_variant(d["name"], d["params"], hist, d.get("size", 1.0), note="Loaded saved", tf=d.get("tf", 1),
                                  risk=d.get("risk"), traits=d.get("traits"), origin="saved")
            if saved:
                self.q.put(("log", f"Loaded {len(saved)} saved strategies into the tournament."))
            if self.settings.get("hof_load", True) and not squad:
                have = {v.key() for v in tourn.vars}
                hof = [b for b in E.load_hof() if E.bot_key(b) not in have][:10]
                for b in hof:
                    v = tourn.add_variant(b["name"], b["params"], hist, b.get("size", 1.0), note="Hall of Fame:",
                                          tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), origin="hall of fame")
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
        self.check_daily()
        self.refresh_view()

    def on_bar(self, prices):
        self.bar_n += 1
        keep = min(6000, max(1000, int(1440 / self.session_bar) + 20))
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
        if self.wild and self.tourn:
            self.tourn.step({c: self.hist[c] for c in self.session_coins}, {c: self.hx[c] for c in self.session_coins})
            self.collect_shadow()
            self.check_hof()
            for e in self.tourn.pop_events():
                self.write("Wildcard: " + friendly_text(e[0].lower() + e[1:]))
            self.update_wild_msg()
            self.refresh_board()
        for c in self.session_coins:
            if c in self.pending:
                continue
            mult, who = 1.0, []
            if self.wild and self.tourn:
                sig, mult = self.tourn.vote(c)
                if sig:
                    add = self.tourn.kind.get(c) == "add"
                    pool = self.tourn.active if add else self.tourn.active + self.tourn.slowed
                    who = [v.name for v in pool
                           if (self.tourn._holding(v, c) if add else v.sig.get(c) == sig)]
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
            if sig:
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
        self.save_state()

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

    def check_hof(self):
        """Saves long-term winners to the Hall of Fame."""
        try:
            need = int(float(self.settings.get("hof_hours", 6)) * 60 / max(self.session_bar, 0.25))
            cands = self.tourn.hall_of_fame_candidates(max(1, need), int(self.settings.get("hof_min_trades", 4)))
            if not cands:
                return
            rows = [dict(r, bar_minutes=self.session_bar) for r in self.tourn.board() if r["v"] in cands]
            entries = E.export_bots(rows, self.version, note=f"Hall of Fame, {', '.join(self.session_coins)}")["bots"]
            new = E.induct(entries, self.version)
            if self.settings.get("auto_catch", True):
                self.catch_rows(entries, "hall of fame")
            for v in cands:
                v.inducted = True
                rec = self.tourn.lineage.get(v.uid)
                if rec:
                    rec["inducted"] = True
                lf = self.tourn.life(v)
                msg = (f"Hall of Fame: {friendly_label(v.name, v.params, v.tf, self.session_bar)} made "
                       f"{lf['ret'] * 100:+.1f}% ({pct_txt(lf['edge'])} vs holding) and was saved.")
                self.write(msg, "good")
                self.alert("hof", msg)
            if new:
                self.notify("CBBF", f"{new} bot{'s' if new != 1 else ''} joined the Hall of Fame", sound="hof")
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
            del self.shadow_trades[:-2000]
            if new:
                self.refresh_trades()
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
        pl = proceeds - self.cost.get(c, 0)
        self.cash += proceeds
        self.pos[c], self.cost[c] = 0, 0
        self.log_trade("sell", c, qty, px, proceeds, pl, reason)

    def log_trade(self, side, c, qty, px, usd, pl, reason):
        row = dict(time=datetime.now().isoformat(timespec="seconds"), mode="live" if self.live else "paper",
                   side=side, coin=c, qty=round(qty, 8), price=round(px, 6), usd=round(usd, 2),
                   pl="" if pl is None else round(pl, 2), reason=reason, who=self.pending_who.pop(c, ""))
        self.trades.append(row)
        if side == "buy":
            self.high[c] = max(self.high.get(c, px), px)
        elif self.pos.get(c, 0) <= 0:
            self.high.pop(c, None)
        try:
            E.append_trade_csv(row)
        except OSError:
            pass
        self.save_state()  # cash and positions have already changed, so persist them before any screen code runs
        # Each screen update is isolated: one failing widget must not hide the trade or freeze the balances.
        for step, name in ((self.refresh_trades, "updating the Trades tab"),
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
        ans = simpledialog.askstring(
            "Confirm LIVE trading",
            f"This will place REAL market orders using up to ${self.cash:,.2f}.\n"
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

    # ---------------- Bot Farm grid (default tab) ----------------
    GRID_STATUS = {"active": ("WINNING", "#22c55e"), "slowed": ("SHAKY", "#f59e0b"), "standby": ("STANDBY", "#3b82f6"),
                   "benched": ("RESTING", "#6b7280"), "warming up": ("LEARNING", "#8b5cf6")}

    def build_grid_tab(self, parent):
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", padx=6, pady=(6, 2))
        self.grid_title = tk.Label(top, text="Bot Farm", bg=BG, fg=TEXT, font=("", 12, "bold"))
        self.grid_title.pack(side="left")
        self.grid_sum = tk.Label(top, text="", bg=BG, fg=MUTED, font=("", 9))
        self.grid_sum.pack(side="left", padx=12)
        right = tk.Frame(top, bg=BG)
        right.pack(side="right")
        self.grid_filter = tk.StringVar(value=self.cfg.get("grid_filter", "All"))
        self.grid_chips = {}
        for name in ("All", "Working", "Winning", "Sleeping", "Caught"):
            l = tk.Label(right, text=name, bg=CARD, fg=MUTED, padx=10, pady=2, cursor="hand2", font=("", 8, "bold"))
            l.pack(side="left", padx=2)
            l.bind("<Button-1>", lambda e, n=name: self._grid_set_filter(n))
            self.grid_chips[name] = l
        ttk.Button(right, text="Catch!", style="Accent.TButton", command=self.catch_selected).pack(side="left", padx=(10, 0))
        self.grid = tk.Canvas(parent, bg=BG, highlightthickness=0)
        ys = ttk.Scrollbar(parent, orient="vertical", command=self.grid.yview)
        self.grid.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.grid.pack(fill="both", expand=True, padx=(6, 0), pady=(2, 4))
        self.grid.bind("<Configure>", lambda e: self.grid_refresh())
        self.grid.bind("<Button-1>", self._grid_click)
        self.grid.bind("<Double-1>", lambda e: (self._grid_click(e), self.bot_detail()))
        self.grid.bind("<Motion>", self._grid_motion)
        self.grid.bind("<Leave>", lambda e: setattr(self, "grid_hover", None))
        for ev, d in (("<Button-4>", -1), ("<Button-5>", 1)):
            self.grid.bind(ev, lambda e, d=d: self.grid.yview_scroll(d, "units"))
        self.grid.bind("<MouseWheel>", lambda e: self.grid.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.grid_cards, self.grid_hover, self.grid_sig = [], None, None
        self._grid_paint_chips()
        self.root.after(250, self.grid_animate)

    def _grid_set_filter(self, n):
        self.grid_filter.set(n)
        self.cfg["grid_filter"] = n
        self._grid_paint_chips()
        self.grid_sig = None
        self.grid.yview_moveto(0)
        self.grid_refresh()

    def _grid_paint_chips(self):
        for n, l in self.grid_chips.items():
            on = n == self.grid_filter.get()
            l.config(bg=ACCENT if on else CARD, fg="white" if on else MUTED)

    def _grid_rows(self):
        if not self.tourn:
            return []
        awake = self.running and self.seeded
        rows = self.tourn.board()
        f = self.grid_filter.get()
        if f == "Working":
            rows = [r for r in rows if awake and r["status"] != "benched"]
        elif f == "Winning":
            rows = [r for r in rows if r["status"] == "active"]
        elif f == "Sleeping":
            rows = [r for r in rows if not awake or r["status"] == "benched"]
        elif f == "Caught":
            have = {E.bot_key(b) for b in E.load_collection()}
            rows = [r for r in rows if r["v"].key() in have]
        return rows

    @staticmethod
    def round_rect(c, x0, y0, x1, y1, r=12, **kw):
        pts = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
               x0, y1 - r, x0, y0 + r, x0, y0)
        return c.create_polygon(*pts, smooth=True, **kw)

    def grid_refresh(self):
        """Draws the cards (the still parts). The dragons are animated separately by grid_animate."""
        if not hasattr(self, "grid"):
            return
        c = self.grid
        W = max(c.winfo_width(), 200)
        rows = self._grid_rows()
        c.delete("card")
        self.grid_cards = []
        awake = self.running and self.seeded
        if self.tourn:
            st = [r["status"] for r in self.tourn.board()]
            asleep = len(st) if not awake else st.count("benched")
            self.grid_sum.config(text=f"{len(st)} dragons  |  {st.count('active')} winning  |  {st.count('slowed')} shaky  |  "
                                      f"{len(st) - asleep} working  |  {asleep} asleep"
                                      + (f"  |  generation {max(v.gen for v in self.tourn.vars)}" if self.tourn.vars else ""))
        else:
            self.grid_sum.config(text="")
        if not rows:
            msg = ("Your dragons hatch here when the bot runs with 'Let the bot choose (Wildcard)' ticked.\n"
                   "Press Start the bot. Each dragon is one trading bot." if not self.tourn else "No dragons match this filter.")
            c.create_text(W // 2, 90, text=msg, fill=MUTED, justify="center", tags="card", font=("", 10))
            c.configure(scrollregion=(0, 0, W, 200))
            return
        cw, ch, g = 236, 150, 12
        per = max(1, (W - g) // (cw + g))
        caught = {E.bot_key(b): b for b in E.load_collection()}
        sel = set(self.board.selection())
        for i, r in enumerate(rows):
            v, lf = r["v"], r["life"]
            x = g + (i % per) * (cw + g)
            y = g + (i // per) * (ch + g)
            label, col = self.GRID_STATUS.get(r["status"], ("?", MUTED))
            if not awake:
                label, col = "ASLEEP", "#6b7280"
            rec = E.lifetime_record(lf, self.session_bar)
            rar = E.rarity_for(rec)
            rc = E.RARITY_COLORS[rar]
            xp = E.xp_for(rec)
            lvl = E.level_for(xp)
            lo_xp, hi_xp = 10 * (lvl - 1) ** 2, 10 * lvl ** 2
            is_sel = str(id(v)) in sel
            self.round_rect(c, x, y, x + cw, y + ch, 14, fill=CARD, outline=TEXT if is_sel else rc,
                            width=3 if is_sel else (2 if rar != "Common" else 1), tags="card")
            self.round_rect(c, x + 3, y + 3, x + 76, y + 76, 10, fill=BG, outline="", tags="card")
            # status pill
            c.create_rectangle(x + cw - 84, y + 10, x + cw - 10, y + 28, fill=col, outline="", tags="card")
            c.create_text(x + cw - 47, y + 19, text=label, fill="white", font=("", 8, "bold"), tags="card")
            nick = E.bot_nickname(v.key())
            star = "★ " if v.inducted else ""
            ball = " ◉" if v.key() in caught else ""
            c.create_text(x + 84, y + 40, anchor="w", text=star + nick + ball, fill=TEXT, font=("", 10, "bold"), tags="card")
            c.create_text(x + 84, y + 57, anchor="w", text=STRAT_LABELS.get(v.name, v.name), fill=MUTED, font=("", 8), tags="card")
            pace = E.fmt_bar(v.tf * self.session_bar)
            c.create_text(x + 84, y + 71, anchor="w", text=f"every {pace}  |  {rar}", fill=rc, font=("", 8, "bold"), tags="card")
            c.create_text(x + 10, y + 15, anchor="w", text=f"LV {lvl}", fill=rc, font=("", 9, "bold"), tags="card")
            # XP and health bars
            frac = (xp - lo_xp) / max(1, hi_xp - lo_xp)
            dd = self.tourn.drawdown(v) if v.eq else 0
            hp = max(0.0, 1 - dd * 10)
            for j, (name, f, bc) in enumerate((("XP", frac, "#a855f7"), ("HP", hp, GREEN if hp > 0.5 else "#f59e0b" if hp > 0.2 else RED))):
                by = y + 88 + j * 13
                c.create_text(x + 10, by + 4, anchor="w", text=name, fill=MUTED, font=("", 7, "bold"), tags="card")
                self.round_rect(c, x + 30, by, x + cw - 10, by + 8, 4, fill=LINE, outline="", tags="card")
                if f > 0.02:
                    self.round_rect(c, x + 30, by, x + 30 + (cw - 40) * min(f, 1), by + 8, 4, fill=bc, outline="", tags="card")
            # stat chips
            chips = [(pct_txt(r["score"]), GREEN if (r["score"] or 0) > 0 else RED if r["score"] else MUTED),
                     ("vs hold " + pct_txt(r.get("edge")), GREEN if (r.get("edge") or 0) > 0 else MUTED),
                     ("WR " + ("-" if lf["winrate"] is None else f"{lf['winrate'] * 100:.0f}%"), TEXT),
                     (f"{lf['trades']} trades", MUTED)]
            cx = x + 10
            for txt, tc in chips:
                t = c.create_text(cx + 5, y + ch - 17, anchor="w", text=txt, fill=tc, font=("", 7, "bold"), tags="card")
                bb = c.bbox(t)
                if bb[2] > x + cw - 8:  # no room left on this card
                    c.delete(t)
                    break
                bgc = self.round_rect(c, bb[0] - 5, bb[1] - 2, bb[2] + 5, bb[3] + 2, 6, fill=BG, outline="", tags="card")
                c.tag_lower(bgc, t)
                cx = bb[2] + 9
            self.grid_cards.append((x, y, x + cw, y + ch, v, r["status"]))
        nrows = (len(rows) + per - 1) // per
        c.configure(scrollregion=(0, 0, W, nrows * (ch + g) + g))

    def grid_animate(self):
        try:
            self._grid_anim_frame()
        except Exception:
            pass
        self.root.after(160, self.grid_animate)

    def _grid_anim_frame(self):
        c = self.grid
        c.delete("anim")
        if not c.winfo_ismapped() or not self.grid_cards:
            return
        self.farm_phase = getattr(self, "farm_phase", 0) + 1
        ph = self.farm_phase
        top, bottom = c.canvasy(0), c.canvasy(c.winfo_height())
        awake = self.running and self.seeded
        for i, (x0, y0, x1, y1, v, st) in enumerate(self.grid_cards):
            if y1 < top or y0 > bottom:
                continue  # off screen: skip
            label, col = self.GRID_STATUS.get(st, ("?", MUTED))
            working = awake and st != "benched"
            just = awake and bool(v.log) and v.log[-1][0] == (self.tourn.n if self.tourn else -1)
            bob = 2 * math.sin((ph + i * 3) / 2.4) if working else 0
            draw_dragon(c, x0 + 14, y0 + 22 + bob, 50, col if awake else "#6b7280", awake=working, phase=ph + i * 3,
                        mood="happy" if st == "active" else "plain", fire=just and v.log[-1][2] == "buy",
                        flash=just and ph % 2 == 0, tags="anim")
            if not working:
                k = (ph + i * 5) % 18
                if k < 12:
                    c.create_text(x0 + 66 + k / 3, y0 + 26 - k, text="z" if k < 6 else "Z", fill=MUTED,
                                  font=("", 8 + k // 6, "bold"), tags="anim")
            elif just:
                side = v.log[-1][2]
                c.create_text(x0 + 66, y0 + 24, text="BUY!" if side == "buy" else "SELL!", tags="anim",
                              fill=GREEN if side == "buy" else "#f59e0b", font=("", 8, "bold"))
        hv = self.grid_hover
        if hv is not None:
            x0, y0, x1, y1 = hv
            self.round_rect(c, x0 - 2, y0 - 2, x1 + 2, y1 + 2, 15, fill="", outline=ACCENT, width=2, tags="anim")

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
        if not card:
            return
        iid = str(id(card[4]))
        if self.board.exists(iid):
            if e.state & 0x0004 or e.state & 0x0001:  # ctrl/shift: several (for Catch! or Export)
                cur = set(self.board.selection())
                cur ^= {iid}
                self.board.selection_set(list(cur))
            else:
                self.board.selection_set(iid)
        self.grid_refresh()
        self.draw_chart()

    # ---------------- portfolio tab ----------------
    def build_portfolio(self, parent):
        top = tk.Frame(parent, bg=CARD)
        top.pack(fill="x", padx=6, pady=(6, 2))
        self.pf_stats = {}
        for i, (k, t) in enumerate((("value", "Total value"), ("invested", "In coins"), ("cash", "Cash"),
                                    ("unreal", "Open profit / loss"), ("real", "Closed profit / loss"), ("n", "Positions"))):
            f = tk.Frame(top, bg=CARD)
            f.grid(row=0, column=i, sticky="w", padx=(0, 22))
            tk.Label(f, text=t.upper(), bg=CARD, fg=MUTED, font=("", 7, "bold")).pack(anchor="w")
            l = tk.Label(f, text="-", bg=CARD, fg=TEXT, font=("", 11, "bold"))
            l.pack(anchor="w")
            self.pf_stats[k] = l
        btns = tk.Frame(top, bg=CARD)
        btns.grid(row=0, column=7, sticky="e")
        top.columnconfigure(6, weight=1)
        ttk.Button(btns, text="Buy...", style="Accent.TButton", command=lambda: self.trade_dialog("buy")).pack(side="left")
        ttk.Button(btns, text="Sell...", style="Plain.TButton", command=lambda: self.trade_dialog("sell")).pack(side="left", padx=4)
        self.pf_alloc = tk.Canvas(parent, height=26, bg=CARD, highlightthickness=0)
        self.pf_alloc.pack(fill="x", padx=6, pady=(2, 4))
        self.pf_alloc.bind("<Configure>", lambda e: self.draw_alloc())
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
        name.bind("<Button-1>", lambda e: (self.chart_view.set(c), self.draw_chart()))
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
            return messagebox.showinfo("Trade", "Press 'Start the bot' first to open a session (Practice or Live). "
                                                "You can stop the bot right away and still trade by hand.")
        coins = list(dict.fromkeys(list(self.session_coins) + [c for c, q in self.pos.items() if q > 0]))
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
        self.farm.bind("<Double-1>", lambda e: (self._farm_click(e), self.bot_detail()))
        self.root.after(200, self.animate_farm)

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

    def animate_farm(self):
        """Little robots for every bot: working ones bob and blink, sleeping ones snooze, a bot that just
        traded flashes. Runs about 6 times a second; cheap enough to never slow the app down."""
        try:
            self._draw_farm()
        except Exception:
            pass
        self.root.after(170, self.animate_farm)

    def _draw_farm(self):
        c = self.farm
        if not c.winfo_ismapped():
            return
        c.delete("all")
        self.farm_phase += 1
        ph = self.farm_phase
        W = max(c.winfo_width(), 100)
        self.farm_hits = []
        if not self.tourn:
            msg = "Your bot farm appears here when Wildcard is running." if not self.running else "Waking the bots up..."
            c.create_text(W // 2, 22, text=msg, fill=MUTED)
            c.config(height=46)
            return
        rows = self.tourn.board()
        size, gap = 26, 6
        per_row = max(1, (W - 8) // (size + gap))
        nrows = (len(rows) + per_row - 1) // per_row
        want_h = nrows * (size + 18) + 6
        if int(c.cget("height")) != want_h:
            c.config(height=want_h)
        amber = "#fbbf24" if DARK else "#d97706"
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
            bob = round(2 * math.sin((ph + i * 3) / 2.2)) if working else 0
            y = y0 + bob
            mood = "happy" if st == "active" else "plain"
            draw_dragon(c, x0, y, size, col, awake=working, phase=ph + i * 3, mood=mood,
                        fire=just_traded and v.log[-1][2] == "buy", flash=just_traded and ph % 2 == 0)
            if not working:
                k = (ph + i * 5) % 18
                if k < 12:
                    c.create_text(x0 + size + 1 + k / 3, y + 2 - k, text="z" if k < 6 else "Z", fill=MUTED,
                                  font=("", 7 + k // 6, "bold"))
            if just_traded:
                side = v.log[-1][2]
                c.create_text(x0 + size / 2, y - 9, text="$" if side == "sell" else "+", fill=GREEN if side == "buy" else amber,
                              font=("", 9, "bold"))
            if v.inducted:
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

    def toggle_team_mode(self):
        on = self.team_var.get()
        self.cfg["team_mode"] = on
        E.save_json(CONFIG_PATH, self.cfg)
        n = len(E.team())
        if on and not n:
            self.team_var.set(False)
            self.cfg["team_mode"] = False
            return messagebox.showinfo("Team", "Your team is empty. Open the Bot Collection (Ctrl+B), pick up to "
                                               f"{E.TEAM_MAX} bots and press 'Add to team'.")
        if on and not self.wild_var.get():
            self.wild_var.set(True)
            self.toggle_wild()
        self.update_team_label()
        if self.session and self.tourn:
            self.write("The team change applies when Wildcard next starts (File > Reset session).", "warn")

    def update_team_label(self):
        n = len(E.team())
        self.team_cb.config(text=f"Trade with my team ({n}/{E.TEAM_MAX})")

    def open_collection(self):
        if getattr(self, "col_win", None) is not None and self.col_win.winfo_exists():
            return self.col_win.lift()
        w = self.col_win = tk.Toplevel(self.root)
        w.title("Bot Collection")
        w.geometry("1020x680")
        top = ttk.Frame(w, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        self.col_head = ttk.Label(top, text="Bot Collection", font=("", 13, "bold"))
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
        self.col_canvas.bind("<Configure>", lambda e: self.col_draw())
        self.col_canvas.bind("<Button-1>", self._col_click)
        self.col_canvas.bind("<Double-1>", lambda e: (self._col_click(e), self._col_team(None)))
        self.col_canvas.bind("<MouseWheel>", lambda e: self.col_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.col_canvas.bind("<Button-4>", lambda e: self.col_canvas.yview_scroll(-1, "units"))
        self.col_canvas.bind("<Button-5>", lambda e: self.col_canvas.yview_scroll(1, "units"))
        self.col_sel = set()
        for txt, cmd, tip in (("Add to team", lambda: self._col_team(True), "Your team trades together when "
                               "'Trade with my team' is ticked. Double-click a card to add or remove it."),
                              ("Remove from team", lambda: self._col_team(False), ""),
                              ("Add to this run", self._col_add_run, "Drop the selected bots into the running tournament."),
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
        self.col_head.config(text=f"Bot Collection: {len(self.col_bots)} caught  |  "
                                  + "  ".join(f"{n} {r}" for r, n in reversed(list(counts.items())) if n)
                                  + (f"  |  ✨ {shiny} shiny" if shiny else ""))
        n_team = sum(1 for b in self.col_bots if b.get("team"))
        self.col_team_lbl.config(text=f"Team: {n_team}/{E.TEAM_MAX}")
        if hasattr(self, "team_cb"):
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
            draw_dragon(c, x + 8, y + 40, 42, rc, awake=True, phase=0, mood="happy", gold=bool(b.get("shiny")))
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
            if b.get("team"):
                c.create_text(x + cw - 8, y + ch - 12, anchor="e", text="★ TEAM", fill="#f59e0b", font=("", 8, "bold"))
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
                                      tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), origin="collection"):
                n += 1
        self.flush_events()
        self.refresh_board()
        self.write(f"Added {n} bots from your collection to this run." if n else "Those bots are already competing.")

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
            E.release([E.bot_key(b) for b in bots])
            self.col_sel = set()
            self.col_refresh()

    # ---------------- bot detail card ----------------
    def bot_detail(self, v=None):
        v = v or self.selected_variant()
        if not v or not self.tourn:
            return messagebox.showinfo("Strategy", "Click a strategy on the scoreboard first.")
        w = tk.Toplevel(self.root)
        w.title(STRAT_LABELS.get(v.name, v.name))
        w.geometry("760x620")
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        title = friendly_label(v.name, v.params, v.tf, self.session_bar)
        ttk.Label(f, text=("★ " if v.inducted else "") + title, font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(f, text=STRAT_TIPS.get(v.name, ""), foreground=MUTED, wraplength=720).pack(anchor="w", pady=(2, 8))
        stats = ttk.Frame(f)
        stats.pack(fill="x")
        info_lbls = {}
        for i, key in enumerate(("Status", "Recent gain", "vs holding (recent)", "Lifetime return", "Lifetime vs holding",
                                 "Win rate", "Trades", "Worst drop", "Time winning", "Best market", "Own stops",
                                 "Family", "Traits")):
            ttk.Label(stats, text=key, foreground=MUTED).grid(row=i // 3, column=(i % 3) * 2, sticky="w", padx=(0, 6), pady=2)
            l = ttk.Label(stats, text="-", font=("", 10, "bold"), wraplength=230)
            l.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=(0, 22), pady=2)
            info_lbls[key] = l
        crow = ttk.Frame(f)
        crow.pack(fill="x", pady=(10, 2))
        ttk.Label(crow, text="Entries and exits on").pack(side="left")
        coin = tk.StringVar(value=self.session_coins[0] if self.session_coins else "")
        cb = ttk.Combobox(crow, textvariable=coin, values=self.session_coins, state="readonly", width=8)
        cb.pack(side="left", padx=6)
        ttk.Label(crow, text="▲ buy   ▼ sell   (last 300 decisions)", foreground=MUTED).pack(side="left")
        cv = tk.Canvas(f, height=230, bg=CARD, highlightthickness=0)
        cv.pack(fill="both", expand=True)

        def paint(_=None):
            if not w.winfo_exists():
                return
            lf = self.tourn.life(v)
            d = self.tourn.info.get(id(v), {})
            st = STATUS_TEXT.get(d.get("status"), d.get("status", "removed")) if v in self.tourn.vars else "Removed"
            hrs = v.win_steps * self.session_bar / 60
            parents = self.tourn.lineage.get(v.uid, {}).get("parents", [])
            fam = {"starter": "starter bot", "bred": f"bred from #{' and #'.join(map(str, parents))}",
                   "tweaked": f"tweaked copy of #{parents[0]}" if parents else "tweaked copy"}.get(v.origin, v.origin or "-")
            vals = {"Status": st, "Recent gain": pct_txt(d.get("score")), "vs holding (recent)": pct_txt(d.get("edge")),
                    "Lifetime return": pct_txt(lf["ret"]), "Lifetime vs holding": pct_txt(lf["edge"]),
                    "Win rate": "-" if lf["winrate"] is None else f"{lf['winrate'] * 100:.0f}% ({lf['wins']}W/{lf['losses']}L)",
                    "Trades": str(lf["trades"]), "Worst drop": f"{lf['maxdd'] * 100:.1f}%",
                    "Time winning": f"{hrs:.1f} h" if hrs >= 1 else f"{hrs * 60:.0f} min",
                    "Best market": MOOD_TEXT.get(lf["best_mood"], "not sure yet") if lf["best_mood"] else "not sure yet",
                    "Own stops": E.risk_text(v.risk) or "none", "Family": f"#{v.uid}, {fam}, generation {v.gen}",
                    "Traits": E.traits_text(v.traits) or "none yet"}
            for k, l in info_lbls.items():
                l.config(text=vals[k])
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
            rows = [dict(r, bar_minutes=self.session_bar) for r in self.tourn.board() if r["v"] is v]
            E.induct(E.export_bots(rows, self.version, note="added by hand")["bots"], self.version)
            v.inducted = True
            self.write(f"Added {title} to the Hall of Fame.", "good")
            self.refresh_board()
            paint()

        def size(d):
            v.size_mult = round(min(max(v.size_mult + d, 0.25), 3.0), 2)
            self.tourn._rate()
            self.refresh_board()
            self.write(f"{title} trade size is now {v.size_mult:.2f}x.")
        ttk.Button(b, text="Export this bot...", command=export_one).pack(side="left")
        ttk.Button(b, text="Add to Hall of Fame", command=to_hof).pack(side="left", padx=6)
        ttk.Button(b, text="Catch!", style="Accent.TButton", command=lambda: self.catch_rows(
            [r for r in self.tourn.board() if r["v"] is v], "detail card")).pack(side="left", padx=(0, 6))
        ttk.Button(b, text="Size -", command=lambda: size(-0.25)).pack(side="left")
        ttk.Button(b, text="Size +", command=lambda: size(0.25)).pack(side="left", padx=(4, 0))
        ttk.Button(b, text="Show on main chart", command=lambda: (
            self.board.selection_set(str(id(v))) if self.board.exists(str(id(v))) else None,
            self.chart_view.set(coin.get() or "Account value"), self.chart_bot.set(True), self.draw_chart())).pack(side="left", padx=6)
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
            f"Bots get in automatically after {self.settings.get('hof_hours', 6):g} hours of Winning with at least "
            f"{self.settings.get('hof_min_trades', 4)} finished trades, ahead of both zero and just holding over their whole life. "
            "Bot Lab passes can be added too. The file is a normal bot file you can share. "
            "Past wins don't guarantee future ones, so they always have to win again on live prices.")).pack(anchor="w", pady=(2, 0))
        mid = ttk.Frame(w, padding=(10, 4))
        mid.pack(fill="both", expand=True)
        t = self.make_tree(mid, ("strategy", "edge", "ret", "win", "hours", "best", "stops", "when"),
                           (250, 85, 80, 70, 70, 110, 120, 120),
                           ("Strategy", "vs holding", "Return", "Win rate", "Hrs winning", "Best market", "Own stops", "Added"))
        t.configure(selectmode="extended")
        state = {"bots": []}

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
                                           tf=b.get("tf", 1), risk=b.get("risk"), traits=b.get("traits"), origin="hall of fame")
                if v:
                    v.inducted, n = True, n + 1
            self.flush_events()
            self.refresh_board()
            self.write(f"Added {n} Hall of Fame bots to this run." if n else "Those bots are already competing.")

        def remove():
            sel = [state["bots"][int(i)] for i in t.selection()]
            if not sel or not messagebox.askyesno("Hall of Fame", f"Remove {len(sel)} bots from the Hall of Fame?", parent=w):
                return
            E.remove_from_hof([E.bot_key(b) for b in sel])
            fill()
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
                                          tf=r["tf"], risk=r.get("risk"), traits=r.get("traits"), origin="bot lab"):
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
            "Ctrl+B        Bot Collection\n"
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
            ttk.Label(f, text=blurb, foreground=MUTED, wraplength=520).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
            for r, (key, label, typ) in enumerate(fields, start=1):
                cur = self.settings.get(key)
                if typ is bool:
                    var = tk.BooleanVar(value=bool(cur))
                    ttk.Checkbutton(f, text=label, variable=var).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)
                    ents[key] = (var, bool)
                    continue
                ttk.Label(f, text=label, wraplength=360, justify="left").grid(row=r, column=0, sticky="w", pady=3)
                if typ == "bar":
                    var = tk.StringVar(value=BAR_LABELS.get(float(cur), "1 min"))
                    ttk.Combobox(f, textvariable=var, values=list(BAR_LABELS.values()), state="readonly", width=12).grid(
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
                    elif typ is str:
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
                assert 14 <= new["max_bots"] <= 50 and new["retire_hours"] >= 0 and 0 <= new["auto_coins"] <= 15
                assert new["hof_hours"] > 0 and new["hof_min_trades"] >= 0 and new["max_trades_hour"] >= 0
                assert 0 <= new["trail_stop"] < 100 and 0 <= new["slippage"] < 5 and 0 <= new["maker_fee_pct"] < 5
                assert 3 <= new["limit_wait"] <= 600 and 0 <= new["fee_pct"] < 5
                for n, p in params.items():
                    assert E.fix_params(n, dict(p)) == p
            except Exception:
                return messagebox.showerror("Settings", "One of the numbers isn't valid. Reminders: winner trade size must be "
                                                        "between 1 and 3; most strategies must be 14 to 50; the bot can pick 0 to 15 "
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
            self.upd_btn.config(text=f"\u2b06 Update to {remote}")
            self.upd_btn.pack(side="right", padx=(0, 8), before=self.theme_btn)
            self.write(f"Version {remote} is available. Click 'Update' at the top to see what's new.", "good")
            if not silent:
                self.show_update()
            return
        if kind == "installed":
            written, backup = data
            self.upd_btn.pack_forget()
            self.write(f"Updated {len(written)} files. The old version is kept in {backup}.", "good")
            if messagebox.askyesno("Update installed", "The update is installed. Restart CBBF now to use it?\n\n"
                                                       "(Your session is saved first. Tools > Undo last update goes back.)"):
                self.restart_app()
            return
        if kind == "install_fail":
            messagebox.showerror("Update", f"The update didn't install, nothing was changed: {data}")

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
