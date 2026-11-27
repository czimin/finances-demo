"""
snapshot.py — the monthly asset snapshot, read straight from your statements.

    python snapshot.py                  # find statements, preview, confirm, write
    python snapshot.py --dry-run        # parse + preview only; asks and writes nothing
    python snapshot.py --month 2026-09  # pick the month instead of being asked

Drop the month's statements into data/ under any file name — each is recognised
by its contents — then run this:

    stocks_foreign  IBKR Activity Statement CSV            one row per holding, tag = ticker
    stocks_local    Local Brokerage Monthly Statement PDF  closing value less money-market
                                                           fund/cash
    bonds           CDP Account Statement PDF              sum of bond face values
    cash_local      Local Bank account statement PDF       closing balance (+ IBKR /
                                                           money-market fund / brokerage cash)
    cash_foreign    typed in, in its own currency ("GBP 120.00")

(The same Local Bank statement feeds import_statement.py, which logs its transactions.)
With --ibkr-mcp and no IBKR CSV, the holdings are read live from IBKR's MCP server
instead (scripts/ibkr_mcp.py; run its `login` once first).

The snapshot is dated the last day of the statement month. The USD->SGD rate
comes from the IBKR statement's own totals; other currencies are looked up at
the month-end rate (api.frankfurter.dev — this script's only network call
without --ibkr-mcp, and it sends nothing but the date and the currency code). Any category without a statement is
typed in as an SGD total; skip one (Enter) and the month is marked partial on
the dashboard until you fill it in by running this again.

Snapshots are absolute: re-running a month REPLACES what a source wrote before,
never adds to it. Before anything is written you see every row, the rates used,
and warnings for anything that moved implausibly since the last snapshot. The
database is backed up to data/backups/ first, and the dashboard data is
re-exported afterwards.
"""
import argparse
import calendar
import csv
import json
import re
import urllib.request
from datetime import date, datetime

import export
from add_expense import parse_amount
from db import ROOT, backup, connect, money
from import_statement import ParseError as BankParseError
from import_statement import read_local_bank
from keywords import ASSET_CATEGORIES, LOCAL_TICKERS
from scripts.pdftext import extract_text

DATA_DIR = ROOT / "data"
BASE = "SGD"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
SYMBOLS = {"£": "GBP", "€": "EUR", "¥": "JPY"}
# A move bigger than either of these since the last snapshot gets a warning.
WARN_PCT, WARN_SGD = 0.30, 2000


class ParseError(Exception):
    """A statement was recognised but its numbers couldn't be read reliably."""


def num(s: str) -> float:
    return float(s.replace(",", ""))


def month_end(year: int, month: int) -> str:
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


# ---------- FX ----------

_rates: dict[tuple[str, str], float] = {}


def fx_rate(ccy: str, on: str, interactive: bool = True) -> float:
    """SGD per 1 `ccy` on (or just before) `on`. Looked up once, then cached."""
    if ccy == BASE:
        return 1.0
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        raise ParseError(f"not a currency code: {ccy!r}")
    if (ccy, on) not in _rates:
        url = f"https://api.frankfurter.dev/v1/{on}?from={ccy}&to={BASE}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "snapshot.py"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                rate = float(json.load(resp)["rates"][BASE])
            if not 0 < rate < 1e6:
                raise ValueError(rate)
        except Exception as exc:            # offline, unknown currency, odd reply
            if not interactive:
                raise ParseError(f"couldn't look up {ccy}->{BASE} ({exc})")
            print(f"  Couldn't look up the {ccy}->{BASE} rate for {on} ({exc}).")
            rate = None
            while not rate or rate <= 0:
                rate = parse_amount(input(f"  {BASE} per 1 {ccy}: ").strip())
        _rates[(ccy, on)] = rate
    return _rates[(ccy, on)]


# ---------- statement readers ----------
# Each returns {"kind", "date", "rows": [(category, tag, amount, note)], ...}
# or None when the file is some other kind of document.

