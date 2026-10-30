"""Core logic for CBBF (Coinbase Bot Farm): strategies, simulation, wildcard tournament,
historical candles and local storage. No GUI code lives here."""
import csv
import json
import os
import importlib
import importlib.util
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone

HOME_DIR = os.path.join(os.path.expanduser("~"), ".cbbf")
OLD_HOME_DIR = os.path.join(os.path.expanduser("~"), ".desk-utils")  # the app's data folder before it was renamed


def migrate_home():
    """First start after the rename: copy the old data folder (sessions, trades, saved bots) across.
    The old folder is left in place untouched. Returns True if something was copied."""
    try:
        if not os.path.exists(HOME_DIR) and os.path.isdir(OLD_HOME_DIR):
            shutil.copytree(OLD_HOME_DIR, HOME_DIR)
            return True
    except OSError:
        pass
    return False
WARM = 30  # bars skipped at the start of a backtest so indicators have data
TIMEFRAMES = (1, 2, 3, 5, 10, 15)  # how many app bars a bot waits between decisions (x the Bar length setting)
BAR_CHOICES = (0.25, 0.5, 1, 5, 15, 60)  # decision speed in minutes: 15 s, 30 s, 1, 5, 15, 60 min
CANDLE_GRANS = (60, 300, 900, 3600, 21600, 86400)  # what Coinbase's candle endpoint offers (seconds)
LOOKBACK = 300  # most bars any strategy looks back; longer history is trimmed for speed


def candle_gran(bar_minutes):
    """Closest Coinbase candle size for a bar length. Sub-minute bars use 1-minute candles
    (Coinbase has nothing shorter), so their history is approximate until live bars replace it."""
    sec = float(bar_minutes) * 60
    return min(CANDLE_GRANS, key=lambda g: (abs(g - sec), g)) if sec >= 60 else 60


def fmt_bar(minutes):
    """'15 sec', '1 min', '1 hr' ..."""
    m = float(minutes)
    if m < 1:
        return f"{round(m * 60):g} sec"
    if m >= 60 and m % 60 == 0:
        return f"{m / 60:g} hr"
    return f"{m:g} min"


