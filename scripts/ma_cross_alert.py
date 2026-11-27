"""
Moving-average crossover alert.

Checks each ticker's latest daily close and alerts when:
  - the 50-day SMA crosses the 200-day SMA (golden / death cross), or
  - the price crosses the 50-day SMA (earlier, noisier signal).

Run once a day after the market closes (see bottom of file for scheduling).

Setup (installs into the project venv, not system Python):
    .venv/Scripts/python.exe -m pip install -r requirements.txt

Phone notifications (optional, free, no account):
    1. Install the "ntfy" app on your phone.
    2. Subscribe to a topic name only you know, e.g. "ma-alerts-k7x92q".
    3. Put that same name in NTFY_TOPIC below.
Leave NTFY_TOPIC empty to just print alerts to the screen.

Each run also saves the last CHART_MONTHS of closes with both SMAs, for the
watchlist and for the holdings in the latest snapshot, to data/prices.json;
`python export.py` puts it on the dashboard's PRICE TRENDS card.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import db  # noqa: E402  (stdlib-only; it finds the database)

# Yahoo Finance tickers: LSE listings end in ".L"
TICKERS = [
    "CSPX.L",  # iShares Core S&P 500
    "VWRA.L",  # Vanguard FTSE All-World
    "EIMI.L",  # iShares Core MSCI EM IMI
    "IGLN.L",  # iShares Physical Gold
    "INRG.L",  # iShares Global Clean Energy
    "NUCL.L",  # VanEck Uranium & Nuclear
]

FAST = 50
SLOW = 200
CHECK_PRICE_VS_FAST = True  # also alert when price crosses the 50-day
NTFY_TOPIC = ""             # e.g. "ma-alerts-k7x92q"
HISTORY = "5y"              # download length; the last cross is searched within it
TREND_DAYS = 5              # gap trend is measured over this many trading days
APPROACH_DAYS = 10          # alert when the next cross is this close at the current pace

# --- Dashboard price charts -------------------------------------------------
# Holdings come from the latest asset snapshot (stock rows' tickers); the
# watchlist is TICKERS minus whatever is held. Holdings are charted, not alerted.
CHART_MONTHS = 8
CHART_SKIP: set[str] = set()   # tickers (lowercase) left off the chart
SYMBOLS = {"es3": "ES3.SI"}    # Yahoo symbol for a held ticker not in TICKERS
PRICES_PATH = ROOT / "data" / "prices.json"

# --- Colour -----------------------------------------------------------------
# Green = bullish, red = bearish, yellow = worth a look, dim = nothing happening.
# On in a terminal and in PyCharm's Run console (not a TTY, but it renders ANSI;
# PyCharm marks it with PYCHARM_HOSTED). Off for scheduled runs and redirects.
# FORCE_COLOR=1 forces it on, NO_COLOR=1 forces it off.
COLOR = not os.environ.get("NO_COLOR") and bool(
    os.environ.get("FORCE_COLOR") or os.environ.get("PYCHARM_HOSTED")
    or sys.stdout.isatty())
if COLOR and os.name == "nt":
    os.system("")  # switches the Windows console into ANSI (VT) mode

GREEN, RED, YELLOW, BOLD, DIM = "32", "31", "33", "1", "2"


def c(text: str, *codes: str) -> str:
    return f"[{';'.join(codes)}m{text}[0m" if COLOR and codes else text


def tone(text: str) -> str | None:
    """Colour for a message by what it says: bullish, bearish or a warning."""
    if text.startswith("APPROACHING"):
        return YELLOW
    if re.search(r"GOLDEN|ABOVE", text):
        return GREEN
    if re.search(r"DEATH|BELOW", text):
        return RED
    return None


def pct(value: float) -> str:
    """A signed percentage, green when positive and red when negative."""
    return c(f"{value:+.1f}%", GREEN if value > 0 else RED)


def kind(name: str) -> str:
    return c(name, GREEN if name == "GOLDEN" else RED)


def detect_signals(close: pd.Series) -> list[str]:
    """Return a list of signals that fired on the most recent close."""
    close = close.dropna()
    if len(close) < SLOW + 1:
        return [f"not enough history ({len(close)} days, need {SLOW + 1})"]

    fast = close.rolling(FAST).mean()
    slow = close.rolling(SLOW).mean()
    signals = []

    # 50 vs 200: compare yesterday's relationship with today's
    prev_above = fast.iloc[-2] > slow.iloc[-2]
    now_above = fast.iloc[-1] > slow.iloc[-1]
    if not prev_above and now_above:
        signals.append(f"GOLDEN CROSS: {FAST}d SMA crossed above {SLOW}d SMA")
    elif prev_above and not now_above:
        signals.append(f"DEATH CROSS: {FAST}d SMA crossed below {SLOW}d SMA")

    # Price vs 50-day
    if CHECK_PRICE_VS_FAST:
        prev_px_above = close.iloc[-2] > fast.iloc[-2]
        now_px_above = close.iloc[-1] > fast.iloc[-1]
        if not prev_px_above and now_px_above:
            signals.append(f"Price crossed ABOVE {FAST}d SMA")
        elif prev_px_above and not now_px_above:
            signals.append(f"Price crossed BELOW {FAST}d SMA")

    return signals


def status_line(close: pd.Series) -> str:
    close = close.dropna()
    fast = close.rolling(FAST).mean().iloc[-1]
    slow = close.rolling(SLOW).mean().iloc[-1]
    gap = (fast / slow - 1) * 100
    return (f"close {close.iloc[-1]:.2f} | {FAST}d {fast:.2f} | {SLOW}d {slow:.2f} "
            f"| {FAST}d is {pct(gap)} vs {SLOW}d")


def gap_series(close: pd.Series) -> pd.Series:
    """Fast SMA vs slow SMA in percent, only where both exist."""
    close = close.dropna()
    fast = close.rolling(FAST).mean()
    slow = close.rolling(SLOW).mean()
    return ((fast / slow - 1) * 100).dropna()


def last_cross(gap: pd.Series) -> str:
    """Describe the most recent golden/death cross within the downloaded history."""
    above = (gap > 0).to_numpy()
    flips = [i for i in range(1, len(above)) if above[i] != above[i - 1]]
    if not flips:
        side = c("above", GREEN) if above[-1] else c("below", RED)
        return (f"no cross since {gap.index[0].date()} "
                f"({FAST}d {side} {SLOW}d the whole time)")
    i = flips[-1]
    name = "GOLDEN" if above[i] else "DEATH"
    ago = len(gap) - 1 - i
    return f"past a {kind(name)} cross: {gap.index[i].date()} ({ago} trading days ago)"


def approach(gap: pd.Series) -> tuple[str, str | None]:
    """Gap trend over TREND_DAYS. Returns (description, alert text or None).

    The next cross is estimated by extrapolating the recent narrowing linearly,
    so treat it as "how soon at this pace", not a forecast.
    """
    if len(gap) < TREND_DAYS + 1:
        return "", None
    now, then = gap.iloc[-1], gap.iloc[-1 - TREND_DAYS]
    trend = f"{pct(then)} -> {pct(now)} over {TREND_DAYS}d"
    if (now > 0) != (then > 0):
        return f"crossed within the last {TREND_DAYS}d ({trend})", None
    per_day = (abs(now) - abs(then)) / TREND_DAYS
    if per_day >= 0:
        return c("gap widening (", DIM) + trend + c(")", DIM), None
    eta = abs(now) / -per_day
    nxt = "DEATH" if now > 0 else "GOLDEN"
    soon = eta <= APPROACH_DAYS
    shown = (f"gap narrowing ({trend}): {kind(nxt)} cross in "
             f"{c(f'~{eta:.0f}', YELLOW, BOLD) if soon else f'~{eta:.0f}'} "
             f"trading days at this pace")
    plain = (f"gap narrowing ({then:+.1f}% -> {now:+.1f}% over {TREND_DAYS}d): "
             f"{nxt} cross in ~{eta:.0f} trading days at this pace")
    return shown, (plain if soon else None)


def notify(title: str, body: str) -> None:
    print(f"*** {title}\n    {body}")
    if NTFY_TOPIC:
        try:
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                          data=body.encode("utf-8"),
                          headers={"Title": title}, timeout=10)
        except requests.RequestException as e:
            print(f"    (notification failed: {e})")


def held_tickers() -> list[str]:
    """Tickers in the newest snapshot: stocks_foreign tags + Local Brokerage note labels."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT category, tags, note FROM assets WHERE date = (SELECT MAX(date) FROM assets)"
            " AND category IN ('stocks_foreign', 'stocks_local')").fetchall()
    conn.close()
    out = []
    for cat, tags, note in rows:
        if cat == "stocks_foreign" and tags:
            out.append(tags.lower())
        elif cat == "stocks_local":   # "Local Brokerage: ES3 500 @ 3.90 (avg 3.21) + ..."
            out += [t.lower() for t in re.findall(r"\b([A-Z0-9]{2,6}) [\d,.]+ @", note or "")]
    return [t for t in dict.fromkeys(out) if t not in CHART_SKIP]


