"""
make_demo_data.py — seed a fictional dataset so the project can be tried out.

    python scripts/make_demo_data.py            # build data/finances.db + data/prices.json
    python scripts/make_demo_data.py --sample   # ...and refresh design/sample_data.json
    python scripts/make_demo_data.py --force    # overwrite an existing database

Everything it writes is made up: a fictional person in Singapore logging
everyday spending, a few subscriptions, a holiday, monthly transfers to a
broker, and month-end asset snapshots from 2026-01. A job (salary) starts in
2026-09, so the dashboard shows both the "runway" and the "years to FI" modes.
Prices are a seeded random walk, not market data. The output is identical on
every run (fixed seed).

It refuses to touch an existing data/finances.db unless --force is given, so it
can never overwrite a real ledger by accident. Afterwards: python serve.py
"""
import argparse
import calendar
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import db  # noqa: E402
import export  # noqa: E402
from keywords import classify, classify_income, norm_tags  # noqa: E402

SEED = 20260101
START, TODAY = date(2026, 1, 1), date(2026, 11, 24)
OPENING_BANK = 34_000.00
PRICES_PATH = ROOT / "data" / "prices.json"
SAMPLE_PATH = ROOT / "design" / "sample_data.json"

# ticker: (Yahoo symbol, currency, IBKR description, start price, annual drift, annual vol)
FOREIGN = {
    "cspx": ("CSPX.L", "USD", "ISHARES CORE S&P 500", 540.0, 0.09, 0.15),
    "vwra": ("VWRA.L", "USD", "VANGUARD FTSE ALL-WORLD", 122.0, 0.08, 0.14),
    "eimi": ("EIMI.L", "USD", "ISHARES CORE MSCI EM IMI", 33.0, 0.07, 0.18),
}
LOCAL = {"es3": ("ES3.SI", "SGD", "STI ETF", 3.55, 0.06, 0.11)}
WATCH = {
    "igln": ("IGLN.L", "USD", "ISHARES PHYSICAL GOLD", 48.0, 0.10, 0.14),
    "inrg": ("INRG.L", "USD", "ISHARES GLOBAL CLEAN ENERGY", 8.5, 0.00, 0.26),
    "nucl": ("NUCL.L", "USD", "VANECK URANIUM & NUCLEAR", 31.0, 0.12, 0.28),
}
# Holdings before the first snapshot: ticker -> (quantity, average cost)
OPENING_POSITIONS = {"cspx": (11.0, 498.20), "vwra": (34.0, 109.75), "eimi": (70.0, 30.12)}
ES3_POSITION = (500, 3.21)
BUY_SPLIT = {"cspx": 0.5, "vwra": 0.3, "eimi": 0.2}


def month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def months():
    y, m = START.year, START.month
    while date(y, m, 1) <= TODAY:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


# ---------- prices ----------

def trading_days(first, last):
    d = first
    while d <= last:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def price_paths(rng):
    """{ticker: {date: close}} — a geometric random walk per ticker."""
    days = list(trading_days(date(2025, 1, 2), TODAY))
    out = {}
    for t, (_s, _c, _d, p0, mu, sigma) in {**FOREIGN, **LOCAL, **WATCH}.items():
        p, path = p0, {}
        dt = 1 / 252
        for d in days:
            p *= pow(2.718281828, (mu - sigma ** 2 / 2) * dt + sigma * (dt ** 0.5) * rng.gauss(0, 1))
            path[d] = round(p, 4)
        out[t] = path
    return out


def close_on(path, d):
    return path[max(k for k in path if k <= d)]


def chart(ticker, spec, path, months_back=8, fast=50, slow=200):
    symbol, ccy = spec[0], spec[1]
    days = sorted(path)
    closes = [path[d] for d in days]
    sma = lambda n, i: round(sum(closes[i - n + 1:i + 1]) / n, 4) if i + 1 >= n else None
    y, m = TODAY.year, TODAY.month - months_back
    while m < 1:
        y, m = y - 1, m + 12
    cut = date(y, m, min(TODAY.day, calendar.monthrange(y, m)[1]))
    keep = [i for i, d in enumerate(days) if d >= cut]
    return {"symbol": symbol, "currency": ccy, "ticker": ticker.upper(),
            "dates": [days[i].isoformat() for i in keep],
            "close": [closes[i] for i in keep],
            "fast": [sma(fast, i) for i in keep],
            "slow": [sma(slow, i) for i in keep]}


# ---------- ledger ----------

def expense(conn, d, category, amount, note):
    db.upsert(conn, "expenses", d.isoformat(), category, round(amount, 2), note,
              norm_tags(classify(note)[1]))
    return round(amount, 2)


def income(conn, d, category, amount, note, tags=None):
    tags = tags if tags is not None else classify_income(note)[1]
    db.upsert(conn, "income", d.isoformat(), category, round(amount, 2), note, norm_tags(tags))
    return round(amount, 2)