def tail_series(p, tf=1, keep=LOOKBACK):
    """The last `keep` closes of tf-bar candles ending at the newest bar (cheap: never copies the whole history)."""
    if tf <= 1:
        return p[-keep:]
    last = len(p) - 1
    if last < 0:
        return []
    start = last - tf * min(keep - 1, last // tf)
    return p[start::tf]


def tail_extra(p, x, tf=1, keep=LOOKBACK):
    """Highs, lows and volumes matching tail_series(p, tf, keep): each tf-bar candle gets the highest
    high, lowest low and summed volume of its bars. None when the extra data doesn't line up."""
    if not x or len(x.get("h", ())) != len(p) or len(x.get("l", ())) != len(p) or len(x.get("v", ())) != len(p):
        return None
    if tf <= 1:
        return dict(h=x["h"][-keep:], l=x["l"][-keep:], v=x["v"][-keep:])
    last = len(p) - 1
    start = last - tf * min(keep - 1, last // tf)
    H, Lo, V = x["h"], x["l"], x["v"]
    h, lo, v = [], [], []
    for j in range(start, last + 1, tf):
        a = max(0, j - tf + 1)
        h.append(max(H[a:j + 1]))
        lo.append(min(Lo[a:j + 1]))
        v.append(sum(V[a:j + 1]))
    return dict(h=h, l=lo, v=v)


def strat_signal(name, p, n, params, tf=1, x=None):
    """One strategy's signal on the newest bar, using tf-bar candles. Strategies that need highs,
    lows or volume get them when available and stay quiet when not."""
    spec = STRATS[name]
    series = tail_series(p, tf, spec.get("keep", LOOKBACK))
    if spec.get("hlv"):
        return spec["fn"](series, n // tf if tf > 1 else n, params,
                          tail_extra(p, x, tf, spec.get("keep", LOOKBACK)))
    return spec["fn"](series, n // tf if tf > 1 else n, params)


def clamp_tf(tf):
    """Nearest allowed timeframe for any value (bad input falls back to 1)."""
    try:
        tf = float(tf)
    except (TypeError, ValueError):
        return 1
    return min(TIMEFRAMES, key=lambda t: abs(t - tf))


# ---------------- network helpers ----------------
def http_get(url, timeout=10, retries=0):
    """GET with a few automatic retries for brief network blips, rate limits (429) and server errors."""
    delay = 1.0
    for attempt in range(retries + 1):
        wait = delay
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cbbf"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as ex:
            if ex.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                raise
            try:
                wait = min(max(float(ex.headers.get("Retry-After", delay)), delay), 10.0)
            except (TypeError, ValueError):
                pass
        except (urllib.error.URLError, OSError):  # timeouts, DNS, dropped connections
            if attempt >= retries:
                raise
        time.sleep(wait)
        delay *= 2


def spot(coin, quote="USD"):
    data = json.loads(http_get(f"https://api.coinbase.com/v2/prices/{coin}-{quote}/spot", 8, retries=2))
    return float(data["data"]["amount"])


def spot_many(coins, quote="USD"):
    """Prices for many coins in one request (public Advanced Trade endpoint). Missing coins are left out."""
    ids = "&".join(f"product_ids={c}-{quote}" for c in coins)
    data = json.loads(http_get(f"https://api.coinbase.com/api/v3/brokerage/market/products?{ids}", 8, retries=1))
    out = {}
    for p in data.get("products", []):
        try:
            c = p["product_id"].rsplit("-", 1)[0]
            px = float(p.get("price") or 0)
            if c in coins and px > 0:
                out[c] = px
        except (KeyError, TypeError, ValueError):
            continue
    return out


def to_dict(r):
    return r if isinstance(r, dict) else r.to_dict()


def fetch_candles(coin, gran_seconds, count, quote="USD"):
    """Closing prices, oldest first, from Coinbase's public candle endpoint.
    gran_seconds must be one of 60, 300, 900, 3600, 21600, 86400."""
    return fetch_ohlcv(coin, gran_seconds, count, quote)["c"]


def fetch_ohlcv(coin, gran_seconds, count, quote="USD"):
    """{'c': closes, 'h': highs, 'l': lows, 'v': volumes}, oldest first."""
    rows = {}
    end = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    for _ in range(count // 300 + 2):
        if len(rows) >= count:
            break
        start = end - timedelta(seconds=gran_seconds * 300)
        url = (f"https://api.exchange.coinbase.com/products/{coin}-{quote}/candles"
               f"?granularity={gran_seconds}&start={start.strftime(fmt)}&end={end.strftime(fmt)}")
        data = json.loads(http_get(url, 15, retries=2))
        if not data:
            break
        for r in data:  # [time, low, high, open, close, volume]
            rows[r[0]] = (float(r[4]), float(r[2]), float(r[1]), float(r[5]))
        end = start
        time.sleep(0.15)
    seq = [rows[t] for t in sorted(rows)][-count:]
    return dict(c=[r[0] for r in seq], h=[r[1] for r in seq], l=[r[2] for r in seq], v=[r[3] for r in seq])


def latest_volume(coin, gran_seconds, quote="USD"):
    """Volume of the most recent finished candle (None if unavailable)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=gran_seconds * 4)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    data = json.loads(http_get(f"https://api.exchange.coinbase.com/products/{coin}-{quote}/candles"
                               f"?granularity={gran_seconds}&start={start.strftime(fmt)}&end={end.strftime(fmt)}", 8))
    data = sorted(data or [], key=lambda r: r[0])
    if len(data) >= 2:
        return float(data[-2][5])
    return float(data[-1][5]) if data else None


# ---------------- strategies: fn(prices, bar_index, params) -> "buy"/"sell"/None ----------------
def _avg(p, n, off=0):
    seg = p[len(p) - n - off: len(p) - off]
    return sum(seg) / n


def sig_sma(p, n, a):
    f, s = int(a["fast"]), int(a["slow"])
    if f >= s or len(p) < s + 1:
        return None
    f0, s0, f1, s1 = _avg(p, f, 1), _avg(p, s, 1), _avg(p, f), _avg(p, s)
    if f0 <= s0 and f1 > s1:
        return "buy"
    if f0 >= s0 and f1 < s1:
        return "sell"


def sig_rsi(p, n, a):
    k = int(a["period"])
    if len(p) < k + 1:
        return None
    d = [p[i] - p[i - 1] for i in range(len(p) - k, len(p))]
    gain = sum(x for x in d if x > 0) / k
    loss = -sum(x for x in d if x < 0) / k
    rsi = 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return "buy" if rsi < a["lo"] else "sell" if rsi > a["hi"] else None


def sig_mom(p, n, a):
    k = int(a["n"])
    if len(p) < k + 1:
        return None
    ch = p[-1] / p[-1 - k] - 1
    thr = a["thr"] / 100
    return "buy" if ch > thr else "sell" if ch < -thr else None


def sig_boll(p, n, a):
    k = int(a["n"])
    if len(p) < k:
        return None
    w = p[-k:]
    m = sum(w) / k
    sd = (sum((x - m) ** 2 for x in w) / k) ** 0.5
    if p[-1] < m - a["k"] * sd:
        return "buy"
    if p[-1] > m + a["k"] * sd:
        return "sell"


def sig_breakout(p, n, a):
    k = int(a["n"])
    if len(p) < k + 1:
        return None
    prev = p[-k - 1:-1]
    return "buy" if p[-1] > max(prev) else "sell" if p[-1] < min(prev) else None


def _ema(vals, k):
    m, e, out = 2.0 / (k + 1), vals[0], [vals[0]]
    for v in vals[1:]:
        e = v * m + e * (1 - m)
        out.append(e)
    return out


def sig_macd(p, n, a):
    f, s, g = int(a["fast"]), int(a["slow"]), int(a["signal"])
    if f >= s or len(p) < s + g + 2:
        return None
    seg = p[-(s * 3 + g):]
    ef, es = _ema(seg, f), _ema(seg, s)
    macd = [x - y for x, y in zip(ef, es)]
    sig = _ema(macd, g)
    d0, d1 = macd[-2] - sig[-2], macd[-1] - sig[-1]
    if d0 <= 0 < d1:
        return "buy"
    if d0 >= 0 > d1:
        return "sell"


def sig_revert(p, n, a):
    k = int(a["n"])
    if len(p) < k:
        return None
    dev = (p[-1] / _avg(p, k) - 1) * 100
    return "buy" if dev < -a["thr"] else "sell" if dev > a["thr"] else None


def sig_dca(p, n, a):
    return "buy" if n % int(a["every"]) == 0 else None


def sig_ema(p, n, a):
    """Fast/slow exponential average crossover. Reacts quicker than SMA, good for short bars."""
    f, s = int(a["fast"]), int(a["slow"])
    if f >= s or len(p) < s + 2:
        return None
    seg = p[-(s * 3):]
    ef, es = _ema(seg, f), _ema(seg, s)
    d0, d1 = ef[-2] - es[-2], ef[-1] - es[-1]
    gap = abs(d1) / es[-1] * 100 if es[-1] else 0
    if gap < a.get("min_gap", 0):  # ignore crossings too small to beat fees
        return None
    if d0 <= 0 < d1:
        return "buy"
    if d0 >= 0 > d1:
        return "sell"


def sig_stoch(p, n, a):
    """Stochastic oscillator on closes: where the price sits in its recent range (0-100).
    Buys when it climbs back up out of the bottom zone, sells when it drops back out of the top zone."""
    k = int(a["n"])
    if len(p) < k + 1:
        return None

    def pos(w):
        lo, hi = min(w), max(w)
        return 50.0 if hi == lo else (w[-1] - lo) / (hi - lo) * 100
    k0, k1 = pos(p[-k - 1:-1]), pos(p[-k:])
    if k0 < a["lo"] <= k1:
        return "buy"
    if k0 > a["hi"] >= k1:
        return "sell"


def _rsi(p, k):
    d = [p[i] - p[i - 1] for i in range(len(p) - k, len(p))]
    gain = sum(x for x in d if x > 0) / k
    loss = -sum(x for x in d if x < 0) / k
    return 100 if loss == 0 else 100 - 100 / (1 + gain / loss)


def sig_volbreak(p, n, a, x=None):
    """Breakout that only counts when volume is well above normal (a real crowd behind the move)."""
    k = int(a["n"])
    if not x or len(p) < k + 2:
        return None
    vol = x["v"]
    base = vol[-k - 1:-1]
    avgv = sum(base) / k
    if avgv <= 0:
        return None
    if p[-1] > max(x["h"][-k - 1:-1]) and vol[-1] >= a["vmult"] * avgv:
        return "buy"
    if p[-1] < min(x["l"][-k - 1:-1]):
        return "sell"


def sig_vwap(p, n, a, x=None):
    """Volume-weighted average price over the lookback. Buys well below it, sells well above it."""
    k = int(a["n"])
    if not x or len(p) < k:
        return None
    h, lo, v = x["h"][-k:], x["l"][-k:], x["v"][-k:]
    tv = sum(v)
    if tv <= 0:
        return None
    vw = sum((h[i] + lo[i] + p[-k + i]) / 3 * v[i] for i in range(k)) / tv
    dev = (p[-1] / vw - 1) * 100
    return "buy" if dev < -a["thr"] else "sell" if dev > a["thr"] else None


def sig_atr(p, n, a, x=None):
    """ATR channel (Keltner style): buys a push above the average plus m x the typical bar range
    (true range, from highs and lows), sells a drop below the average minus that."""
    k, m = int(a["n"]), a["m"]
    if not x or len(p) < k + 2:
        return None
    h, lo = x["h"], x["l"]
    tr = [max(h[i] - lo[i], abs(h[i] - p[i - 1]), abs(lo[i] - p[i - 1])) for i in range(len(p) - k, len(p))]
    atr = sum(tr) / k
    mid = _ema(p[-(k * 3):], k)[-1]
    if p[-1] > mid + m * atr:
        return "buy"
    if p[-1] < mid - m * atr:
        return "sell"


def sig_trend_dip(p, n, a):
    """Buys short dips (low RSI) only while the price is above its long average (an uptrend).
    Sells on a pop (high RSI) or when the uptrend breaks."""
    t, k = int(a["trend"]), int(a["period"])
    if len(p) < max(t, k + 1):
        return None
    up = p[-1] > _avg(p, t)
    r = _rsi(p, k)
    if up and r < a["lo"]:
        return "buy"
    if r > a["hi"] or not up:
        return "sell"


STRATS = {
    "SMA crossover": dict(fn=sig_sma, params={"fast": 5, "slow": 20},
                          bounds={"fast": (2, 30), "slow": (10, 100)}),
    "RSI": dict(fn=sig_rsi, params={"period": 14, "lo": 30, "hi": 70},
                bounds={"period": (5, 30), "lo": (15, 40), "hi": (60, 85)}),
    "Momentum": dict(fn=sig_mom, params={"n": 10, "thr": 0.5},
                     bounds={"n": (3, 40), "thr": (0.1, 3.0)}),
    "Bollinger bands": dict(fn=sig_boll, params={"n": 20, "k": 2.0},
                            bounds={"n": (10, 50), "k": (1.0, 3.0)}),
    "Breakout": dict(fn=sig_breakout, params={"n": 20}, bounds={"n": (5, 60)}),
    "MACD": dict(fn=sig_macd, params={"fast": 12, "slow": 26, "signal": 9},
                 bounds={"fast": (3, 20), "slow": (15, 50), "signal": (3, 15)}),
    "Mean reversion": dict(fn=sig_revert, params={"n": 20, "thr": 1.0},
                           bounds={"n": (5, 60), "thr": (0.2, 5.0)}),
    "EMA crossover": dict(fn=sig_ema, params={"fast": 4, "slow": 12, "min_gap": 0.05},
                          bounds={"fast": (2, 20), "slow": (5, 60), "min_gap": (0.0, 0.5)}),
    "Stochastic": dict(fn=sig_stoch, params={"n": 14, "lo": 20, "hi": 80},
                       bounds={"n": (5, 40), "lo": (5, 35), "hi": (65, 95)}),
    "Trend + dip": dict(fn=sig_trend_dip, params={"trend": 60, "period": 7, "lo": 30, "hi": 70},
                        bounds={"trend": (20, 150), "period": (3, 20), "lo": (15, 40), "hi": (60, 85)}),
    "Volume breakout": dict(fn=sig_volbreak, params={"n": 20, "vmult": 1.8}, hlv=True, keep=80,
                            bounds={"n": (8, 60), "vmult": (1.0, 4.0)}),
    "VWAP snap-back": dict(fn=sig_vwap, params={"n": 30, "thr": 0.8}, hlv=True, keep=120,
                           bounds={"n": (8, 100), "thr": (0.15, 4.0)}),
    "ATR channel": dict(fn=sig_atr, params={"n": 20, "m": 1.5}, hlv=True, keep=200,
                        bounds={"n": (5, 60), "m": (0.5, 4.0)}),
    "Dollar-cost averaging": dict(fn=sig_dca, params={"every": 24},
                                  bounds={"every": (6, 96)}),
}


def fix_params(name, p):
    """Keeps related settings sensible (short average below long one, buy level below sell level)."""
    d = STRATS[name]["params"]
    if "fast" in p and "slow" in p and p["fast"] >= p["slow"]:
        p["slow"] = min(p["fast"] + 5, STRATS[name]["bounds"]["slow"][1])
        p["fast"] = min(p["fast"], p["slow"] - 1)
        if p["fast"] < STRATS[name]["bounds"]["fast"][0]:
            p["fast"], p["slow"] = d["fast"], d["slow"]
    if "lo" in p and "hi" in p and p["lo"] >= p["hi"] - 10:
        p["lo"], p["hi"] = d["lo"], d["hi"]
    return p
WILDCARD_STRATS = [n for n in STRATS if n != "Dollar-cost averaging"]  # DCA never sells


# ---------------- settings ----------------
DEFAULT_SETTINGS = {
    "trade_pct": 10.0, "stop_loss": 5.0, "take_profit": 10.0, "daily_loss": 10.0,
    "bar_minutes": 1, "refresh": 2, "fee_pct": 0.6, "evolve_every": 24, "notify": True,
    "sound": False,              # (old setting, no longer used: CBBF has its own chime now)
    "chime": True,               # play CBBF's own sounds with notifications (not the Windows system sound)
    "chime_volume": 60,          # 0-100
    "sounds_off": [],            # which of CBBF's sounds are switched off: trade, notify, hof, problem
    "notify_trades": False,      # also notify on every trade (off: only important events)
    "max_coin_pct": 50.0, "style": "Balanced", "keep_awake": True,
    "size_mult": {},  # manual-mode trade size per strategy name (1.0 = normal)
    # Wildcard: how winners and shaky strategies are treated
    "winners_trade_more": True,  # winners add to positions and get bigger trades
    "winner_boost": 2.5,         # biggest trade-size multiplier a top winner can earn
    "add_every": 3,              # decisions between add-on buys while a winner is holding
    "slow_every": 6,             # decisions between trades that only shaky strategies want
    "max_bots": 30,              # most strategies competing at once (14 to 50)
    "retire_hours": 12.0,        # replace a strategy that has not been winning for this long (0 = never)
    "fail_drop": 3.0,            # % drop from a strategy's recent peak that stops it at once (0 = off)
    "beat_hold": True,           # a strategy only counts as Winning if it also beats just holding
    "crossbreed": True,          # new strategies can mix the settings of two winners
    "regime_aware": True,        # favour strategies that do well in the current market mood
    # Hall of Fame: long-term winners are saved automatically
    "hof_hours": 6.0,            # hours a strategy must spend Winning (in total) to get in
    "hof_min_trades": 4,         # and at least this many finished (sold) trades
    "hof_load": True,            # bring the best Hall of Fame bots back when Wildcard starts
    "auto_catch": True,          # Hall of Fame bots also go into your Bot Collection
    "team_locked": True,         # trading with your team: keep exactly those bots (False = they may breed new ones)
    # Orders and costs
    "bot_stops": True,           # Wildcard: the account's safety stop / lock-in-profit follow what the winning bots learned
    "max_trades_hour": 30,       # brake: most signal trades the account may make in an hour (0 = no limit)
    "trail_stop": 0.0,           # % trailing stop: sell if a coin falls this far from its high since buying (0 = off)
    "slippage": 0.05,            # % lost to the bid/ask spread and price movement on each market order (Practice)
    "order_type": "market",      # "market" or "limit" (post-only maker orders, lower fees)
    "maker_fee_pct": 0.4,        # fee for limit orders that rest on the book (Practice)
    "limit_wait": 20,            # seconds a limit order may wait for a fill
    "limit_fallback": True,      # if a limit order does not fill in time, use a market order instead
    # Alerts
    "alert_discord": "", "alert_tg_token": "", "alert_tg_chat": "",
    "alert_trades": True, "alert_hof": True, "alert_problems": True,
    # Coins
    "extra_coins": [],           # more Coinbase coins added from the coin browser
    "auto_coins": 0,             # Wildcard: let the bot pick this many coins at start (0 = use the ticked ones)
}
STYLES = {  # risk presets: trade size, stop-loss, take-profit, daily limit, max in one coin (all %)
    "Cautious": dict(trade_pct=5.0, stop_loss=3.0, take_profit=6.0, daily_loss=5.0, max_coin_pct=30.0),
    "Balanced": dict(trade_pct=10.0, stop_loss=5.0, take_profit=10.0, daily_loss=10.0, max_coin_pct=50.0),
    "Adventurous": dict(trade_pct=20.0, stop_loss=8.0, take_profit=20.0, daily_loss=20.0, max_coin_pct=70.0),
}


def merge_settings(saved):
    s = dict(DEFAULT_SETTINGS)
    s["params"] = {n: dict(sp["params"]) for n, sp in STRATS.items()}
    for k, v in (saved or {}).items():
        if k == "params":
            for n, pp in (v or {}).items():
                if n in s["params"] and isinstance(pp, dict):
                    s["params"][n].update({pk: pv for pk, pv in pp.items() if pk in s["params"][n]})
        elif k in s:
            s[k] = v
    s["size_mult"] = dict(s.get("size_mult") or {})
    s["extra_coins"] = [c for c in (s.get("extra_coins") or []) if isinstance(c, str) and valid_coin(c)]
    if s["order_type"] not in ("market", "limit"):
        s["order_type"] = "market"
    try:
        s["bar_minutes"] = min(BAR_CHOICES, key=lambda b: abs(b - float(s["bar_minutes"])))
    except (TypeError, ValueError):
        s["bar_minutes"] = 1
    return s


def valid_coin(c):
    return bool(re.fullmatch(r"[A-Z0-9]{1,12}", c or ""))


def cost_model(settings):
    """(fee, slippage) as fractions for simulated trades. Limit orders pay the maker fee and no spread."""
    if settings.get("order_type") == "limit":
        return float(settings.get("maker_fee_pct", 0.4)) / 100, 0.0
    return float(settings.get("fee_pct", 0.6)) / 100, float(settings.get("slippage", 0.0)) / 100


# ---------------- market mood ----------------
def market_mood(p, n=60):
    """-> (mood, efficiency 0..1, move %). Efficiency is how straight the price travelled: 1 = a clean
    one-way move, near 0 = lots of back and forth for little net change."""
    if len(p) < n + 1:
        return "unknown", 0.0, 0.0
    seg = p[-n - 1:]
    path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    move = seg[-1] - seg[0]
    er = abs(move) / path if path else 0.0
    pct = move / seg[0] * 100 if seg[0] else 0.0
    if er >= 0.3:
        return ("trending up" if move > 0 else "trending down"), er, pct
    if path / seg[0] * 100 < 0.15 * n ** 0.5 / 4:  # barely moving at all
        return "quiet", er, pct
    return "choppy", er, pct


MOODS = ("trending up", "trending down", "choppy", "quiet")


# ---------------- simulated portfolio ----------------
class Sim:
    def __init__(self, cash, fee, pct, max_coin=None, slip=0.0):
        self.start, self.cash, self.fee, self.pct, self.max_coin = cash, cash, fee, pct, max_coin
        self.slip = slip      # spread + slippage: buys fill a bit higher, sells a bit lower
        self.pos, self.cost = {}, {}
        self.tbars = deque()  # bar indices of recent trades
        self.last = None      # details of the trade just made (read and cleared by the Tournament)

    def buy(self, c, px, n=0, mult=1.0):
        spend = min(self.start * self.pct * mult, self.cash)
        if self.max_coin:
            spend = min(spend, self.start * self.max_coin - self.cost.get(c, 0))
        if spend < 1:
            return False
        fill = px * (1 + self.slip)
        self.cash -= spend
        qty = spend * (1 - self.fee) / fill
        self.pos[c] = self.pos.get(c, 0) + qty
        self.cost[c] = self.cost.get(c, 0) + spend
        self.tbars.append(n)
        self.last = dict(side="buy", coin=c, qty=qty, price=fill, usd=spend, pl=None)
        return True

    def sell(self, c, px, n=0):
        q = self.pos.get(c, 0)
        if q <= 0:
            return False
        fill = px * (1 - self.slip)
        proceeds = q * fill * (1 - self.fee)
        self.cash += proceeds
        self.last = dict(side="sell", coin=c, qty=q, price=fill, usd=proceeds, pl=proceeds - self.cost.get(c, 0))
        self.pos[c], self.cost[c] = 0, 0
        self.tbars.append(n)
        return True

    def act(self, c, sig, px, n=0):
        if sig == "buy":
            self.buy(c, px, n)
        elif sig == "sell":
            self.sell(c, px, n)

    def equity(self, last):
        return self.cash + sum(q * last.get(c, 0) for c, q in self.pos.items())


# ---------------- wildcard tournament ----------------
def tournament_opts(settings):
    """Picks the winner/shaky-strategy knobs out of the app settings."""
    fee, slip = cost_model(settings)
    return dict(trade_more=bool(settings.get("winners_trade_more", True)),
                winner_boost=float(settings.get("winner_boost", 2.5)),
                add_every=int(settings.get("add_every", 3)),
                slow_every=int(settings.get("slow_every", 6)),
                fail_drop=float(settings.get("fail_drop", 3.0)),
                beat_hold=bool(settings.get("beat_hold", True)),
                crossbreed=bool(settings.get("crossbreed", True)),
                regime_aware=bool(settings.get("regime_aware", True)),
                slip=slip)


RISK_BOUNDS = {"sl": (0.5, 15.0), "tp": (0.5, 30.0)}  # a bot's own stop-loss / take-profit, % (0 = none)


def clean_risk(r):
    out = {}
    for k, (lo, hi) in RISK_BOUNDS.items():
        try:
            v = float((r or {}).get(k, 0) or 0)
        except (TypeError, ValueError):
            v = 0.0
        out[k] = 0.0 if v <= 0 else round(min(max(v, lo), hi), 2)
    return out


def risk_text(r):
    r = r or {}
    parts = ([f"stop {r['sl']:g}%"] if r.get("sl") else []) + ([f"target {r['tp']:g}%"] if r.get("tp") else [])
    return ", ".join(parts)


# Traits: extra habits every bot can evolve on top of its strategy. 0 / empty = off.
TRAITS = {
    "trail":   dict(lo=0.5, hi=12.0, kind=float, text=lambda v: f"trailing stop {v:g}%"),
    "maxhold": dict(lo=3, hi=200, kind=int, text=lambda v: f"sells after {v} decisions"),
    "cool":    dict(lo=1, hi=30, kind=int, text=lambda v: f"waits {v} decisions after selling"),
    "trend":   dict(lo=20, hi=250, kind=int, text=lambda v: f"buys only above its {v}-bar average"),
    "minvol":  dict(lo=0.02, hi=1.5, kind=float, text=lambda v: f"skips markets moving under {v:g}% a bar"),
    "rsicap":  dict(lo=55, hi=90, kind=int, text=lambda v: f"won't buy if RSI is over {v}"),
    "confirm": dict(lo=2, hi=3, kind=int, text=lambda v: f"waits for {v} signals in a row"),
    "entries": dict(lo=1, hi=5, kind=int, text=lambda v: f"buys at most {v}x per position"),
    "exit":    dict(lo=1, hi=1, kind=int, text=lambda v: "sells only through its stops"),
    "moods":   dict(lo=1, hi=14, kind=int, text=lambda v: "trades only when " + " / ".join(
        m for i, m in enumerate(MOODS) if v & (1 << i))),
}
TRAIT_NAMES = {"trail": "Trailing stop", "maxhold": "Max hold", "cool": "Cooldown", "trend": "Trend filter",
               "minvol": "Min volatility", "rsicap": "Overbought guard", "confirm": "Confirmation",
               "entries": "Max entries", "exit": "Stops-only exit", "moods": "Mood gate"}


def clean_traits(t):
    out = {}
    for k, spec in TRAITS.items():
        try:
            v = float((t or {}).get(k, 0) or 0)
        except (TypeError, ValueError):
            v = 0
        if v <= 0:
            continue
        v = min(max(v, spec["lo"]), spec["hi"])
        out[k] = int(round(v)) if spec["kind"] is int else round(v, 2)
    return out


def traits_text(t):
    return ", ".join(TRAITS[k]["text"](v) for k, v in sorted((t or {}).items()) if k in TRAITS)


def bot_step(v, closes, n, coins, extra=None):
    """One decision of one bot on every coin: its own exits first (stop-loss, take-profit, trailing stop,
    max hold), then its strategy signal filtered by its traits. Returns the trades made [(coin, trade)]."""
    last = {c: closes[c][-1] for c in coins}
    tr, made = v.traits, []
    for c in coins:
        sim = v.sim
        sim.last = None
        q = sim.pos.get(c, 0)
        if q > 0:
            v.hi[c] = max(v.hi.get(c, last[c]), last[c])
            r = (last[c] / (sim.cost[c] / q) - 1) * 100
            why = None
            if v.risk["sl"] and r <= -v.risk["sl"]:
                why = "stop"
            elif v.risk["tp"] and r >= v.risk["tp"]:
                why = "target"
            elif tr.get("trail") and last[c] <= v.hi[c] * (1 - tr["trail"] / 100):
                why = "trail"
            elif tr.get("maxhold") and n - v.since.get(c, n) >= tr["maxhold"] * v.tf:
                why = "time"
            if why:
                sim.sell(c, last[c], n)
                v.sig[c] = "sell"
        if sim.last is None:
            if n % v.tf:  # between this bot's own decisions: no signal, no trade
                v.sig[c] = None
                continue
            s = strat_signal(v.name, closes[c], n, v.params, v.tf, (extra or {}).get(c))
            if tr.get("confirm"):
                v.run_sig[c] = (s, v.run_sig.get(c, (None, 0))[1] + 1) if s and s == v.run_sig.get(c, (None, 0))[0] else (s, 1 if s else 0)
                if s and v.run_sig[c][1] < tr["confirm"]:
                    s = None
            if s == "buy":
                ser = None
                if tr.get("cool") and n - v.sold_at.get(c, -10 ** 9) < tr["cool"] * v.tf:
                    s = None
                elif tr.get("entries") and v.entries.get(c, 0) >= tr["entries"]:
                    s = None
                else:
                    if tr.get("trend") or tr.get("minvol") or tr.get("rsicap"):
                        ser = tail_series(closes[c], v.tf)
                    if tr.get("trend") and (len(ser) < tr["trend"] or ser[-1] <= _avg(ser, tr["trend"])):
                        s = None
                    elif tr.get("minvol") and (len(ser) < 21 or sum(abs(ser[i] / ser[i - 1] - 1) for i in range(-20, 0)) / 20 * 100 < tr["minvol"]):
                        s = None
                    elif tr.get("rsicap") and len(ser) > 15 and _rsi(ser, 14) > tr["rsicap"]:
                        s = None
                    elif tr.get("moods") and not (tr["moods"] & (1 << MOODS.index(m)) if (m := market_mood(closes[c])[0]) in MOODS else False):
                        s = None
            elif s == "sell" and tr.get("exit") and (v.risk["sl"] or v.risk["tp"] or tr.get("trail") or tr.get("maxhold")):
                s = None  # this bot only leaves through its own exits
            v.sig[c] = s
            sim.act(c, s, last[c], n)
        t = sim.last
        if t:
            if t["side"] == "buy":
                v.entries[c] = v.entries.get(c, 0) + 1
                v.since.setdefault(c, n)
                v.hi[c] = max(v.hi.get(c, last[c]), last[c])
            else:
                v.entries.pop(c, None)
                v.since.pop(c, None)
                v.hi.pop(c, None)
                v.sold_at[c] = n
            made.append((c, t))
    return made


class Variant:
    def __init__(self, name, params, sim, maxlen, tf=1, risk=None, traits=None):
        self.name, self.params, self.sim = name, params, sim
        self.risk = clean_risk(risk)
        self.traits = clean_traits(traits)
        self.hi, self.since, self.entries, self.sold_at, self.run_sig = {}, {}, {}, {}, {}
        self.uid, self.parents, self.gen = 0, [], 0
        self.tf = clamp_tf(tf)  # decides once every tf bars, on tf-bar candles
        self.eq = deque(maxlen=maxlen)
        self.sig = {}
        self.strikes = 0
        self.size_mult = 1.0  # you can raise or lower this per strategy
        # lifetime track record (since this bot was created)
        self.born_px = None
        self.age = self.ntrades = self.wins = self.losses = 0
        self.win_steps = self.streak = 0
        self.peak, self.maxdd = 0.0, 0.0
        self.mood_gain = {}   # mood -> summed equity change (fraction) while that mood ruled
        self.mood_n = {}
        self.inducted = False
        self.origin = ""
        self.log = deque(maxlen=300)  # (decision number, coin, side, price) of this bot's own trades, for charts

    @property
    def label(self):
        rt = ", ".join(x for x in (risk_text(self.risk), traits_text(self.traits)) if x)
        return (f"{self.name} ({', '.join(f'{k}={v}' for k, v in self.params.items())})"
                + (f" on {self.tf}-bar candles" if self.tf > 1 else "") + (f" [{rt}]" if rt else ""))

    def key(self):
        return (self.name, self.tf, tuple(sorted((k, round(float(v), 4)) for k, v in self.params.items())),
                tuple(sorted(self.risk.items())), tuple(sorted(self.traits.items())))


def bot_key(d):
    return (d["name"], clamp_tf(d.get("tf", 1)),
            tuple(sorted((k, round(float(v), 4)) for k, v in d["params"].items())),
            tuple(sorted(clean_risk(d.get("risk")).items())),
            tuple(sorted(clean_traits(d.get("traits")).items())))


class Tournament:
    """Runs many strategy variants as shadow portfolios on the same prices.

    Each variant is rated on recent return, how that compares to just holding the same coins,
    consistency (share of recent sub-periods that were profitable) and how far it has dropped
    from its recent peak.

      active   profitable, consistent and (with beat_hold) ahead of holding. The account follows it
               with bigger trades (up to winner_boost x for the strongest) and keeps adding while it holds.
      slowed   profitable but inconsistent, or not beating holding. Followed only with small trades
               and a cooldown between them.
      standby  qualifies as a winner but there is no room for it among the top few.
      benched  losing, not consistent at all, or dropped sharply. Zero trades. Variants that
               stay benched are retired and replaced by tweaked copies of the winners."""

    def __init__(self, coins, start_cash, fee, pct, evolve_every=24, window=100,
                 min_trades=2, max_active=5, cap=14, params=None, rng=None, retire_after=3, opts=None,
                 retire_steps=0):
        self.coins, self.start_cash, self.fee, self.pct = list(coins), start_cash, fee, pct
        self.evolve_every, self.W, self.min_trades = max(1, evolve_every), window, min_trades
        self.max_active, self.cap, self.retire_after = max_active, cap, retire_after
        self.family_cap = max(5, -(-self.cap // 4))
        self.retire_steps = max(0, int(retire_steps))  # decisions a bot may go without winning before it is replaced
        self.o = dict(trade_more=True, winner_boost=2.5, add_every=3, slow_every=6, fail_drop=3.0,
                      beat_hold=True, crossbreed=True, regime_aware=True, slip=0.0)
        self.o.update(opts or {})
        self.slowed, self.cool, self.add_cool, self.kind = [], {}, {}, {}
        self.rng = rng or random.Random()
        self.n = 0
        self.events = []
        self.recording = False  # the app switches this on once live; history replays are never recorded
        self.shadow = []        # every bot's own pretend trades since the last pop_shadow()
        self.hold_ret = None    # what just holding the coins returned over the scoring window
        self.mood, self.moods = "unknown", {}
        self.last_px = {}
        self.xfull = None       # highs/lows/volumes lined up with the closes of the current step
        self.serial = 0
        self.lineage = {}       # uid -> family-tree record (kept after a bot is removed)
        self.vars = []
        self.info, self.active = {}, []
        for name in WILDCARD_STRATS:
            p = dict((params or {}).get(name) or STRATS[name]["params"])
            self.vars.append(self._new(name, p))
        for name in WILDCARD_STRATS:  # a slower-paced copy of each, so different speeds compete from the start
            p = dict((params or {}).get(name) or STRATS[name]["params"])
            self.vars.append(self._new(name, p, tf=3))
        while len(self.vars) < min(self.cap, 24):  # a bigger, more varied field from the start
            name = self.rng.choice(WILDCARD_STRATS)
            self.vars.append(self._new(name, self._rand_params(name), tf=self.rng.choice(TIMEFRAMES),
                                       risk=self.rand_risk(), traits=self.rand_traits()))
        del self.vars[max(self.cap, 14):]
        for v in self.vars:
            self._register(v, [], "starter")

    def _new(self, name, params, tf=1, risk=None, traits=None):
        return Variant(name, params, Sim(self.start_cash, self.fee, self.pct, slip=self.o.get("slip", 0.0)),
                       self.W + 1, tf, risk, traits)

    def rand_traits(self, chance=0.3):
        t = {}
        for k, spec in TRAITS.items():
            if self.rng.random() < chance:
                v = self.rng.uniform(spec["lo"], spec["hi"])
                if k == "moods":
                    v = self.rng.randint(1, 14)
                t[k] = v
        return clean_traits(t)

    def mutate_traits(self, t):
        out = dict(t)
        for k, spec in TRAITS.items():
            roll = self.rng.random()
            if roll < 0.08:  # switch a trait on or off
                if k in out:
                    out.pop(k)
                else:
                    out[k] = self.rng.randint(1, 14) if k == "moods" else self.rng.uniform(spec["lo"], spec["hi"])
            elif k in out and roll < 0.35:
                if k == "moods":
                    out[k] = out[k] ^ (1 << self.rng.randrange(len(MOODS))) or out[k]
                else:
                    out[k] = out[k] * self.rng.uniform(0.75, 1.3)
        return clean_traits(out)

    def _register(self, v, parents, origin):
        """Gives a bot its id and family-tree entry."""
        self.serial += 1
        v.uid, v.parents, v.origin = self.serial, [p.uid for p in parents], origin
        v.gen = 1 + max((p.gen for p in parents), default=-1) if parents else 0
        self.lineage[v.uid] = dict(uid=v.uid, label=v.label, name=v.name, tf=v.tf, parents=list(v.parents),
                                   gen=v.gen, origin=origin, born=self.n, died=None, why="", ret=None, inducted=False)

    def _remove(self, v, why):
        if v in self.vars:
            self.vars.remove(v)
        rec = self.lineage.get(v.uid)
        if rec:
            rec.update(died=self.n, why=why, ret=v.eq[-1] / v.sim.start - 1 if v.eq else None, inducted=v.inducted)
        dead = [u for u, r in self.lineage.items() if r["died"] is not None]
        for u in dead[:-400]:  # keep the tree from growing forever
            del self.lineage[u]

    def rand_risk(self):
        """About half of new random bots carry their own stop-loss and/or take-profit."""
        r = {}
        for k, (lo, hi) in RISK_BOUNDS.items():
            r[k] = round(self.rng.uniform(lo, hi * 0.5), 2) if self.rng.random() < 0.5 else 0.0
        return r

    def mutate_risk(self, r):
        out = {}
        for k, (lo, hi) in RISK_BOUNDS.items():
            v = float(r.get(k, 0) or 0)
            roll = self.rng.random()
            if roll < 0.1:  # switch it on or off
                v = 0.0 if v else self.rng.uniform(lo, hi * 0.5)
            elif v and roll < 0.45:
                v *= self.rng.uniform(0.7, 1.3)
            out[k] = v
        return clean_risk(out)

    def mutate_tf(self, tf):
        """Usually keep the parent's pace; sometimes move one step faster or slower."""
        i = TIMEFRAMES.index(clamp_tf(tf))
        if self.rng.random() < 0.4:
            i = min(max(i + self.rng.choice((-1, 1)), 0), len(TIMEFRAMES) - 1)
        return TIMEFRAMES[i]

    def _run(self, v, closes, n, mood=None, extra=None):
        last = {c: closes[c][-1] for c in self.coins}
        if v.born_px is None:
            v.born_px = dict(last)
        prev_eq = v.eq[-1] if v.eq else None
        x = extra if extra is not None else self.xfull
        for c, t in bot_step(v, closes, n, self.coins, x):
            v.log.append((n, c, t["side"], last[c]))
            v.ntrades += 1
            if t["side"] == "sell":
                if t["pl"] > 0:
                    v.wins += 1
                else:
                    v.losses += 1
            if self.recording:
                self.shadow.append(dict(t, name=v.name, params=dict(v.params), tf=v.tf, uid=v.uid))
        e = v.sim.equity(last)
        v.eq.append(e)
        v.age += 1
        v.peak = max(v.peak, e)
        if v.peak > 0:
            v.maxdd = max(v.maxdd, (v.peak - e) / v.peak)
        if mood and mood != "unknown" and prev_eq:
            v.mood_gain[mood] = v.mood_gain.get(mood, 0.0) + (e / prev_eq - 1)
            v.mood_n[mood] = v.mood_n.get(mood, 0) + 1
        while v.sim.tbars and v.sim.tbars[0] <= n - self.W:
            v.sim.tbars.popleft()

    def _market(self, closes):
        self.last_px = {c: closes[c][-1] for c in self.coins}
        L = min(len(closes[c]) for c in self.coins)
        self.hold_ret = (sum(closes[c][-1] / closes[c][-1 - self.W] for c in self.coins) / len(self.coins) - 1
                         if L > self.W else None)
        self.moods = {c: market_mood(closes[c])[0] for c in self.coins}
        known = [m for m in self.moods.values() if m != "unknown"]
        self.mood = max(set(known), key=known.count) if known else "unknown"

    def step(self, closes, extra=None):
        """closes: coin -> list of bar closes, newest last, same length per coin.
        extra (optional): coin -> {'h': highs, 'l': lows, 'v': volumes}, lined up with the closes."""
        self.n += 1
        self.xfull = extra
        self._market(closes)
        for c in self.coins:
            self.cool[c] = max(0, self.cool.get(c, 0) - 1)
            self.add_cool[c] = max(0, self.add_cool.get(c, 0) - 1)
        for v in self.vars:
            self._run(v, closes, self.n, self.mood)
        self._rate()
        for v in self.vars:
            if self.info[id(v)]["status"] == "active":
                v.win_steps += 1
                v.streak += 1
            else:
                v.streak = 0
        if self.retire_steps:
            self._rotate(closes)
        if self.n % self.evolve_every == 0:
            self.evolve(closes)

    def warmup(self, closes, start=WARM, progress=None, extra=None):
        L = min(len(closes[c]) for c in self.coins)
        for i in range(start, L):
            self.step({c: closes[c][:i + 1] for c in self.coins}, slice_extra(extra, i + 1))
            if progress and (i - start) % 50 == 0:
                progress(i - start, L - start)

    def score(self, v):
        if len(v.eq) < self.W + 1 or len(v.sim.tbars) < self.min_trades or v.eq[0] <= 0:
            return None
        return v.eq[-1] / v.eq[0] - 1

    def consistency(self, v):
        """Share of the 4 recent sub-periods in which the variant made money (0..1)."""
        eq, size = list(v.eq), self.W // 4
        if len(eq) < self.W + 1 or size < 1:
            return 0.0
        return sum(1 for k in range(4) if eq[(k + 1) * size] > eq[k * size]) / 4

    def drawdown(self, v):
        """How far the variant's equity sits below its recent peak (0..1)."""
        eq = list(v.eq)
        peak = max(eq) if eq else 0
        return (peak - eq[-1]) / peak if peak > 0 else 0.0

    def mood_fit(self, v, mood=None):
        """Average gain per decision while `mood` ruled, or None with too little evidence."""
        mood = mood or self.mood
        k = v.mood_n.get(mood, 0)
        return v.mood_gain.get(mood, 0.0) / k if k >= 30 else None

    def best_mood(self, v):
        fits = [(self.mood_fit(v, m), m) for m in MOODS]
        fits = [x for x in fits if x[0] is not None]
        return max(fits)[1] if fits and max(fits)[0] > 0 else None

    def life(self, v):
        """Lifetime track record since the bot was created."""
        ret = v.eq[-1] / v.sim.start - 1 if v.eq and v.sim.start else 0.0
        hold = None
        if v.born_px and self.last_px:
            r = [self.last_px[c] / v.born_px[c] for c in self.coins if v.born_px.get(c) and c in self.last_px]
            hold = sum(r) / len(r) - 1 if r else None
        closed = v.wins + v.losses
        return dict(ret=ret, hold=hold, edge=None if hold is None else ret - hold, trades=v.ntrades,
                    wins=v.wins, losses=v.losses, winrate=v.wins / closed if closed else None,
                    maxdd=v.maxdd, age=v.age, win_steps=v.win_steps, streak=v.streak, best_mood=self.best_mood(v))

    def _rate(self):
        info, o = {}, self.o
        for v in self.vars:
            sc = self.score(v)
            if sc is None:
                info[id(v)] = dict(score=None, cons=None, weight=0.0, stake=0.0, status="warming up", failing=False,
                                   edge=None, behind=False)
                continue
            cons = self.consistency(v)
            edge = None if self.hold_ret is None else sc - self.hold_ret
            behind = bool(o["beat_hold"] and edge is not None and edge <= 0)
            failing = o["fail_drop"] > 0 and self.drawdown(v) * 100 > o["fail_drop"]
            base = edge if (o["beat_hold"] and edge is not None and edge > 0) else sc
            if failing or sc <= 0 or cons < 0.25:
                status, w = "benched", 0.0
            elif cons >= 0.5 and not behind:
                status, w = "active", base * cons ** 2
            else:
                status, w = "slowed", sc * cons ** 2
            if w > 0 and o["regime_aware"]:
                fit = self.mood_fit(v)
                if fit is not None:
                    w *= 1.25 if fit > 0 else 0.8
            info[id(v)] = dict(score=sc, cons=cons, weight=w, stake=0.0, status=status, failing=failing,
                               edge=edge, behind=behind)
        ranked = sorted((v for v in self.vars if info[id(v)]["status"] == "active"),
                        key=lambda v: -info[id(v)]["weight"])
        self.active = ranked[:self.max_active]
        top = info[id(self.active[0])]["weight"] if self.active else 0
        hi = max(1.0, o["winner_boost"] if o["trade_more"] else 2.0)
        for v in self.active:
            d = info[id(v)]
            d["stake"] = 0.5 + (hi - 0.5) * d["weight"] / top
        for v in ranked[self.max_active:]:
            info[id(v)].update(status="standby", weight=0.0)
        self.slowed = sorted((v for v in self.vars if info[id(v)]["status"] == "slowed"),
                             key=lambda v: -info[id(v)]["weight"])
        for v in self.slowed:
            info[id(v)]["stake"] = 0.25
        for v in self.vars:
            d = info[id(v)]
            if d["status"] in ("active", "slowed"):
                d["stake"] *= v.size_mult
        self.info = info

    def ranking(self):
        r = [(v, self.score(v)) for v in self.vars]
        return sorted([x for x in r if x[1] is not None], key=lambda x: -x[1])

    def drivers(self):
        return list(self.active)

    def summary(self):
        """(number of active winners, best variant or None, any variant scored yet)"""
        scored = any(d["score"] is not None for d in self.info.values())
        return len(self.active), (self.active[0] if self.active else None), scored

    @staticmethod
    def _holding(v, c):
        return v.sim.pos.get(c, 0) > 0

    def vote(self, c):
        """-> (signal, size multiplier). Winners vote at full weight, shaky strategies at a
        small weight. A vote that only shaky strategies want must wait out a cooldown, so they
        trade slowly. When nobody has a fresh signal but strong winners are holding the coin,
        the account adds to the position now and then (self.kind[c] == "add")."""
        self.kind[c] = None
        bw = sw = bs = ss = 0.0
        act_buy = act_sell = False
        for v in self.active + self.slowed:
            d, s = self.info[id(v)], v.sig.get(c)
            is_act = d["status"] == "active"
            if s == "buy":
                bw += d["weight"]
                bs += d["weight"] * d["stake"]
                act_buy = act_buy or is_act
            elif s == "sell":
                sw += d["weight"]
                ss += d["weight"] * d["stake"]
                act_sell = act_sell or is_act
        sig, mult, from_winner = None, 1.0, False
        if bw > sw:
            sig, mult, from_winner = "buy", bs / bw, act_buy
        elif sw > bw:
            sig, mult, from_winner = "sell", ss / sw, act_sell
        if sig and not from_winner:  # only shaky strategies want this: slow it down
            if self.cool.get(c, 0) > 0:
                return None, 1.0
            self.cool[c] = self.o["slow_every"]
            self.kind[c] = "slow"
        if sig is None and self.o["trade_more"] and self.add_cool.get(c, 0) == 0:
            holders = [v for v in self.active if self._holding(v, c) and self.info[id(v)]["cons"] >= 0.75]
            hw = sum(self.info[id(v)]["weight"] for v in holders)
            tw = sum(self.info[id(v)]["weight"] for v in self.active)
            if holders and hw * 2 > tw:  # winners with most of the weight are still in this coin
                self.add_cool[c] = self.o["add_every"]
                self.kind[c] = "add"
                avg = sum(self.info[id(v)]["weight"] * self.info[id(v)]["stake"] for v in holders) / hw
                return "buy", 0.5 * avg
        return sig, mult

    def board(self):
        blank = dict(score=None, cons=None, stake=0.0, status="warming up", failing=False, edge=None, behind=False)
        order = {"active": 0, "slowed": 1, "standby": 2, "benched": 3, "warming up": 4}
        rows = [(v, self.info.get(id(v), blank)) for v in self.vars]
        rows.sort(key=lambda x: (order[x[1]["status"]], -(x[1]["score"] or 0)))
        return [dict(name=v.name, params=dict(v.params), label=v.label, score=d["score"], cons=d["cons"],
                     stake=d["stake"], status=d["status"], trades=len(v.sim.tbars), strikes=v.strikes, tf=v.tf,
                     failing=d.get("failing", False), driving=d["status"] == "active", v=v, size=v.size_mult,
                     edge=d.get("edge"), behind=d.get("behind", False), life=self.life(v), origin=v.origin,
                     risk=dict(v.risk), traits=dict(v.traits), uid=v.uid, parents=list(v.parents), gen=v.gen)
                for v, d in rows]

    def mutate(self, name, params, scale=0.25):
        spec, new = STRATS[name], {}
        for k, val in params.items():
            lo, hi = spec["bounds"][k]
            nv = min(max(val * self.rng.uniform(1 - scale, 1 + scale), lo), hi)
            new[k] = int(round(nv)) if isinstance(spec["params"][k], int) else round(nv, 2)
        return fix_params(name, new)

    def crossover(self, name, a, b):
        """Child settings: each setting taken from one parent or the other, then lightly tweaked."""
        mix = {k: (a[k] if self.rng.random() < 0.5 else b.get(k, a[k])) for k in a}
        return self.mutate(name, mix, scale=0.1)

    def replay(self, v, closes):
        L = min(len(closes[c]) for c in self.coins)
        m = min(self.W + 1, L - WARM)
        rec, self.recording = self.recording, False  # replayed history is not a new trade
        try:
            x = self.xfull if self.xfull and all(len((self.xfull.get(c) or {}).get("h", ())) == len(closes[c])
                                                 for c in self.coins) else None
            for j in range(m):
                i = L - m + j
                self._run(v, {c: closes[c][:i + 1] for c in self.coins}, self.n - (m - 1 - j),
                          extra=slice_extra(x, i + 1) if x else {})
        finally:
            self.recording = rec

    def evolve(self, closes):
        for v in self.vars:  # strikes: checks spent benched (slowed counts half, standby none)
            st = self.info[id(v)]["status"]
            if st == "active":
                v.strikes = 0
            elif st == "benched":
                v.strikes += 1
            elif st == "slowed":
                v.strikes += 0.5
        retired = 0
        for v in sorted(self.vars, key=lambda x: -x.strikes):
            if v.strikes >= self.retire_after and sum(1 for x in self.vars if x.name == v.name) > 1:
                self._remove(v, "retired (kept losing)")
                retired += 1
                self.events.append(f"Retired {v.label} (kept losing)")
        strong = any(self.info[id(v)]["cons"] >= 0.75 and self.info[id(v)]["score"] > 0 for v in self.active)
        for _ in range(max(1, retired) + (1 if self.o["trade_more"] and strong else 0)):
            self._spawn(closes)  # strong winners get an extra tweaked copy
        for _ in range(min(3, self.cap - len(self.vars))):  # keep filling up to the limit
            self._spawn_or_random(closes)
        self._rate()

    def _spawn_or_random(self, closes):
        before = len(self.vars)
        self._spawn(closes)
        if len(self.vars) == before and len(self.vars) < self.cap:
            self.spawn_random(closes)

    def _rotate(self, closes):
        """Replaces bots that have gone retire_steps decisions without winning (losing, or never trading)."""
        gone = []
        for v in self.vars:
            st = self.info.get(id(v), {}).get("status")
            v.bad = 0 if st in ("active", "slowed", "standby") else getattr(v, "bad", 0) + 1
            if v.bad >= self.retire_steps and len(self.vars) - len(gone) > 8:
                gone.append(v)
        for v in gone:
            self._remove(v, "rotated out (not winning for a long time)")
            self.events.append(f"Rotated out {v.label} (not winning for a long time)")
        for _ in gone:
            self._spawn_or_random(closes)
        if gone:
            self._rate()

    def _spawn(self, closes):
        if self.active:
            parent = self.rng.choices(self.active, weights=[self.info[id(v)]["weight"] for v in self.active])[0]
        else:
            r = self.ranking()
            if not r:
                return
            parent = r[0][0]
        fam = sum(1 for x in self.vars if x.name == parent.name)
        if fam >= self.family_cap or len(self.vars) >= self.cap:
            cands = [v for v, _ in reversed(self.ranking()) if v is not parent and v not in self.active
                     and sum(1 for x in self.vars if x.name == v.name) > 1]
            if fam >= self.family_cap:
                cands = [v for v in cands if v.name == parent.name]
            if not cands:
                return
            self._remove(cands[0], "dropped to make room")
            self.events.append(f"Dropped {cands[0].label}")
        mate = None
        if self.o.get("crossbreed", True) and self.rng.random() < 0.4:
            good = [v for v in self.vars if v is not parent and v.name == parent.name
                    and self.info.get(id(v), {}).get("status") in ("active", "standby", "slowed")]
            if good:
                mate = self.rng.choice(good)
        if mate:
            risk = {k: self.rng.choice((parent.risk[k], mate.risk[k])) for k in RISK_BOUNDS}
            traits = {}
            for k in TRAITS:
                pick = self.rng.choice((parent, mate)).traits
                if k in pick:
                    traits[k] = pick[k]
            child = self._new(parent.name, self.crossover(parent.name, parent.params, mate.params),
                              self.rng.choice((parent.tf, mate.tf)), self.mutate_risk(risk), self.mutate_traits(traits))
            self._register(child, [parent, mate], "bred")
            note = f"Bred {child.label} from two winners"
        else:
            child = self._new(parent.name, self.mutate(parent.name, parent.params), self.mutate_tf(parent.tf),
                              self.mutate_risk(parent.risk), self.mutate_traits(parent.traits))
            self._register(child, [parent], "tweaked")
            note = f"Spawned {child.label} (tweaked from {parent.label})"
        self.replay(child, closes)
        self.vars.append(child)
        self.events.append(note)

    def add_variant(self, name, params, closes=None, size=1.0, note="Added", tf=1, origin="added", risk=None, traits=None):
        """Adds a strategy with the given settings (clamped to sane bounds) and replays recent prices."""
        if name not in STRATS:
            return None
        spec, p = STRATS[name], {}
        for k, dv in spec["params"].items():
            lo, hi = spec["bounds"][k]
            try:
                val = min(max(float(params.get(k, dv)), lo), hi)
            except (TypeError, ValueError):
                val = dv
            p[k] = int(round(val)) if isinstance(dv, int) else round(val, 2)
        fix_params(name, p)
        v = self._new(name, p, tf, risk, traits)
        self._register(v, [], origin)
        v.size_mult = min(max(float(size), 0.25), 3.0)
        if closes:
            self.replay(v, closes)
        self.vars.append(v)
        self.events.append(f"{note} {v.label}")
        self._rate()
        return v

    def _rand_params(self, name):
        spec, p = STRATS[name], {}
        for k, dv in spec["params"].items():
            lo, hi = spec["bounds"][k]
            val = self.rng.uniform(lo, hi)
            p[k] = int(round(val)) if isinstance(dv, int) else round(val, 2)
        if "fast" in p and "slow" in p:
            p["fast"], p["slow"] = sorted((p["fast"], p["slow"]))
            if p["fast"] == p["slow"]:
                p["slow"] += 5
        return fix_params(name, p)

    def spawn_random(self, closes=None):
        """A brand new strategy with random settings. Makes room by dropping the weakest duplicate."""
        name = self.rng.choice(WILDCARD_STRATS)
        p = self._rand_params(name)
        if len(self.vars) >= self.cap:
            cands = [v for v, _ in reversed(self.ranking()) if v not in self.active
                     and sum(1 for x in self.vars if x.name == v.name) > 1]
            if cands:
                self._remove(cands[0], "dropped to make room")
                self.events.append(f"Dropped {cands[0].label}")
            else:
                return None
        return self.add_variant(name, p, closes, note="Random new strategy:", tf=self.rng.choice(TIMEFRAMES),
                                origin="random", risk=self.rand_risk(), traits=self.rand_traits(0.35))

    def hall_of_fame_candidates(self, min_win_steps, min_trades):
        """Bots that have earned a place: enough time Winning, enough finished trades, and ahead of
        both zero and just holding over their whole life."""
        out = []
        for v in self.vars:
            if v.inducted or v.win_steps < min_win_steps:
                continue
            lf = self.life(v)
            if (v.wins + v.losses) >= min_trades and lf["ret"] > 0 and (lf["edge"] is None or lf["edge"] > 0):
                out.append(v)
        return out

    def export_lineup(self):
        """Strategies worth keeping (everything not stopped), as plain data for saving."""
        keep = [v for v in self.vars if self.info.get(id(v), {}).get("status") != "benched"] or list(self.vars)
        return [dict(name=v.name, params=dict(v.params), size=v.size_mult, tf=v.tf, risk=dict(v.risk), traits=dict(v.traits))
                for v in keep]

    def family(self):
        """Family-tree records for living and recently removed bots, with live stats for the living."""
        alive = {v.uid: v for v in self.vars}
        out = []
        for uid, rec in self.lineage.items():
            r = dict(rec)
            v = alive.get(uid)
            if v:
                r.update(label=v.label, alive=True, status=self.info.get(id(v), {}).get("status", "warming up"),
                         ret=v.eq[-1] / v.sim.start - 1 if v.eq else None, inducted=v.inducted)
            else:
                r.update(alive=False, status=rec["why"] or "removed")
            out.append(r)
        return out

    def pop_shadow(self):
        s, self.shadow = self.shadow, []
        return s

    def pop_events(self):
        e, self.events = self.events, []
        return e


# ---------------- backtesting ----------------
def simulate(closes, sigfn, start_cash, fee, pct, stop=0.0, take=0.0, warm=WARM, max_coin=None, slip=0.0, trail=0.0,
             extra=None):
    """sigfn(i, prefixes, extra_prefixes) -> {coin: signal}. Returns (equity_curve, trade_count)."""
    coins = list(closes)
    n = min(len(v) for v in closes.values())
    sim = Sim(start_cash, fee, pct, max_coin, slip)
    eq, trades = [start_cash], 0
    high = {}
    for i in range(n):
        pre = {c: closes[c][:i + 1] for c in coins}
        sigs = sigfn(i, pre, slice_extra(extra, i + 1) if extra else None)
        if i < warm:
            continue
        px = {c: closes[c][i] for c in coins}
        for c in coins:
            q = sim.pos.get(c, 0)
            if q > 0:
                high[c] = max(high.get(c, px[c]), px[c])
                r = px[c] / (sim.cost[c] / q) - 1
                if (stop > 0 and r <= -stop) or (take > 0 and r >= take) or \
                        (trail > 0 and px[c] <= high[c] * (1 - trail)):
                    sim.sell(c, px[c], i)
                    high.pop(c, None)
                    trades += 1
                    continue
            s, m = sigs.get(c), 1.0
            if isinstance(s, tuple):
                s, m = s
            if s == "buy" and sim.buy(c, px[c], i, m):
                high[c] = max(high.get(c, px[c]), px[c])
                trades += 1
            elif s == "sell" and sim.sell(c, px[c], i):
                high.pop(c, None)
                trades += 1
        eq.append(sim.equity(px))
    return eq, trades


def metrics(eq):
    peak, dd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x)
        dd = max(dd, (peak - x) / peak if peak else 0)
    return eq[-1] / eq[0] - 1, dd


def bot_sigfn(name, params, tf, coins):
    """Signal function for one bot that decides the same way it does inside the tournament."""
    tf = clamp_tf(tf)

    def sig(i, pre, prex=None):
        if i % tf:
            return {}
        return {c: strat_signal(name, pre[c], i, params, tf, (prex or {}).get(c)) for c in coins}
    return sig


def replay_bot(v, closes, extra, start, start_cash, fee, pct, slip=0.0):
    """A frozen copy of bot v trading on its own from bar `start` to the end, exactly as it would in the
    tournament (strategy, pace, own stops and traits). Returns (equity curve, trade count)."""
    coins = list(closes)
    b = Variant(v.name, dict(v.params), Sim(start_cash, fee, pct, slip=slip), 2, v.tf, dict(v.risk), dict(v.traits))
    L = min(len(x) for x in closes.values())
    eq, trades = [start_cash], 0
    for i in range(start, L):
        pre = {c: closes[c][max(0, i + 1 - 4600):i + 1] for c in coins}
        ex = {c: {k: d[k][max(0, i + 1 - 4600):i + 1] for k in d} for c, d in extra.items()} if extra else None
        trades += len(bot_step(b, pre, i, coins, ex))
        eq.append(b.sim.equity({c: closes[c][i] for c in coins}))
    return eq, trades


def slice_extra(extra, i):
    """The extra data (highs/lows/volumes) cut to the first i bars."""
    if not extra:
        return None
    return {c: {k: v[:i] for k, v in d.items()} for c, d in extra.items() if d}


def run_backtest(closes, names, settings, start_cash=1000.0, seed=1, extra=None):
    """Returns rows of (name, return, max_drawdown, trades), best first."""
    fee, slip = cost_model(settings)
    pct = settings["trade_pct"] / 100
    stop, take = settings["stop_loss"] / 100, settings["take_profit"] / 100
    trail = float(settings.get("trail_stop", 0)) / 100
    mc = settings.get("max_coin_pct", 50) / 100
    coins = list(closes)
    L = min(len(v) for v in closes.values())
    closes = {c: v[-L:] for c, v in closes.items()}
    extra = {c: {k: v[-L:] for k, v in d.items()} for c, d in extra.items()} if extra else None
    rows = []
    for name in names:
        a = settings["params"][name]
        eq, tr = simulate(closes, lambda i, pre, prex, a=a, name=name: {
            c: strat_signal(name, pre[c], i, a, 1, (prex or {}).get(c)) for c in coins},
            start_cash, fee, pct, stop, take, max_coin=mc, slip=slip, trail=trail, extra=extra)
        rows.append((name, *metrics(eq), tr))
    t = Tournament(coins, start_cash, fee, pct, settings["evolve_every"], cap=int(settings.get("max_bots", 30)),
                   params=settings["params"], rng=random.Random(seed), opts=tournament_opts(settings))

    def wsig(i, pre, prex=None):
        t.step(pre, prex)
        return {c: t.vote(c) for c in coins}
    eq, tr = simulate(closes, wsig, start_cash, fee, pct, stop, take, max_coin=mc, slip=slip, trail=trail, extra=extra)
    rows.append(("Wildcard", *metrics(eq), tr))
    units = {c: (start_cash / len(coins)) / closes[c][WARM] for c in coins}
    bh = [start_cash] + [sum(units[c] * closes[c][i] for c in coins) for i in range(WARM, L)]
    rows.append(("Buy & hold (equal weight)", *metrics(bh), 0))
    return sorted(rows, key=lambda r: -r[1])


# ---------------- Bot Lab: evolve on old prices, test on newer unseen prices ----------------
def run_lab(closes, settings, train_frac=0.7, top_n=12, start_cash=1000.0, seed=None, progress=None, extra=None):
    """Evolves a tournament on the first part of the history, then tests its best bots on the
    remaining part they never saw. Returns a dict with per-bot rows and summary rows.

    progress(stage, done, total) is called now and then (stage: 'train' or 'test')."""
    fee, slip = cost_model(settings)
    pct = settings["trade_pct"] / 100
    coins = list(closes)
    L = min(len(v) for v in closes.values())
    closes = {c: v[-L:] for c, v in closes.items()}
    extra = {c: {k: v[-L:] for k, v in d.items()} for c, d in extra.items()} if extra else None
    split = int(L * train_frac)
    if split < 200 or L - split < 80:
        raise ValueError("Not enough history for a train/test split (need about 300+ candles).")
    rng = random.Random(seed)
    t = Tournament(coins, start_cash, fee, pct, settings["evolve_every"], cap=int(settings.get("max_bots", 30)),
                   params=settings["params"], rng=rng, opts=tournament_opts(settings),
                   retire_steps=int(settings.get("evolve_every", 24)) * 8)
    train = {c: v[:split] for c, v in closes.items()}
    t.warmup(train, progress=(lambda d, n: progress("train", d, n)) if progress else None,
             extra=slice_extra(extra, split))
    cands, seen = [], set()
    for v in t.vars:
        lf = t.life(v)
        if v.key() in seen:
            continue
        seen.add(v.key())
        if v.ntrades >= 3 and v.age >= 50:
            info = t.info.get(id(v), {})
            cands.append((v, lf, info))
    cands.sort(key=lambda x: -((x[1]["edge"] if x[1]["edge"] is not None else x[1]["ret"]) * (0.5 + (x[2].get("cons") or 0))))
    cands = cands[:top_n]
    test_hold = sum(closes[c][-1] / closes[c][split] for c in coins) / len(coins) - 1
    rows = []
    for k, (v, lf, info) in enumerate(cands):
        eq, tr = replay_bot(v, closes, extra, split, start_cash, fee, pct, slip)
        ret, dd = metrics(eq)
        if ret > 0 and ret > test_hold and tr >= 2:
            verdict = "Passed"
        elif ret > test_hold and tr >= 2:
            verdict = "Lost less than holding"
        elif tr < 2:
            verdict = "Too few trades"
        else:
            verdict = "Failed"
        rows.append(dict(name=v.name, params=dict(v.params), tf=v.tf, size=v.size_mult, risk=dict(v.risk),
                         traits=dict(v.traits),
                         train_ret=lf["ret"], train_edge=lf["edge"], train_status=info.get("status", ""),
                         test_ret=ret, test_edge=ret - test_hold, test_dd=dd, test_trades=tr, verdict=verdict,
                         passed=verdict == "Passed",
                         stats=dict(recent_gain_pct=round(ret * 100, 2), status="lab " + verdict.lower(),
                                    trades=tr, consistency_pct=None)))
        if progress:
            progress("test", k + 1, len(cands) + 1)

    def wsig(i, pre, prex=None):  # the whole Wildcard system keeps evolving through the unseen part
        if i < split:
            return {}
        t.step(pre, prex)
        return {c: t.vote(c) for c in coins}
    stop, take = settings["stop_loss"] / 100, settings["take_profit"] / 100
    eq, tr = simulate(closes, wsig, start_cash, fee, pct, stop, take, warm=split,
                      max_coin=settings.get("max_coin_pct", 50) / 100, slip=slip,
                      trail=float(settings.get("trail_stop", 0)) / 100, extra=extra)
    wret, wdd = metrics(eq)
    if progress:
        progress("test", len(cands) + 1, len(cands) + 1)
    return dict(rows=rows, split=split, total=L, test_hold=test_hold,
                wildcard=dict(ret=wret, dd=wdd, trades=tr), bots_tried=len(t.vars))


# ---------------- Coinbase coin list (public, no login) ----------------
def list_products(quote="USD"):
    """[{coin, price, change_24h, volume_usd}] for every coin Coinbase trades against `quote`,
    biggest 24h volume first. Uses the public Advanced Trade endpoint, falling back to the
    Exchange product list (no prices) if that fails."""
    out = []
    try:
        data = json.loads(http_get("https://api.coinbase.com/api/v3/brokerage/market/products?product_type=SPOT",
                                   15, retries=1))
        for p in data.get("products", []):
            if p.get("quote_currency_id") != quote or p.get("is_disabled") or p.get("trading_disabled"):
                continue
            if p.get("status") not in (None, "", "online"):
                continue
            coin = p.get("base_currency_id", "")
            if not valid_coin(coin):
                continue

            def num(k):
                try:
                    return float(p.get(k) or 0)
                except (TypeError, ValueError):
                    return 0.0
            price = num("price")
            out.append(dict(coin=coin, price=price, change_24h=num("price_percentage_change_24h"),
                            volume_usd=num("volume_24h") * price))
    except Exception:
        out = []
    if not out:
        data = json.loads(http_get("https://api.exchange.coinbase.com/products", 15, retries=1))
        for p in data:
            if p.get("quote_currency") == quote and p.get("status") == "online" and not p.get("trading_disabled") \
                    and valid_coin(p.get("base_currency", "")):
                out.append(dict(coin=p["base_currency"], price=None, change_24h=None, volume_usd=None))
    seen, uniq = set(), []
    for d in sorted(out, key=lambda d: -(d["volume_usd"] or 0)):
        if d["coin"] not in seen:
            seen.add(d["coin"])
            uniq.append(d)
    return uniq


STABLES = {"USDC", "USDT", "DAI", "PYUSD", "EURC", "GUSD", "USDS", "PAX", "TUSD", "FDUSD", "RLUSD"}


def scout_coins(products, n, min_volume=5_000_000):
    """The bot's own coin pick: liquid coins (enough daily volume to trade without big spreads)
    that are actually moving, skipping stablecoins. Ranked by volume x size of the 24h move."""
    pool = [d for d in products if d["coin"] not in STABLES and (d["volume_usd"] or 0) >= min_volume
            and d["change_24h"] is not None]
    pool.sort(key=lambda d: -((d["volume_usd"] or 0) ** 0.5 * (0.5 + min(abs(d["change_24h"]), 20))))
    return [d["coin"] for d in pool[:n]]


# ---------------- alerts (Discord / Telegram) ----------------
def send_alert(settings, text):
    """Posts a short message to a Discord webhook and/or a Telegram chat. Returns a list of problems."""
    problems = []
    hook = (settings.get("alert_discord") or "").strip()
    if hook:
        if not re.match(r"^https://(discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/", hook):
            problems.append("Discord: that is not a Discord webhook link.")
        else:
            try:
                req = urllib.request.Request(hook, data=json.dumps({"content": text[:1900]}).encode(),
                                             headers={"Content-Type": "application/json", "User-Agent": "cbbf"})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as ex:
                problems.append(f"Discord: {ex}")
    tok, chat = (settings.get("alert_tg_token") or "").strip(), (settings.get("alert_tg_chat") or "").strip()
    if tok and chat:
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", tok):
            problems.append("Telegram: the bot token doesn't look right (it looks like 123456:ABC...).")
        else:
            try:
                body = urllib.parse.urlencode({"chat_id": chat, "text": text[:3500]}).encode()
                req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body,
                                             headers={"User-Agent": "cbbf"})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as ex:
                problems.append(f"Telegram: {ex}")
    return problems


def alerts_configured(settings):
    return bool((settings.get("alert_discord") or "").strip()
                or ((settings.get("alert_tg_token") or "").strip() and (settings.get("alert_tg_chat") or "").strip()))

# ---------------- keep the computer awake while the bot runs ----------------
class AwakeLock:
    """Asks the operating system not to go to sleep while the bot is running (the screen may still
    turn off). A sleeping computer stops the bot and cuts its internet connection, which looks like
    the app 'disconnecting' after the idle timer runs out. Closing a laptop lid can still sleep it."""

    def __init__(self):
        self.proc, self.held = None, False

    def on(self):
        if self.held:
            return
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS | SYSTEM_REQUIRED
            elif sys.platform == "darwin" and shutil.which("caffeinate"):
                self.proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            elif shutil.which("systemd-inhibit"):
                self.proc = subprocess.Popen(["systemd-inhibit", "--what=sleep:idle", "--who=CBBF",
                                              "--why=Trading bot is running", "--mode=block", "sleep", "infinity"])
            else:
                return
            self.held = True
        except Exception:
            self.proc, self.held = None, False

    def off(self):
        if not self.held:
            return
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            elif self.proc:
                self.proc.terminate()
        except Exception:
            pass
        self.proc, self.held = None, False


# ---------------- storage ----------------
def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def state_path(live):
    return os.path.join(HOME_DIR, f"state-{'live' if live else 'paper'}.json")


def strategies_path():
    return os.path.join(HOME_DIR, "saved-strategies.json")


def save_strategies(lineup):
    save_json(strategies_path(), lineup)


def load_strategies():
    data = load_json(strategies_path(), [])
    if not isinstance(data, list):
        return []
    return [dict(d, risk=clean_risk(d.get("risk") if isinstance(d.get("risk"), dict) else None),
                 traits=clean_traits(d.get("traits") if isinstance(d.get("traits"), dict) else None))
            for d in data if isinstance(d, dict) and d.get("name") in STRATS and isinstance(d.get("params"), dict)]


# ---------------- sharing bots (export / import) ----------------
BOT_FORMAT = "cbbf-bots"
BOT_FORMATS = ("cbbf-bots", "desk-utils-bots")  # files from before the rename still import
BOT_FORMAT_VERSION = 1
MAX_BOT_FILE = 1024 * 1024  # bytes; a bot file is tiny, anything bigger is not one


def export_bots(rows, app_version="", note=""):
    """rows: tournament.board() entries to share. Returns plain data (strategy name, settings,
    trade size, and how it was doing) so someone else can load it and see how it behaves on
    their own prices. No keys, balances or trade history are included."""
    bots = []
    for r in rows:
        if r.get("stats") and "score" not in r:  # already a bot-file entry (Hall of Fame, Bot Lab)
            b = dict(name=r["name"], params=dict(r["params"]), size=round(float(r.get("size", 1.0)), 2),
                     tf=int(r.get("tf", 1)), risk=clean_risk(r.get("risk")), traits=clean_traits(r.get("traits")),
                     stats=dict(r["stats"]),
                     notes=r.get("notes", note))
            if r.get("lifetime"):
                b["lifetime"] = dict(r["lifetime"])
            bots.append(b)
            continue
        b = dict(
            name=r["name"], params=dict(r["params"]), size=round(float(r.get("size", 1.0)), 2), tf=int(r.get("tf", 1)),
            risk=clean_risk(r.get("risk")), traits=clean_traits(r.get("traits")),
            stats=dict(recent_gain_pct=None if r.get("score") is None else round(r["score"] * 100, 2),
                       consistency_pct=None if r.get("cons") is None else int(r["cons"] * 100),
                       trades=int(r.get("trades", 0)), status=r.get("status", "")),
            notes=note)
        lf = r.get("life")
        if lf:
            b["lifetime"] = lifetime_record(lf, r.get("bar_minutes"))
        bots.append(b)
    return dict(format=BOT_FORMAT, version=BOT_FORMAT_VERSION, app_version=app_version,
                exported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), bots=bots)


def lifetime_record(lf, bar_minutes=None):
    """Plain-number lifetime stats for a bot file."""
    def pct(x):
        return None if x is None else round(x * 100, 2)
    out = dict(return_pct=pct(lf.get("ret")), vs_holding_pct=pct(lf.get("edge")), win_rate_pct=pct(lf.get("winrate")),
               wins=lf.get("wins", 0), losses=lf.get("losses", 0), trades=lf.get("trades", 0),
               worst_drop_pct=pct(lf.get("maxdd")), decisions_alive=lf.get("age", 0),
               decisions_winning=lf.get("win_steps", 0), best_market=lf.get("best_mood"))
    if bar_minutes:
        out["bar_minutes"] = bar_minutes
        out["hours_winning"] = round(lf.get("win_steps", 0) * float(bar_minutes) / 60, 2)
    return out


# ---------------- Bot Collection (catch bots, give them names, rarities and levels, pick a team) ----------------
COLLECT_FIELDS = ("nickname", "rarity", "level", "xp", "caught_at", "source", "team", "shiny")
TEAM_MAX = 6
RARITIES = ("Common", "Uncommon", "Rare", "Epic", "Legendary")
RARITY_COLORS = {"Common": "#9ca3af", "Uncommon": "#22c55e", "Rare": "#3b82f6", "Epic": "#a855f7", "Legendary": "#f59e0b"}
_ADJ = ("Swift", "Silent", "Lucky", "Iron", "Golden", "Shadow", "Turbo", "Patient", "Crimson", "Frosty", "Clever", "Bold",
        "Sneaky", "Mighty", "Cosmic", "Thunder", "Velvet", "Rusty", "Neon", "Stormy", "Quiet", "Brave", "Jade", "Solar",
        "Lunar", "Atomic", "Wild", "Steady", "Nimble", "Electric", "Mystic", "Copper")
_BEAST = ("Falcon", "Otter", "Badger", "Lynx", "Mantis", "Viper", "Panda", "Heron", "Wolf", "Gecko", "Raven", "Bison",
          "Fox", "Shark", "Owl", "Tiger", "Moth", "Yak", "Cobra", "Koala", "Puma", "Hawk", "Beetle", "Marten", "Orca",
          "Ibex", "Lemur", "Jackal", "Stoat", "Kestrel", "Tapir", "Walrus")


def bot_nickname(key):
    """A stable, friendly name for a bot (the same settings always get the same name)."""
    import hashlib
    h = hashlib.sha1(repr(key).encode()).digest()
    return f"{_ADJ[h[0] % len(_ADJ)]} {_BEAST[h[1] % len(_BEAST)]}"


def is_shiny(key):
    import hashlib
    return hashlib.sha1(("shiny" + repr(key)).encode()).digest()[0] < 5  # about 1 in 50


def rarity_for(lt):
    """Rarity from a bot's track record (lifetime dict from a bot file): beating holding counts most,
    then win rate and time spent winning."""
    lt = lt or {}
    edge = float(lt.get("vs_holding_pct") or 0)
    wr = lt.get("win_rate_pct")
    hrs = float(lt.get("hours_winning") or 0)
    score = edge + (0 if wr is None else (float(wr) - 50) * 0.08) + min(hrs, 48) * 0.15
    if lt.get("source") == "Bot Lab unseen-data test" and edge > 0:
        score += 1.5  # passing on unseen prices is worth extra
    for name, need in (("Legendary", 12), ("Epic", 7), ("Rare", 3.5), ("Uncommon", 1.5)):
        if score >= need:
            return name
    return "Common"


def xp_for(lt):
    lt = lt or {}
    return int(float(lt.get("hours_winning") or 0) * 10 + float(lt.get("trades") or 0) * 2
               + max(0.0, float(lt.get("vs_holding_pct") or 0)) * 5)


def level_for(xp):
    return min(99, 1 + int((max(0, xp) / 10) ** 0.5))


def collection_path():
    return os.path.join(HOME_DIR, "collection.json")


def load_collection():
    if not os.path.exists(collection_path()):
        return []
    bots, _ = read_bots_file(collection_path())
    return bots


def save_collection(bots):
    save_json(collection_path(), dict(format=BOT_FORMAT, version=BOT_FORMAT_VERSION, kind="collection",
                                      exported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      bots=bots[:200]))


def catch(entries, source=""):
    """Adds bots to the collection, or updates ones you already have (they can level up and rank up,
    never down). Returns (new, levelled_up, [caught entries])."""
    col = load_collection()
    index = {bot_key(b): i for i, b in enumerate(col)}
    new = ups = 0
    out = []
    for e in entries:
        k = bot_key(e)
        lt = e.get("lifetime") or {}
        xp = xp_for(lt)
        r = rarity_for(lt)
        if k in index:
            old = col[index[k]]
            xp = max(xp, int(old.get("xp") or 0))
            if RARITIES.index(old.get("rarity", "Common")) > RARITIES.index(r):
                r = old.get("rarity", "Common")
            if level_for(xp) > int(old.get("level") or 1) or r != old.get("rarity"):
                ups += 1
            merged = dict(e, nickname=old.get("nickname") or bot_nickname(k), rarity=r, xp=xp, level=level_for(xp),
                          caught_at=old.get("caught_at"), source=old.get("source") or source, team=bool(old.get("team")),
                          shiny=bool(old.get("shiny")))
            col[index[k]] = merged
            out.append(merged)
        else:
            if len(col) >= 200:
                continue
            item = dict(e, nickname=bot_nickname(k), rarity=r, xp=xp, level=level_for(xp),
                        caught_at=datetime.now().isoformat(timespec="seconds"), source=source, team=False,
                        shiny=is_shiny(k))
            index[k] = len(col)
            col.append(item)
            out.append(item)
            new += 1
    save_collection(col)
    return new, ups, out


def release(keys):
    keys = set(keys)
    col = [b for b in load_collection() if bot_key(b) not in keys]
    save_collection(col)
    return col


def set_team(keys, on):
    """Adds bots to (or removes them from) your team. Returns (team size, refused because the team is full)."""
    keys, col, full = set(keys), load_collection(), 0
    size = sum(1 for b in col if b.get("team"))
    for b in col:
        if bot_key(b) in keys and bool(b.get("team")) != on:
            if on and size >= TEAM_MAX:
                full += 1
                continue
            b["team"] = on
            size += 1 if on else -1
    save_collection(col)
    return size, full


def team():
    return [b for b in load_collection() if b.get("team")]


# ---------------- Hall of Fame (long-term winners, saved automatically) ----------------
def hof_path():
    return os.path.join(HOME_DIR, "hall-of-fame.json")


def load_hof():
    """Hall of Fame entries (bot-file entries with lifetime stats), best first."""
    if not os.path.exists(hof_path()):
        return []
    bots, _ = read_bots_file(hof_path())
    return sorted(bots, key=lambda b: -((b.get("lifetime") or {}).get("vs_holding_pct") or 0))


def save_hof(bots, app_version=""):
    save_json(hof_path(), dict(format=BOT_FORMAT, version=BOT_FORMAT_VERSION, app_version=app_version,
                               exported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               kind="hall-of-fame", bots=bots[:200]))


def induct(entries, app_version=""):
    """Adds or refreshes Hall of Fame entries (bot-file dicts). Returns how many were new."""
    hof = load_hof()
    index = {bot_key(b): i for i, b in enumerate(hof)}
    new = 0
    for e in entries:
        k = bot_key(e)
        e = dict(e, inducted_at=e.get("inducted_at") or datetime.now().isoformat(timespec="seconds"))
        if k in index:
            e["inducted_at"] = hof[index[k]].get("inducted_at", e["inducted_at"])
            hof[index[k]] = e
        else:
            index[k] = len(hof)
            hof.append(e)
            new += 1
    save_hof(sorted(hof, key=lambda b: -((b.get("lifetime") or {}).get("vs_holding_pct") or 0)), app_version)
    return new


def remove_from_hof(keys):
    hof = [b for b in load_hof() if bot_key(b) not in set(keys)]
    save_hof(hof)
    return hof


def save_bots_file(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def read_bots_file(path):
    """-> (valid bots, list of problems). Never raises on bad content. Accepts a bot file, or the
    plain saved-lineup list. Only known strategy names and numeric settings get through; the
    file is data only and nothing in it is ever run."""
    problems = []
    try:
        if os.path.getsize(path) > MAX_BOT_FILE:
            return [], ["That file is too big to be a bot file."]
        with open(path) as f:
            data = json.load(f)
    except Exception as ex:
        return [], [f"Could not read the file ({ex.__class__.__name__})."]
    if isinstance(data, dict) and data.get("format") in BOT_FORMATS:
        raw = data.get("bots")
    elif isinstance(data, dict) and "name" in data and "params" in data:
        raw = [data]
    elif isinstance(data, list):
        raw = data
    else:
        return [], ["That does not look like a CBBF bot file."]
    if not isinstance(raw, list):
        return [], ["The bot list in this file is damaged."]
    good = []
    for i, d in enumerate(raw[:200], 1):
        if not isinstance(d, dict) or d.get("name") not in STRATS or d["name"] == "Dollar-cost averaging" \
                or not isinstance(d.get("params"), dict):
            problems.append(f"Skipped item {i}: unknown or unsupported strategy.")
            continue
        try:
            params = {k: float(v) for k, v in d["params"].items() if k in STRATS[d["name"]]["params"]}
            size = float(d.get("size", 1.0))
        except (TypeError, ValueError):
            problems.append(f"Skipped item {i}: settings are not numbers.")
            continue
        stats = d.get("stats") if isinstance(d.get("stats"), dict) else {}
        item = dict(name=d["name"], params=params, size=size, stats=stats, tf=clamp_tf(d.get("tf", 1)),
                    risk=clean_risk(d.get("risk") if isinstance(d.get("risk"), dict) else None),
                    traits=clean_traits(d.get("traits") if isinstance(d.get("traits"), dict) else None))
        if isinstance(d.get("lifetime"), dict):
            item["lifetime"] = {k: v for k, v in d["lifetime"].items()
                                if isinstance(v, (int, float, str)) or v is None}
        if isinstance(d.get("inducted_at"), str):
            item["inducted_at"] = d["inducted_at"][:32]
        for k in COLLECT_FIELDS:  # Bot Collection extras (plain text / numbers only)
            v = d.get(k)
            if isinstance(v, bool) or isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= 80):
                item[k] = v
        if isinstance(d.get("notes"), str):
            item["notes"] = d["notes"][:200]
        good.append(item)
    return good, problems


TRADE_FIELDS = ["time", "mode", "side", "coin", "qty", "price", "usd", "pl", "reason", "who"]


def append_trade_csv(row):
    path = os.path.join(HOME_DIR, "trades.csv")
    os.makedirs(HOME_DIR, exist_ok=True)
    if os.path.exists(path):  # an older file has fewer columns: keep it aside rather than mixing formats
        try:
            with open(path, newline="") as f:
                header = f.readline().strip()
            if header != ",".join(TRADE_FIELDS):
                os.replace(path, os.path.join(HOME_DIR, f"trades-before-{datetime.now():%Y%m%d-%H%M%S}.csv"))
        except OSError:
            pass
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in TRADE_FIELDS})