def yahoo_symbol(ticker: str) -> str | None:
    if ticker in SYMBOLS:
        return SYMBOLS[ticker]
    return next((t for t in TICKERS if t.split(".")[0].lower() == ticker), None)


def chart_series(symbol: str, close: pd.Series) -> dict:
    """The last CHART_MONTHS of closes and both SMAs (computed on full history)."""
    close = close.dropna()
    fast = close.rolling(FAST).mean()
    slow = close.rolling(SLOW).mean()
    keep = close.index >= close.index[-1] - pd.DateOffset(months=CHART_MONTHS)
    r = lambda s: [None if pd.isna(v) else round(float(v), 4) for v in s[keep]]
    try:
        ccy = yf.Ticker(symbol).fast_info["currency"]
    except Exception:
        ccy = None
    return {"symbol": symbol, "currency": ccy,
            "dates": [d.strftime("%Y-%m-%d") for d in close.index[keep]],
            "close": r(close), "fast": r(fast), "slow": r(slow)}


def save_prices(holdings: list[dict], watchlist: list[dict]) -> None:
    data = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "months": CHART_MONTHS, "fast": FAST, "slow": SLOW,
            "holdings": holdings, "watchlist": watchlist}
    PRICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PRICES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(PRICES_PATH)
    print(c(f"saved {len(holdings)} holding + {len(watchlist)} watchlist charts "
            f"to {PRICES_PATH.relative_to(ROOT)} (python export.py to show them)", DIM))