def read_ibkr(path, interactive=True):
    with open(path, encoding="utf-8-sig", newline="") as f:
        raw = [r for r in csv.reader(f) if len(r) > 2]
    # Multi-section CSV: column 0 names the section, column 1 says whether the
    # line is that section's Header, a Data row, or a Total.
    header, sec = {}, {}
    for r in raw:
        if r[1] == "Header":
            header[r[0]] = r[2:]
        elif r[0] in header:
            sec.setdefault((r[0], r[1]), []).append(dict(zip(header[r[0]], r[2:])))
    fields = {r.get("Field Name"): r.get("Field Value") for r in sec.get(("Statement", "Data"), [])}
    if fields.get("Title") != "Activity Statement":
        return None

    try:
        end = fields["Period"].split(" - ")[-1]
        start = fields["Period"].split(" - ")[0]
        d1 = datetime.strptime(end.strip(), "%B %d, %Y").date()
        d0 = datetime.strptime(start.strip(), "%B %d, %Y").date()
    except (KeyError, ValueError):
        raise ParseError("no readable Period in the Statement section")
    warnings = []
    if d1.isoformat() != month_end(d1.year, d1.month):
        warnings.append(f"IBKR statement ends {d1}, not on a month-end — dated to the month-end anyway")
    if (d0.year, d0.month) != (d1.year, d1.month):
        warnings.append(f"IBKR statement covers {d0} to {d1} — more than one month; using its end")
    snap = month_end(d1.year, d1.month)

    acct = {r.get("Field Name"): r.get("Field Value") for r in sec.get(("Account Information", "Data"), [])}
    base = acct.get("Base Currency", BASE)
    if base != BASE:
        raise ParseError(f"account base currency is {base}, expected {BASE}")

    # Summary rows only: Lot rows are the per-purchase breakdown of the same
    # holding and would multiply-count it.
    positions = [p for p in sec.get(("Open Positions", "Data"), [])
                 if p.get("DataDiscriminator") == "Summary"]
    totals = {t["Currency"]: num(t["Value"]) for t in sec.get(("Open Positions", "Total"), [])}
    names = {i["Symbol"]: i["Description"]
             for i in sec.get(("Financial Instrument Information", "Data"), [])}

    # FX: with a single foreign currency the statement's own two totals give the
    # exact rate it valued the account at. Otherwise look each one up.
    foreign = {p["Currency"] for p in positions} - {base}
    rates = {base: 1.0}
    if len(foreign) == 1 and base in totals and totals.get(*foreign, 0) > 0:
        (ccy,) = foreign
        rates[ccy] = totals[base] / totals[ccy]

    def rate(ccy):
        if ccy not in rates:
            rates[ccy] = fx_rate(ccy, snap, interactive)
        return rates[ccy]

    rows = []
    for p in positions:
        ccy, sym = p["Currency"], p["Symbol"]
        note = (f"{names.get(sym, sym)}: {p['Quantity']} @ {ccy} {p['Close Price']}; "
                f"avg {num(p['Cost Price']):.2f}; P&L {num(p['Unrealized P/L']):+.2f}")
        rows.append(("stocks_foreign", sym.lower(), round(num(p["Value"]) * rate(ccy), 2), note))
    if base in totals and abs(sum(r[2] for r in rows) - totals[base]) > 0.05:
        warnings.append(f"positions sum to {money(sum(r[2] for r in rows))} but the statement's "
                        f"own {base} total is {money(totals[base])}")

    cash = {}
    for c in sec.get(("Cash Report", "Data"), []):
        ccy = c.get("Currency", "")
        if c.get("Currency Summary") == "Ending Cash" and re.fullmatch(r"[A-Z]{3}", ccy):
            sgd = num(c["Total"]) * rate(ccy)
            if abs(sgd) >= 0.01:          # IBKR leaves fraction-of-a-cent residue
                cat = "cash_local" if ccy == base else "cash_foreign"
                amt, note = cash.get(cat, (0, "IBKR cash"))
                cash[cat] = (amt + sgd, f"{note} {ccy} {num(c['Total']):,.2f}" if ccy != base
                             else "IBKR cash (SGD)")
    rows += [(cat, "ibkr", round(amt, 2), note) for cat, (amt, note) in cash.items()]

    return {"kind": "ibkr", "date": snap, "rows": rows, "warnings": warnings,
            "rates": {c: r for c, r in rates.items() if c != base and r}}