def everyday(conn, rng, d):
    """One day's small spending. Returns the total."""
    spent = 0.0
    if rng.random() < 0.85:
        for _ in range(rng.choice((1, 1, 2, 2, 3))):
            note, lo, hi = rng.choice([
                ("hawker lunch", 4.5, 8), ("coffee", 2.2, 6.8), ("breakfast", 5.5, 9),
                ("bubble tea", 3.8, 6.5), ("soy milk", 2.5, 4.5), ("dinner", 8, 24),
                (None, 3, 12), ("vending machine", 1.5, 3)])
            spent += expense(conn, d, "food", rng.uniform(lo, hi), note)
    if d.weekday() == 5:
        spent += expense(conn, d, "food", rng.uniform(22, 65), "groceries")
    if d.weekday() < 5 and rng.random() < 0.7:
        spent += expense(conn, d, "transport", rng.uniform(1.4, 3.9), "public transport")
    if rng.random() < 0.06:
        spent += expense(conn, d, "transport", rng.uniform(11, 26), "RideNow")
    if rng.random() < 0.10:
        note = rng.choice(["ShopMall", "Dollar Depot", "Homeware Hub", "ShopMall"])
        spent += expense(conn, d, "misc", rng.uniform(4, 38), note)
    if rng.random() < 0.035:
        spent += expense(conn, d, "leisure", rng.uniform(13, 15.5), "movie")
    return spent


def fixed(conn, rng, d):
    """Recurring and one-off planned costs on day `d`. Returns (spent, invested)."""
    spent = invested = 0.0
    y, m, day = d.year, d.month, d.day
    if day == 3:
        spent += expense(conn, d, "health", 98.00, "ABC Fitness gym membership")
    if day == 12:
        spent += expense(conn, d, "software", 27.50, "CloudNote subscription")
    if day == 15:
        spent += expense(conn, d, "bills", 10.00, "Nova Mobile plan")
    if day == 5:
        invested += expense(conn, d, "investment", 1000.00, "IBKR transfer")
    if m >= 8 and day == 20:            # not configured as a subscription: the
        spent += expense(conn, d, "learning", 160.00, "piano lesson")  # radar detects it
    one_offs = {
        (2, 10): [("gifts", 188.00, "CNY red packets")],
        (3, 2): [("software", 59.90, "SafeGuard Antivirus annual plan")],
        (3, 9): [("investment", 5000.00, "SSB application")],
        (3, 10): [("investment", 2.00, "SSB fee")],
        (3, 21): [("learning", 89.00, "online data course")],
        (3, 28): [("clothes", 79.80, "Basics Apparel")],
        (4, 6): [("clothes", -39.90, "Basics Apparel refund")],
        (4, 18): [("leisure", 32.00, "escape room")],
        (4, 25): [("learning", 24.00, "music theory book")],
        (5, 14): [("holiday", 642.00, "Bali trip flights")],
        (5, 15): [("holiday", 884.00, "Bali trip hotel")],
        (5, 22): [("holiday", 517.40, "Bali trip food & transport")],
        (6, 27): [("health", 120.00, "dental scaling")],
        (7, 11): [("leisure", 79.00, "board game")],
        (7, 19): [("leisure", 24.60, "Craft Corner supplies")],
        (8, 3): [("investment", 5000.00, "T-bill purchase")],
        (8, 29): [("gifts", 45.00, "birthday gift")],
        (9, 6): [("clothes", 112.70, "Basics Apparel")],
        (10, 14): [("health", 42.00, "GP visit")],
        (11, 7): [("bills", 5.00, "Nova Mobile 1-for-1 top-up")],
        (11, 15): [("clothes", 64.90, "Basics Apparel")],
    }
    for category, amount, note in one_offs.get((m, day), []):
        amt = expense(conn, d, category, amount, note)
        if category == "investment":
            invested += amt
        else:
            spent += amt
    return spent, invested


# ---------- build ----------