def main() -> int:
    try:
        held = held_tickers()
    except Exception as e:  # no database yet, etc. -- alerts still run
        print(c(f"couldn't read holdings from the database ({e})", YELLOW))
        held = []
    held_syms = {}
    for t in held:
        sym = yahoo_symbol(t)
        if sym:
            held_syms[sym] = t
        else:
            print(c(f"{t.upper()}: no Yahoo symbol -- add it to SYMBOLS to chart it", YELLOW))
    charts = {}

    for ticker in list(dict.fromkeys(TICKERS + list(held_syms))):
        try:
            data = yf.download(ticker, period=HISTORY, interval="1d",
                               auto_adjust=True, progress=False)
        except Exception as e:  # network / Yahoo errors
            print(c(f"{ticker}: download failed ({e})", YELLOW))
            continue
        if data.empty:
            print(c(f"{ticker}: no data returned", YELLOW))
            continue

        close = data["Close"]
        if isinstance(close, pd.DataFrame):  # newer yfinance returns a frame
            close = close.iloc[:, 0]

        charts[ticker] = chart_series(ticker, close)
        if ticker not in TICKERS:   # a holding fetched only for its chart
            continue

        last_date = close.dropna().index[-1].date()
        signals = detect_signals(close)
        if len(close.dropna()) > SLOW:
            print(f"{c(ticker, BOLD)} ({last_date}): {status_line(close)}")
            gap = gap_series(close)
            print(f"    {last_cross(gap)}")
            trend, warn = approach(gap)
            if trend:
                print(f"    {trend}")
            if warn:
                signals.append(f"APPROACHING: {warn}")
        for s in signals:
            notify(f"{ticker} {last_date}", s)
        if not signals:
            print(c("    no crossover today", DIM))

    holdings = [dict(charts[s], ticker=held_syms[s].upper()) for s in held_syms if s in charts]
    watchlist = [dict(charts[s], ticker=s.split(".")[0]) for s in TICKERS
                 if s in charts and s not in held_syms]
    if holdings or watchlist:
        save_prices(holdings, watchlist)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# --- Scheduling -------------------------------------------------------------
# Mac/Linux (cron), weekdays at 17:15 London time — run `crontab -e` and add:
#   15 17 * * 1-5  /path/to/project/.venv/bin/python /path/to/project/scripts/ma_cross_alert.py
# Windows: Task Scheduler > Create Basic Task > Daily, weekdays,
#   action "Start a program":
#     program:   <project folder>\ma_cross_alert.bat
#   (the .bat runs this with the project venv's Python; system Python lacks
#   pandas/yfinance/requests)