def export_trades_csv(path, trades):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        w.writeheader()
        for t in trades:
            w.writerow({k: t.get(k, "") for k in TRADE_FIELDS})


# ---------------- optional add-ons (installed on demand) ----------------
DEPENDENCIES = [  # keep in sync with requirements.txt
    dict(pip="coinbase-advanced-py>=1.7.0", module="coinbase", label="Coinbase connection",
         why="Needed for Live trading and logging in. Practice mode works without it."),
    dict(pip="keyring>=24.0", module="keyring", label="Secure login storage",
         why="Lets the app remember your Coinbase login in your computer's keychain."),
]


def dep_status():
    """[(dependency, installed?)]"""
    importlib.invalidate_caches()
    return [(d, importlib.util.find_spec(d["module"]) is not None) for d in DEPENDENCIES]


def missing_deps():
    return [d for d, ok in dep_status() if not ok]


def install_packages(specs, log=print):
    """Installs packages with pip into the Python running this app. Calls log(line) as pip works.
    Returns (ok, plain-English message)."""
    flags = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no extra console window on Windows

    def run(cmd):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, **flags)
        except OSError as e:
            return 1, str(e)
        out = []
        for line in p.stdout:
            out.append(line)
            log(line.rstrip())
        p.wait()
        return p.returncode, "".join(out)

    if importlib.util.find_spec("pip") is None:
        log("pip is missing, setting it up first...")
        run([sys.executable, "-m", "ensurepip", "--upgrade"])
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    rc, out = run(base + list(specs))
    low = out.lower()
    if rc != 0 and any(x in low for x in ("permission", "access is denied", "externally-managed", "externally managed")):
        log("Retrying for your user account only...")
        rc, out = run(base + ["--user"] + list(specs))
        low = out.lower()
    if rc == 0:
        importlib.invalidate_caches()
        try:  # a fresh per-user install folder is not on the path until the next start
            import site
            u = site.getusersitepackages()
            if os.path.isdir(u) and u not in sys.path:
                sys.path.append(u)
        except Exception:
            pass
        return True, "All set. The add-ons are installed and ready, no restart needed."
    if any(x in low for x in ("getaddrinfo", "connection", "timed out", "could not find a version", "no matching distribution",
                              "name or service not known", "network")):
        return False, "Couldn't download the add-ons. Check your internet connection and try again."
    if "externally-managed" in low or "externally managed" in low:
        return False, ("Your Python is locked by your operating system. Run this once in a terminal instead:\n"
                       f"{os.path.basename(sys.executable)} -m pip install --user --break-system-packages "
                       + " ".join(f'"{x}"' for x in specs))
    if "microsoft visual c++" in low or "failed building wheel" in low:
        return False, "An add-on needs build tools your computer doesn't have. Installing a newer Python (3.11 or 3.12) usually fixes it."
    return False, "The install didn't finish. The details are in the box above; sending them to me is the quickest way to get it fixed."