def build(conn, rng):
    paths = price_paths(rng)
    bank = OPENING_BANK
    positions = {t: list(v) for t, v in OPENING_POSITIONS.items()}
    ssb, tbill = 2000.00, 0.0
    mmf = 1500.00
    ibkr_cash = 12.34
    usd, gbp = 1.345, 1.705

    for y, m in months():
        flow = 0.0                       # money in minus money out of the bank this month
        for day in range(1, calendar.monthrange(y, m)[1] + 1):
            d = date(y, m, day)
            if d > TODAY:
                break
            flow -= everyday(conn, rng, d)
            spent, invested = fixed(conn, rng, d)
            flow -= spent + invested
            if invested and d.day == 5:
                # The IBKR transfer is invested the same day, split across the ETFs.
                for t, share in BUY_SPLIT.items():
                    price = close_on(paths[t], d)
                    qty = round(1000.00 * share / usd / price, 4)
                    q0, avg0 = positions[t]
                    positions[t] = [round(q0 + qty, 4), (q0 * avg0 + qty * price) / (q0 + qty)]
        if m == 3:
            ssb = 7000.00
        if m == 8:
            tbill = 5000.00

        # income
        end = month_end(y, m)
        last = min(end, TODAY)
        flow += income(conn, last, "interest", round(rng.uniform(2, 9), 2), "bank interest")
        if m == 2:
            flow += income(conn, date(y, 2, 17), "gift", 388.00, "CNY ang bao")
        if m in (3, 4, 5):
            flow += income(conn, date(y, m, 24), "hustle", 240.00, "tutoring", ["tuition"])
        if m == 7:
            flow += income(conn, date(y, 7, 1), "interest", 27.00, "SSB interest")
            flow += income(conn, date(y, 7, 16), "hustle", 150.00, "logo design", ["design"])
        if m == 10:
            flow += income(conn, date(y, 10, 21), "investment", 200.00,
                           "uninvested IBKR cash back to savings")
        if m >= 9 and date(y, m, 25) <= TODAY:
            flow += income(conn, date(y, m, 25), "salary", 4100.00, "salary")
        bank = round(bank + flow, 2)
        if end > TODAY:
            break                        # the current month has no snapshot yet

        # month-end snapshot
        usd = round(usd * (1 + rng.uniform(-0.012, 0.012)), 4)
        gbp = round(gbp * (1 + rng.uniform(-0.015, 0.015)), 4)
        mmf = round(mmf * 1.0028, 2)
        rows = []
        foreign = []
        for t, (q, avg) in positions.items():
            close = close_on(paths[t], end)
            value = round(q * close * usd, 2)
            note = (f"{FOREIGN[t][2]}: {q:g} @ USD {close:.2f}; avg {avg:.2f}; "
                    f"P&L {q * (close - avg):+.2f}")
            foreign.append((t, value, note))
        es3_close = close_on(paths["es3"], end)
        es3_value = round(ES3_POSITION[0] * es3_close, 2)
        bonds = ssb + tbill
        bond_note = "CDP: SBJAN26 2,000" + (" + SBMAR26 5,000" if m >= 3 else "") + \
                    (" + BS26108Z 5,000" if tbill else "") + " (face value)"
        fx_note = f"GBP 120.00 @ {gbp:.4f}"
        if m < 7:                        # early months: one untagged total per category
            rows += [("stocks_foreign", "", round(sum(v for _, v, _ in foreign) + ibkr_cash, 2), None),
                     ("stocks_local", "", es3_value, None),
                     ("cash_local", "", bank, None),
                     ("cash_local", "mmf", mmf, None),
                     ("bonds", "", bonds, bond_note if m >= 3 else None)]
        else:                            # from 2026-07: per holding, as snapshot.py writes them
            rows += [("stocks_foreign", t, v, n) for t, v, n in foreign]
            rows += [("cash_local", "ibkr", ibkr_cash, "IBKR cash (SGD)"),
                     ("stocks_local", "", es3_value,
                      f"Local Brokerage: ES3 {ES3_POSITION[0]} @ {es3_close:.2f} (avg {ES3_POSITION[1]:.2f})"),
                     ("cash_local", "mmf", mmf,
                      f"Local Brokerage money-market fund: {mmf:.2f} units @ 1.0000"),
                     ("cash_local", "", bank, "Local Bank Savings Account closing balance"),
                     ("bonds", "", bonds, bond_note)]
        rows.append(("cash_foreign", "", round(120.00 * gbp, 2), fx_note))
        conn.executemany(
            "INSERT OR REPLACE INTO assets (date, category, amount, note, tags) VALUES (?,?,?,?,?)",
            [(end.isoformat(), c, a, n, t) for c, t, a, n in rows])
    conn.commit()
    return paths


def main():
    ap = argparse.ArgumentParser(description="Seed the project with a fictional demo dataset.")
    ap.add_argument("--force", action="store_true", help="overwrite an existing data/finances.db")
    ap.add_argument("--sample", action="store_true", help="also rewrite design/sample_data.json")
    args = ap.parse_args()
    if db.DB_PATH.exists():
        if not args.force:
            raise SystemExit(f"{db.DB_PATH.relative_to(ROOT)} already exists — refusing to overwrite it. "
                             "Use --force if it really is demo data.")
        db.DB_PATH.unlink()

    rng = random.Random(SEED)
    with db.connect() as conn:
        paths = build(conn, rng)
    conn.close()

    holdings = [chart(t, FOREIGN.get(t) or LOCAL[t], paths[t]) for t in (*FOREIGN, *LOCAL)]
    watchlist = [chart(t, WATCH[t], paths[t]) for t in WATCH]
    PRICES_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "months": 8, "fast": 50, "slow": 200, "holdings": holdings, "watchlist": watchlist,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {db.DB_PATH.relative_to(ROOT)} and {PRICES_PATH.relative_to(ROOT)} (fictional data).")
    export.main()
    if args.sample:
        SAMPLE_PATH.write_text(json.dumps(export.build(), indent=1), encoding="utf-8")
        print(f"Wrote {SAMPLE_PATH.relative_to(ROOT)}.")
    print("Now run: python serve.py")


if __name__ == "__main__":
    main()