def _lines(text):
    return [l.strip() for l in text.splitlines() if l.strip()]


def read_local_brokerage(text):
    if "Local Brokerage" not in text:
        return None
    if "Monthly Account Statement" not in text:
        raise ParseError("a Local Brokerage document, but not a Monthly Account Statement "
                         "(a trade confirmation carries no balance)")
    L = _lines(text)
    m = re.search(r"Period:\s*\d\d/\d\d/\d{4}\s*-\s*\d\d/(\d\d)/(\d{4})", text)
    if not m:
        raise ParseError("no statement period found")
    snap = month_end(int(m.group(2)), int(m.group(1)))
    try:
        i = next(k for k, l in enumerate(L) if l.startswith("NET ACCOUNT VALUE"))
        if BASE not in L[i]:
            raise ParseError(f"net account value isn't in {BASE}: {L[i]}")
        o = L.index("Opening", i)
        labels = L[i + 1:o]
        n = len(labels)
        if L[o + 1 + n] != "Closing":
            raise ValueError
        opening = dict(zip(labels, map(num, L[o + 1:o + 1 + n])))
        closing = dict(zip(labels, map(num, L[o + 2 + n:o + 2 + 2 * n])))
        total = closing["Total Value"]
    except (StopIteration, ValueError, KeyError, IndexError):
        raise ParseError("couldn't read the NET ACCOUNT VALUE table")
    # The table's own arithmetic is the proof the columns lined up.
    if abs(sum(v for k, v in closing.items() if k != "Total Value") - total) > 0.02:
        raise ParseError("NET ACCOUNT VALUE components don't add up to Total Value")

    mmf = closing.get("Market Value (MMF)", 0.0)
    cash = closing.get("Total Cash", 0.0)
    stocks = round(total - mmf - cash, 2)

    # Notes are best-effort detail; the amounts above don't depend on them.
    holdings, mb_note = [], "Local Brokerage money-market fund"
    try:
        p = L.index("PORTFOLIO SUMMARY")
        k = L.index("Exchange", p) + 1
        name = []
        while k < len(L) and L[k] != "MMF" and not L[k].startswith("of"):
            if re.fullmatch(r"-?[\d,]+(\.\d+)?", L[k]):
                qty, avg, close = L[k], L[k + 2], L[k + 4]
                full = " ".join(name)
                label = next((t for key, t in LOCAL_TICKERS.items() if key in full.lower()), full)
                holdings.append(f"{label} {qty} @ {close} (avg {avg})")
                k, name = k + 8, []
            else:
                name.append(L[k])
                k += 1
        u = L.index("Units", p)
        if L[u + 1:u + 3] == ["Closing Price", "Currency"]:
            mb_note += f": {L[u + 4]} units @ {L[u + 5]}"
    except (ValueError, IndexError):
        pass

    rows = []
    if stocks or holdings:
        rows.append(("stocks_local", "", stocks,
                     "Local Brokerage: " + (" + ".join(holdings) or "closing total value")))
    if abs(mmf) >= 0.01:      # a money-market fund is cash, not stocks
        rows.append(("cash_local", "mmf", mmf, mb_note))
    if abs(cash) >= 0.01:
        rows.append(("cash_local", "local_brokerage", cash, "Local Brokerage uninvested cash"))
    return {"kind": "local_brokerage", "date": snap, "rows": rows, "warnings": [],
            "opening_total": opening.get("Total Value")}