# ---------------- Coinbase login (API key) ----------------
KEYRING_SERVICE = "cbbf"
OLD_KEYRING_SERVICE = "desk-utils"


def parse_key_json(text):
    """Reads the JSON file Coinbase gives you when you create an API key.
    Older (ECDSA) files have "name"; newer (Ed25519) files may only have "id"."""
    d = json.loads(text)
    name = (d.get("name") or d.get("apiKeyName") or "").strip()
    if not name and d.get("id"):
        name = str(d["id"]).strip()  # only the bare id: the dialog asks for the full key name
    return name, d.get("privateKey") or d.get("api_secret") or ""


_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----(.*?)-----END \1-----", re.S)


def normalize_secret(s):
    """Cleans up a pasted private key: quotes, stray spaces, literal backslash-n, one-line PEMs."""
    s = (s or "").strip().strip("\"'").strip().replace("\\n", "\n")
    m = _PEM.search(s)
    if m:  # rebuild a proper PEM even if the line breaks were lost while pasting
        body = re.sub(r"\s+", "", m.group(2))
        return f"-----BEGIN {m.group(1)}-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) \
            + f"\n-----END {m.group(1)}-----\n"
    return re.sub(r"\s+", "", s)  # raw base64 keys never contain spaces or line breaks


def key_kind(secret):
    """'ecdsa', 'ed25519' or None when it does not look like a private key."""
    if "BEGIN EC PRIVATE KEY" in secret:
        return "ecdsa"
    if "PRIVATE KEY" in secret:
        return "ed25519"  # PKCS8 PEM; Coinbase uses this form for Ed25519
    if re.fullmatch(r"[A-Za-z0-9+/_-]{40,}={0,2}", secret):
        return "ed25519"  # raw base64 (32- or 64-byte seed)
    return None


def check_credentials(name, secret):
    if not name:
        return False, "The API key name box is empty. It looks like organizations/.../apiKeys/..."
    if "apiKeys" not in name:
        return False, ("That doesn't look like the full key name. It should look like "
                       "organizations/xxxx/apiKeys/xxxx (copy it from the Coinbase Developer Platform page).")
    if not secret.strip():
        return False, "The private key box is empty. Paste the private key or load the key's JSON file."
    if secret.lstrip().startswith("organizations/"):
        return False, "That's the key NAME. Put it in the first box. The private key is the long secret code."
    if key_kind(secret) is None:
        return False, ("That doesn't look like a private key. It should either start with -----BEGIN EC PRIVATE KEY----- "
                       "or be one long string of letters and numbers. Try 'Load key from JSON file' instead of pasting.")
    return True, ""


def explain_login_error(err, secret=""):
    """Plain-English reason when Coinbase says no."""
    low = str(err).lower()
    kind = key_kind(secret) if secret else None
    if any(x in low for x in ("401", "unauthorized", "invalid key", "invalid signature", "jwt")) or (
            "ed25519" in low or "could not deserialize" in low or "unsupported" in low):
        return ("Coinbase did not accept this key.\n\n"
                + ("This looks like an Ed25519 key (the newer default). Coinbase's Advanced Trade library may only "
                   "accept ECDSA keys.\n\n" if kind == "ed25519" else "")
                + "Fix: in the Coinbase Developer Platform, create a new API key, open 'Advanced settings' and choose "
                  "ECDSA as the signature algorithm, with View and Trade permissions only. Then load that key here.\n\n"
                + f"(Technical detail: {err})")
    if "403" in low or "permission" in low or "scope" in low:
        return f"The key works but is missing permissions. Give it View and Trade.\n\n(Technical detail: {err})"
    return str(err)