def read_cdp(text):
    if "Central Depository" not in text:
        return None
    m = re.search(rf"^\s*({'|'.join(MONTHS)})\s+(\d{{4}})\s*$", text, re.M)
    if not m:
        raise ParseError("no statement month found")
    snap = month_end(int(m.group(2)), MONTHS.index(m.group(1)) + 1)
    L = _lines(text)
    try:
        k = L.index("Bonds")
    except ValueError:
        return {"kind": "cdp", "date": snap, "rows": [], "warnings": ["CDP statement lists no bonds"]}
    parts, total = [], 0.0
    while k < len(L) and not L[k].startswith("TOTAL"):
        # A holding is "<security> <code>" followed by its six columns:
        # free, blocked, balance, closing price, market value, face value.
        if re.fullmatch(r"[A-Z0-9]{4,}\s+[A-Z0-9]{6,}", L[k]):
            cols = L[k + 1:k + 7]
            # SSBs and T-bills aren't exchange-traded, so market value reads
            # "NA" — face value is the measure. Fall back to the balance.
            value = next((num(c) for c in (cols[5], cols[2]) if re.fullmatch(r"[\d,]+(\.\d+)?", c)), None)
            if len(cols) < 6 or value is None:
                raise ParseError(f"couldn't read the row for {L[k]}")
            parts.append(f"{L[k].split()[0]} {value:,.0f}")
            total += value
            k += 7
        else:
            k += 1
    if not parts:
        raise ParseError("found the Bonds table but no holdings in it")
    return {"kind": "cdp", "date": snap, "warnings": [],
            "rows": [("bonds", "", round(total, 2), "CDP: " + " + ".join(parts) + " (face value)")]}


def read_bank(path):
    """The bank statement's closing balance is the month's bank cash."""
    s = read_local_bank(path)
    if not s:
        return None
    end = date.fromisoformat(s["end"])
    snap = month_end(end.year, end.month)
    warnings = [] if s["end"] == snap else [
        f"bank statement ends {s['end']}, not on the month-end — its closing balance is used anyway"]
    return {"kind": "bank", "date": snap, "warnings": warnings,
            "rows": [("cash_local", "", s["closing"], f"Local Bank {s['account']} closing balance")]}