def keyring_available():
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def save_login(name, secret):
    """Stores the key in the OS keychain. Returns False if no keychain is available."""
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, "coinbase", json.dumps({"name": name, "secret": secret}))
        return True
    except Exception:
        return False


def load_login():
    try:
        import keyring
        raw = keyring.get_password(KEYRING_SERVICE, "coinbase")
        if not raw:  # saved before the rename: move it to the new name
            raw = keyring.get_password(OLD_KEYRING_SERVICE, "coinbase")
            if raw:
                keyring.set_password(KEYRING_SERVICE, "coinbase", raw)
                try:
                    keyring.delete_password(OLD_KEYRING_SERVICE, "coinbase")
                except Exception:
                    pass
        if raw:
            d = json.loads(raw)
            return d["name"], d["secret"]
    except Exception:
        pass
    return None


def clear_login():
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, "coinbase")
    except Exception:
        pass
    try:
        keyring.delete_password(OLD_KEYRING_SERVICE, "coinbase")
    except Exception:
        pass


# ---------------- updates from GitHub ----------------
def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def changelog_since(text, current):
    """The changelog sections newer than `current` (sections start with '## <version>' or '## Unreleased')."""
    out, keep = [], False
    for line in (text or "").splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            head = m.group(1).strip()
            nums = re.findall(r"\d+\.\d+(?:\.\d+)?", head)
            keep = bool(nums) and version_tuple(nums[0]) > version_tuple(current)
        if keep:
            out.append(line)
    return "\n".join(out).strip()


UPDATE_FILES = re.compile(r"^(?:[\w.-]+\.py|VERSION|README\.md|CHANGELOG\.md|requirements\.txt|[\w .-]+\.bat)$")


def install_update(zip_bytes, here, current_version):
    """Unpacks a GitHub branch zip over the app folder. The files being replaced are first copied to
    here/backups/<old version>-<time>/ so the update can be undone. Returns (files written, backup folder)."""
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    picks = {}
    for name in z.namelist():
        rel = name.split("/", 1)[1] if "/" in name else ""
        if rel and "/" not in rel and UPDATE_FILES.match(rel):
            picks[rel] = name
    if "main.py" not in picks or "engine.py" not in picks:
        raise RuntimeError("the download doesn't look like this app (main.py / engine.py missing)")
    for rel in ("main.py", "engine.py"):  # never install something that doesn't even compile
        compile(z.read(picks[rel]).decode("utf-8"), rel, "exec")
    backup = os.path.join(here, "backups", f"{current_version}-{datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(backup, exist_ok=True)
    for rel in os.listdir(here):
        if UPDATE_FILES.match(rel) and os.path.isfile(os.path.join(here, rel)):
            shutil.copy2(os.path.join(here, rel), os.path.join(backup, rel))
    written = []
    for rel, name in picks.items():
        tmp = os.path.join(here, rel + ".new")
        with open(tmp, "wb") as f:
            f.write(z.read(name))
        os.replace(tmp, os.path.join(here, rel))
        written.append(rel)
    return written, backup


def latest_backup(here):
    root = os.path.join(here, "backups")
    try:
        dirs = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))),
                      key=lambda d: os.path.getmtime(os.path.join(root, d)))
    except OSError:
        return None
    return os.path.join(root, dirs[-1]) if dirs else None