def find_statements(interactive):
    """Read every statement in data/. Returns ({snapshot date: {kind: stmt}}, problems)."""
    found, problems = {}, []
    for path in sorted(DATA_DIR.glob("*.csv")) + sorted(DATA_DIR.glob("*.pdf")):
        try:
            if path.suffix.lower() == ".csv":
                stmt = read_ibkr(path, interactive)
            else:
                try:
                    text = extract_text(path)
                except ValueError:      # flagged as encrypted: only the bank's PDFs are
                    text = ""
                stmt = read_local_brokerage(text) or read_cdp(text) or read_bank(path)
        except (ParseError, BankParseError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        except (OSError, ValueError, KeyError, IndexError, csv.Error) as exc:
            problems.append(f"{path.name}: unreadable ({exc.__class__.__name__}: {exc})")
            continue
        if stmt:
            stmt["file"] = path.name
            found.setdefault(stmt["date"], {})[stmt["kind"]] = stmt
    return found, problems


LIVE_DAYS = 10        # live IBKR data may stand in for a month-end this many days after it


def ibkr_live(snap, stmts, interactive):
    """IBKR holdings read live through IBKR's MCP server, as a statement-like source.

    Only used when no Activity Statement CSV covers the month (the statement is
    exact; live data is "now"), and only in the first LIVE_DAYS after the
    month-end, so a month isn't recorded with prices from weeks later.
    """
    if "ibkr" in stmts:
        print("  (IBKR Activity Statement found — using it rather than live MCP data)")
        return None
    age = (date.today() - date.fromisoformat(snap)).days
    if not 0 <= age <= LIVE_DAYS:
        print(f"!! --ibkr-mcp: live data can only stand in for a month-end in the {LIVE_DAYS} days "
              f"after it, and {snap} is {age} days away — use the Activity Statement CSV")
        return None
    from scripts import ibkr_mcp      # imported only when asked for: it makes network calls
    try:
        rows = ibkr_mcp.snapshot_rows(snap, lambda ccy, on: fx_rate(ccy, on, interactive), BASE)
    except (ibkr_mcp.McpError, ibkr_mcp.ParseError, ParseError) as exc:
        print(f"!! IBKR MCP: {exc} — falling back to typing the total")
        return None
    return {"kind": "ibkr_mcp", "date": snap, "rows": rows, "file": "IBKR MCP server (live)",
            "warnings": [f"IBKR holdings were read live on {date.today()} through IBKR's MCP server, "
                         f"not from a {snap} statement; IBKR cash isn't included"]}


# ---------- manual entry ----------

def parse_foreign(raw, on):
    """'GBP 120.00' / '120.00 GBP' / '£120.00' (+ more, joined by '+') -> (SGD, note)."""
    total, notes = 0.0, []
    for part in raw.split("+"):
        m = re.fullmatch(r"\s*([A-Za-z]{3}|[£€¥])?\s*([\d.,]+)\s*([A-Za-z]{3})?\s*", part)
        if not m or (m.group(1) and m.group(3)):
            return None
        try:
            amount = num(m.group(2))
        except ValueError:
            return None
        ccy = (m.group(1) or m.group(3) or BASE).upper()
        ccy = SYMBOLS.get(ccy, ccy)
        rate = fx_rate(ccy, on)
        total += amount * rate
        notes.append(f"{ccy} {amount:,.2f}" + (f" @ {rate:.4f}" if ccy != BASE else ""))
    return round(total, 2), " + ".join(notes)


def ask_manual(conn, snap, category, hint):
    """Prompt for one typed-in category. Returns (amount, note) or None to skip."""
    now = conn.execute("SELECT amount, note FROM assets WHERE date=? AND category=? AND tags=''",
                       (snap, category)).fetchone()
    last = conn.execute(
        "SELECT date, amount, note FROM assets WHERE date<? AND category=? AND tags='' "
        "ORDER BY date DESC LIMIT 1", (snap, category)).fetchone()
    ref = (f"already {money(now['amount'])} for this date — Enter keeps it" if now
           else f"last: {money(last['amount'])} on {last['date']} — Enter skips" if last
           else "Enter skips")
    foreign = category == "cash_foreign"
    last_fx = re.match(r"[A-Z]{3} [\d.,]+(?= @)", (last["note"] or "") if last else "")
    if foreign and last_fx:
        ref += f"; '=' means still {last_fx.group()}"
    while True:
        raw = input(f"{category} — {hint} ({ref}): ").strip()
        if not raw:
            return None
        if foreign:
            parsed = parse_foreign(last_fx.group() if raw == "=" and last_fx else raw, snap)
            if parsed:
                return parsed
            print("  Give an amount with its currency, e.g. GBP 120.00 (a bare number means SGD).")
            continue
        amount = parse_amount(raw)
        if amount is None or amount < 0:
            print("  Enter a non-negative number or a sum like 9000+1000.")
            continue
        note = input("  note (optional): ").strip() or None
        return amount, note


# ---------- preview ----------

def sanity_flags(conn, snap, rows, stmts):
    prev = conn.execute("SELECT MAX(date) FROM assets WHERE date < ?", (snap,)).fetchone()[0]
    flags = [w for s in stmts.values() for w in s["warnings"]]
    flags += [f"{c}{' [' + t + ']' if t else ''} is negative ({money(a)})" for c, t, a, _ in rows if a < 0]
    if not prev:
        return prev, flags
    old = {(r["category"], r["tags"]): r["amount"] for r in conn.execute(
        "SELECT category, tags, amount FROM assets WHERE date=?", (prev,))}
    new = {(c, t): a for c, t, a, _ in rows}

    def moved(label, was, now):
        if abs(now - was) > WARN_SGD or (was > 0 and abs(now - was) / was > WARN_PCT):
            flags.append(f"{label} moved {money(was)} -> {money(now)} ({now - was:+,.2f})")

    if "ibkr" in stmts or "ibkr_mcp" in stmts:
        held = {t for (c, t) in old if c == "stocks_foreign" and t}
        have = {t for (c, t) in new if c == "stocks_foreign"}
        flags += [f"holding {t.upper()} was there on {prev} and is gone" for t in sorted(held - have)]
        flags += [f"holding {t.upper()} is new since {prev}" for t in sorted(have - held)] if held else []
        for t in sorted(held & have):
            moved(t.upper(), old[("stocks_foreign", t)], new[("stocks_foreign", t)])
    for cat in sorted({c for c, _ in new}):
        was = sum(a for (c, _), a in old.items() if c == cat)
        # Compare like with like: untouched rows on this date still count.
        keep = sum(r["amount"] for r in conn.execute(
            "SELECT tags, amount FROM assets WHERE date=? AND category=?", (snap, cat))
            if (cat, r["tags"]) not in new and not replaced(cat, r["tags"], stmts, new))
        moved(f"{cat} total", was, keep + sum(a for (c, _), a in new.items() if c == cat))
    w = stmts.get("local_brokerage")
    if w and w.get("opening_total") is not None and prev[:7] == prev_month(snap):
        was = sum(a for (c, t), a in old.items()
                  if c == "stocks_local" or (c == "cash_local" and t in ("mmf", "local_brokerage")))
        if abs(was - w["opening_total"]) > 0.05:
            flags.append(f"Local Brokerage says it opened the month at {money(w['opening_total'])}, but the "
                         f"{prev} snapshot recorded {money(was)} — last month may be wrong")
    return prev, flags


def prev_month(snap):
    y, m = int(snap[:4]), int(snap[5:7])
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def replaced(category, tag, stmts, new):
    """Would writing this snapshot remove an existing (category, tag) row?

    Each source owns its rows: a statement replaces everything it wrote last
    time (so a holding sold since then disappears), a typed-in stock/bond total
    replaces the whole category (so it can't double-count per-holding rows), and
    typed-in cash replaces only the untagged row.
    """
    if (category, tag) in new:
        return True
    if category == "stocks_foreign":      # IBKR via MCP brings holdings, not cash
        return "ibkr" in stmts or "ibkr_mcp" in stmts or (category, "") in new
    if category == "stocks_local":
        return "local_brokerage" in stmts or (category, "") in new
    if category == "bonds":
        return "cdp" in stmts or (category, "") in new
    return (tag == "ibkr" and "ibkr" in stmts) or (tag in ("mmf", "local_brokerage") and "local_brokerage" in stmts)


def show(rows):
    for c, t, a, note in sorted(rows):
        print(f"  {c:15} {t:10} {money(a):>14}  {note or ''}")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Monthly asset snapshot from the statements in data/.")
    ap.add_argument("--month", help="YYYY-MM to snapshot (default: asks, newest unrecorded first)")
    ap.add_argument("--dry-run", action="store_true", help="parse and preview only")
    ap.add_argument("--db", help="database path (default data/finances.db)")
    ap.add_argument("--ibkr-mcp", action="store_true",
                    help="take IBKR holdings live from IBKR's MCP server (scripts/ibkr_mcp.py) "
                         "when there is no Activity Statement CSV")
    args = ap.parse_args()
    interactive = not args.dry_run

    found, problems = find_statements(interactive)
    for p in problems:
        print(f"!! {p}")

    conn = connect(args.db) if args.db else connect()
    recorded = {r[0] for r in conn.execute("SELECT DISTINCT date FROM assets")}

    if args.month:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.month):
            ap.error("--month must look like 2026-09")
        snap = month_end(int(args.month[:4]), int(args.month[5:]))
    else:
        today = date.today()
        last_month = month_end(today.year - (today.month == 1), (today.month - 2) % 12 + 1)
        options = sorted(set(found) | {last_month})
        print("Months you can snapshot:")
        for d in options:
            kinds = ", ".join(f"{k} ({s['file']})" for k, s in sorted(found.get(d, {}).items()))
            print(f"  {d[:7]}  {'recorded' if d in recorded else 'NOT recorded':13} "
                  f"{kinds or 'no statements — everything typed in'}")
        todo = [d for d in options if d in found and d not in recorded]
        default = (todo or options)[-1][:7]
        if interactive:
            pick = input(f"Month [{default}]: ").strip() or default
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", pick):
                raise SystemExit("That isn't a YYYY-MM month.")
        else:
            pick = default
        snap = month_end(int(pick[:4]), int(pick[5:]))

    stmts = dict(found.get(snap, {}))
    if args.ibkr_mcp:
        live = ibkr_live(snap, stmts, interactive)
        if live:
            stmts["ibkr_mcp"] = live
    rows = [r for s in stmts.values() for r in s["rows"]]
    print(f"\n=== Snapshot for {snap} ===")
    for kind, s in sorted(stmts.items()):
        print(f"  {kind}: {s['file']}")

    if interactive:
        print("\nType in what no statement covers (Enter leaves a category as it is):")
        manual = [("stocks_foreign", "ibkr", "total in SGD"), ("stocks_local", "local_brokerage", "total in SGD"),
                  ("bonds", "cdp", "face value in SGD"), ("cash_local", "bank", "bank balance in SGD"),
                  ("cash_foreign", None, "in its own currency, e.g. GBP 120.00")]
        try:
            for category, source, hint in manual:
                if source in stmts or (source == "ibkr" and "ibkr_mcp" in stmts):
                    continue
                entry = ask_manual(conn, snap, category, hint)
                if entry:
                    rows.append((category, "", entry[0], entry[1]))
        except (KeyboardInterrupt, EOFError):
            raise SystemExit("\nAborted; nothing written.")
    assert {c for c, *_ in rows} <= set(ASSET_CATEGORIES)

    if not rows:
        raise SystemExit("Nothing to write.")

    new = {(c, t): a for c, t, a, _ in rows}
    existing = conn.execute(
        "SELECT category, tags, amount, note FROM assets WHERE date=? ORDER BY category, tags",
        (snap,)).fetchall()
    doomed = [r for r in existing if replaced(r["category"], r["tags"], stmts, new)]
    kept = [r for r in existing if r not in doomed]

    print("\nRows to write:")
    show(rows)
    rates = {**{c: r for s in stmts.values() for c, r in s.get("rates", {}).items()},
             **{c: r for (c, d), r in _rates.items() if d == snap}}
    for ccy, r in sorted(rates.items()):
        print(f"  rate: 1 {ccy} = {r:.6f} {BASE}")
    if doomed:
        print(f"\nAlready recorded for {snap} and REPLACED by the above:")
        show([tuple(r) for r in doomed])
    if kept:
        print(f"\nAlready recorded for {snap} and left untouched:")
        show([tuple(r) for r in kept])

    total = sum(new.values()) + sum(r["amount"] for r in kept)
    print("\nBy category:")
    for cat in ASSET_CATEGORIES:
        amt = sum(a for (c, _), a in new.items() if c == cat) + sum(
            r["amount"] for r in kept if r["category"] == cat)
        print(f"  {cat:15} {money(amt):>14}" if amt or any(c == cat for c, _ in new)
              or any(r['category'] == cat for r in kept) else f"  {cat:15} {'— missing —':>14}")
    prev, flags = sanity_flags(conn, snap, rows, stmts)
    line = f"Net worth on {snap}: {money(total)}"
    if prev:
        prev_total = conn.execute("SELECT SUM(amount) FROM assets WHERE date=?", (prev,)).fetchone()[0]
        line += f"  ({total - prev_total:+,.2f} vs {prev})"
    print(line)
    if flags:
        print("\nCheck these before confirming:")
        for f in flags:
            print(f"  ! {f}")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return
    if input("\nWrite this snapshot? [y/N]: ").strip().lower() != "y":
        print("Aborted; nothing written.")
        return

    saved = backup(conn)
    with conn:      # one transaction: the old rows go and the new ones land together
        for r in doomed:
            conn.execute("DELETE FROM assets WHERE date=? AND category=? AND tags=?",
                         (snap, r["category"], r["tags"]))
        conn.executemany(
            "INSERT OR REPLACE INTO assets (date, category, amount, note, tags) VALUES (?,?,?,?,?)",
            [(snap, c, a, note, t) for c, t, a, note in rows])
    conn.close()
    print(f"Written ({len(rows)} rows; backup at data/backups/{saved.name}).")
    if not args.db:
        export.main()


if __name__ == "__main__":
    main()