def restore_backup(here, backup):
    for rel in os.listdir(backup):
        if UPDATE_FILES.match(rel):
            shutil.copy2(os.path.join(backup, rel), os.path.join(here, rel))


# ---------------- CBBF's own notification sounds ----------------
SOUNDS = {  # (frequency Hz, start s, length s) notes; synthesized, so nothing to download
    "trade": [(988, 0.0, 0.09), (1319, 0.05, 0.12)],
    "notify": [(1319, 0.0, 0.35), (1760, 0.13, 0.5)],
    "hof": [(1047, 0.0, 0.22), (1319, 0.11, 0.22), (1568, 0.22, 0.22), (2093, 0.33, 0.6)],
    "problem": [(440, 0.0, 0.28), (349, 0.22, 0.42)],
}


def sound_path(kind, volume=60):
    """Writes (once) and returns a small .wav for this kind of sound: soft bell-like tones with a quick
    fade, clearly different from the Windows system sounds."""
    import math as _m
    import struct
    import wave
    vol = max(0, min(100, int(volume)))
    folder = os.path.join(HOME_DIR, "sounds")
    path = os.path.join(folder, f"{kind}-{vol}.wav")
    if os.path.exists(path):
        return path
    os.makedirs(folder, exist_ok=True)
    rate = 22050
    notes = SOUNDS.get(kind, SOUNDS["notify"])
    total = max(st + ln for _, st, ln in notes) + 0.05
    buf = [0.0] * int(rate * total)
    for f, st, ln in notes:
        a = int(st * rate)
        for i in range(int(ln * rate)):
            t = i / rate
            env = min(1.0, t / 0.008) * _m.exp(-t * 6.5 / ln)  # quick attack, bell-like decay
            v = (_m.sin(2 * _m.pi * f * t) + 0.35 * _m.sin(2 * _m.pi * 2 * f * t) + 0.12 * _m.sin(2 * _m.pi * 3 * f * t)) * env
            if a + i < len(buf):
                buf[a + i] += v
    peak = max(1e-9, max(abs(x) for x in buf))
    scale = 32767 * 0.9 * (vol / 100) / peak
    with wave.open(path + ".tmp", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(x * scale)) for x in buf))
    os.replace(path + ".tmp", path)
    return path


def play_sound(kind, volume=60):
    """Plays one of CBBF's sounds without blocking. Silently does nothing if the computer can't."""
    def run():
        try:
            path = sound_path(kind, volume)
            if sys.platform.startswith("win"):
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", path])
            else:
                for player in ("paplay", "aplay", "pw-play"):
                    if shutil.which(player):
                        subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
        except Exception:
            pass
    import threading
    threading.Thread(target=run, daemon=True).start()
