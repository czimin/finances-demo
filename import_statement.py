"""
import_statement.py — log expenses and income straight from your bank statements.

    python import_statement.py           # import, then ask about what it can't place
    python import_statement.py --auto    # import only what the rules cover; ask nothing
    python import_statement.py --since 2026-09-01

Save your Local Bank account statement PDFs into data/ (any file name) and run this.
Every transaction is filed by the rules in keywords.py (BANK_RULES /
BANK_INCOME_RULES): a bus fare becomes `transport / public transport`, the gym
debit becomes the tagged ABC Fitness subscription, and so on. Whatever no
rule covers is shown to you one at a time — you give it a note and a category
(or mark it "not spending", e.g. a transfer to your own wallet) and can have the
answer remembered as a new rule, so next month it files itself.

What it takes care of:
  - Nothing is imported twice. Every statement line is recorded in the `bank`
    table; re-running, or overlapping statements, only picks up new lines.
  - Rows you already logged by hand are left alone: on the first import only
    lines posted after your newest logged expense are filed (--since overrides),
    and any line whose amount already sits in the ledger on that day is skipped.
  - Card purchases are dated the day you paid, not the day the bank posted them.
  - A currency-conversion fee is folded into the purchase it belongs to.
  - A deposit that matches an expense rule is a refund: a negative expense.
  - Each statement's running balance is checked line by line, so a misread
    figure stops the import rather than being logged.

The database is backed up first, and the dashboard data re-exported after.
"""
import argparse
import json
import re
import sys
from collections import namedtuple
from datetime import date, timedelta
from pathlib import Path

import export
import keywords
from add_expense import get_category, get_tags, prompt
from db import ROOT, backup, connect, money, upsert
from keywords import CATEGORIES, INCOME_CATEGORIES, classify, classify_income, norm_tags
from scripts.pdfitems import PdfError, extract_items, lines

DATA_DIR = ROOT / "data"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
AMOUNT = re.compile(r"\s*([\d,]+\.\d\d)\s*")

# amount is signed: negative = money out. `when` is the purchase date printed on
# card transactions (the day you actually paid), else the posting date.
Tx = namedtuple("Tx", "posted when description amount balance")


class ParseError(Exception):
    pass


# ---------- reading the statement ----------

def read_local_bank(path):
    """Parse a Local Bank account statement PDF.

    Returns {"start", "end", "opening", "closing", "account", "txs": [Tx]}, or
    None if the PDF is some other document.
    """
    try:
        rows = lines(extract_items(path))
    except PdfError as exc:
        raise ParseError(str(exc))

    def text(its):
        # Words inside one table cell are separate runs with their own spacing;
        # a change of cell is a column break, so it gets a space.
        out, last = "", None
        for i in its:
            out += (" " if last is not None and i.cell != last else "") + i.text
            last = i.cell
        return re.sub(r"\s+", " ", out).strip()

    flat = [(y, its, text(its)) for _, y, its in rows]
    if not any("STATEMENT OF ACCOUNT" in t for *_, t in flat):
        return None
    period = next((m for *_, t in flat
                   if (m := re.search(r"(\d+) ([A-Z]{3}) (\d{4}) TO (\d+) ([A-Z]{3}) (\d{4})", t))), None)
    if not period or period.group(2) not in MONTHS or period.group(5) not in MONTHS:
        raise ParseError("no statement period found")
    start = date(int(period.group(3)), MONTHS.index(period.group(2)) + 1, int(period.group(1)))
    end = date(int(period.group(6)), MONTHS.index(period.group(5)) + 1, int(period.group(4)))
    # The account's name is printed on the same line as the period.
    account = period.string[:period.start()].strip() or "account"
    desc_cell = next((i.cell for _, its, _ in flat for i in its if i.text.strip() == "Description"), None)

    def on(day_month):
        """A 'DD MON' from the table as a date: the year that puts it nearest the period."""
        d, m = day_month.split()
        options = [date(y, MONTHS.index(m) + 1, int(d)) for y in (end.year - 1, end.year, end.year + 1)]
        return min(options, key=lambda x: abs(x - end))

    opening = closing = balance = None
    txs, cur, in_table = [], None, False
    for y, its, t in flat:
        squashed = t.replace(" ", "")
        if squashed.startswith("DateDateDescription"):
            in_table = True            # every page repeats the header above its rows
            continue
        if y < 60:
            in_table = False           # page footer
        if not in_table:
            continue
        amounts = [float(m.group(1).replace(",", "")) for i in its if (m := AMOUNT.fullmatch(i.text))]
        words = text([i for i in its if not AMOUNT.fullmatch(i.text)])
        if squashed.startswith("BALANCEB/F"):
            opening = balance = amounts[-1]
        elif squashed.startswith("BALANCEC/F"):
            closing = amounts[-1]
            break
        elif m := re.match(r"(\d\d [A-Z]{3}) (\d\d [A-Z]{3}) ?(.*)", words):
            if balance is None or len(amounts) != 2 or m.group(1)[3:] not in MONTHS:
                raise ParseError(f"can't read the row: {t}")
            amount, new_balance = amounts
            signed = round(new_balance - balance, 2)
            # The running balance is the proof: it tells a withdrawal from a
            # deposit and catches any misread figure.
            if abs(abs(signed) - amount) > 0.005:
                raise ParseError(f"the balance doesn't follow from the row: {t}")
            balance = new_balance
            cur = {"posted": on(m.group(1)), "when": None, "kind": m.group(3), "detail": [],
                   "amount": signed, "balance": new_balance}
            txs.append(cur)
        elif cur and not amounts and its[0].cell == desc_cell:
            if pd := re.fullmatch(r"(\d\d) ?/ ?(\d\d) ?/ ?(\d\d)", words):
                try:
                    cur["when"] = date(2000 + int(pd.group(3)), int(pd.group(2)), int(pd.group(1)))
                except ValueError:
                    pass
            elif len(words) > 1:       # a lone letter is the card's region code
                cur["detail"].append(words)
    if opening is None or closing is None:
        raise ParseError("couldn't find the opening and closing balances")
    if abs(balance - closing) > 0.005:
        raise ParseError("the transactions don't add up to the closing balance")
    out = []
    for c in txs:
        when = c["when"]
        if not when or not timedelta(0) <= c["posted"] - when <= timedelta(days=45):
            when = c["posted"]         # no printed purchase date, or an implausible one
        detail = re.sub(r"\s*([-/*])\s*", r"\1", " ".join(c["detail"]))
        out.append(Tx(c["posted"].isoformat(), when.isoformat(), f"{c['kind']} | {detail}".strip(" |"),
                      c["amount"], c["balance"]))
    return {"start": start.isoformat(), "end": end.isoformat(), "opening": opening,
            "closing": closing, "account": account, "txs": out}


def find_statements(paths=None):
    """Parse the given PDFs, or every bank statement in data/. Oldest first."""
    found, problems = [], []
    for path in sorted(Path(p) for p in paths) if paths else sorted(DATA_DIR.glob("*.pdf")):
        try:
            stmt = read_local_bank(path)
        except ParseError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        except (OSError, ValueError, IndexError, KeyError) as exc:
            if paths:       # in data/, a PDF this can't parse is just not a bank statement
                problems.append(f"{path.name}: unreadable ({exc})")
            continue
        if stmt:
            stmt["file"] = path.name
            found.append(stmt)
    found.sort(key=lambda s: s["start"])
    return found, problems


# ---------- deciding where a transaction goes ----------

def reference(description):
    """The free-text reference on a PayNow transfer — usually your own words."""
    m = re.search(r"OTHR[- ]?(.*)$", description)
    ref = m.group(1).strip() if m else ""
    return "" if re.fullmatch(r"(OTHR|PayNow Transfer|[\dA-Za-z]*\d{6,}[\dA-Za-z ]*)?", ref) else ref


def merchant(description):
    """The stable part of a description, for suggesting a rule keyword."""
    d = description.split("|", 1)[-1]
    d = re.sub(r"xx-\d{4}|via PayNow-\w+|\bto\b|\bfrom\b|OTHR.*$|\b\d{6,}\b", " ", d)
    return re.sub(r"\s+", " ", d).strip().lower()


def place(tx, peers):
    """(table, category, note, tags, date) if a rule covers `tx`, else None."""
    desc = tx.description.lower()
    fee = re.search(r"ccy conversion fee \| for: ?([\d,.]+) ?sgd", desc)
    if fee:
        # Belongs to the purchase it converted: same row, so the two add up.
        target = float(fee.group(1).replace(",", ""))
        near = lambda p: abs(date.fromisoformat(p.posted) - date.fromisoformat(tx.posted)).days <= 7
        parent = next((p for p in peers if p is not tx and abs(abs(p.amount) - target) < 0.005
                       and p.amount < 0 and near(p)), None)
        placed = parent and place(parent, peers)
        return placed and (placed[0], placed[1], None, placed[3], placed[4])
    if tx.amount > 0:
        for kw, (cat, note) in keywords.BANK_INCOME_RULES.items():
            if kw in desc:
                return "income", cat, note, norm_tags(classify_income(note)[1]), tx.when
    for kw, (cat, note) in keywords.BANK_RULES.items():
        if kw in desc:      # for a deposit this is a refund: a negative expense
            return "expenses", cat, note, norm_tags(classify(note)[1]), tx.when
    return None


def already_logged(conn, table, when, amount):
    """Is there a ledger row on that day for exactly this amount?"""
    return conn.execute(
        f"SELECT 1 FROM {table} WHERE date=? AND ABS(amount - ?) < 0.005", (when, amount)
    ).fetchone() is not None


def record(conn, tx, table, category, note, tags, when):
    """Write one transaction to the ledger and mark its bank line done."""
    amount = abs(tx.amount) if (table == "income") == (tx.amount > 0) else -abs(tx.amount)
    upsert(conn, table, when, category, amount, note, tags)
    set_status(conn, tx, f"{table}:{category}")


def set_status(conn, tx, status):
    conn.execute("UPDATE bank SET status=? WHERE posted=? AND amount=? AND balance=? AND description=?",
                 (status, tx.posted, tx.amount, tx.balance, tx.description))


def pending(conn):
    return [Tx(*r) for r in conn.execute(
        "SELECT posted, date, description, amount, balance FROM bank "
        "WHERE status IS NULL ORDER BY posted, rowid")]


def auto_pass(conn, tally):
    """File every pending line a rule covers. Returns how many it filed."""
    rows = pending(conn)
    done = 0
    for tx in rows:
        placed = place(tx, rows)
        if not placed:
            continue
        table, category, note, tags, when = placed
        signed = abs(tx.amount) if (table == "income") == (tx.amount > 0) else -abs(tx.amount)
        if note is not None and already_logged(conn, table, when, signed):
            set_status(conn, tx, "manual")
            tally["already in the ledger"] = tally.get("already in the ledger", 0) + 1
            continue
        record(conn, tx, table, category, note, tags, when)
        key = f"{table}:{category}"
        tally[key] = tally.get(key, 0) + signed
        done += 1
    return done


# ---------- asking about the rest ----------

def remember(table, keyword, category, note):
    """Add a rule to keywords.py (and to this run) so the next one files itself."""
    name = "BANK_INCOME_RULES" if table == "income" else "BANK_RULES"
    marker = f"    # (import_statement.py adds remembered {'income' if table == 'income' else 'expense'} rules above this line)"
    path = ROOT / "keywords.py"
    source = path.read_text(encoding="utf-8")
    if marker not in source:
        print(f"  (couldn't find the marker in keywords.py — add this to {name} yourself: "
              f"{keyword!r}: ({category!r}, {note!r}))")
        return
    quote = lambda text: json.dumps(text, ensure_ascii=False)   # a valid Python string literal
    line = f"    {quote(keyword) + ':':21}({quote(category)}, {quote(note)}),\n"
    updated = source.replace(marker, line + marker, 1)
    try:
        compile(updated, "keywords.py", "exec")     # never leave keywords.py unimportable
    except SyntaxError:
        print("  (couldn't write that rule safely — not remembered)")
        return
    path.write_text(updated, encoding="utf-8")
    getattr(keywords, name)[keyword] = (category, note)


def ask(conn, tx, left):
    """Ask where one transaction goes. Returns False if the user wants to stop."""
    direction = "IN " if tx.amount > 0 else "OUT"
    print(f"\n--- {left} to place ---")
    print(f"  {tx.when}  {direction} {money(abs(tx.amount))}   {tx.description}")
    if tx.amount > 0:
        choice = prompt("  [i]ncome, [r]efund of an expense, [n]ot income (transfer), [l]ater, [q]uit",
                        default="i").lower()[:1]
    else:
        choice = prompt("  [e]xpense, [n]ot spending (transfer), [l]ater, [q]uit", default="e").lower()[:1]
    if choice == "q":
        return False
    if choice == "l":
        return True
    if choice == "n":
        set_status(conn, tx, "skip")
        conn.commit()
        return True
    if choice not in ("i", "r", "e"):
        print("  (not an option — left for later)")
        return True
    table = "income" if choice == "i" else "expenses"
    note = prompt("  Note", default=reference(tx.description) or merchant(tx.description)[:40] or None)
    note = keywords.expand(note)
    guess_cat, guess_tags = classify_income(note) if table == "income" else classify(note)
    category = get_category(INCOME_CATEGORIES if table == "income" else CATEGORIES, default=guess_cat)
    tags = get_tags(guess_tags)
    record(conn, tx, table, category, note, tags, tx.when)
    conn.commit()
    keyword = input(f"  Remember this? Type the words that identify it "
                    f"[{merchant(tx.description)[:30]}], Enter=yes, '-'=no: ").strip().lower()
    keyword = keyword or merchant(tx.description)[:30]
    if keyword and keyword != "-" and len(keyword) >= 3:
        remember(table, keyword, category, note)
        print(f"  remembered: \"{keyword}\" -> {category} / {note}")
    return True


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Log expenses and income from bank statement PDFs.")
    ap.add_argument("files", nargs="*", help="statement PDFs (default: every one in data/)")
    ap.add_argument("--auto", action="store_true", help="file what the rules cover and ask nothing")
    ap.add_argument("--since", help="YYYY-MM-DD: only file lines posted on/after this date")
    args = ap.parse_args()
    if args.since:
        date.fromisoformat(args.since)      # fail early on a typo

    stmts, problems = find_statements(args.files)
    for p in problems:
        print(f"!! {p}")
    if not stmts:
        print("No bank statement PDFs found in data/.")

    conn = connect()
    if stmts or pending(conn):
        backup(conn)
    since = args.since
    if not since and conn.execute("SELECT COUNT(*) FROM bank").fetchone()[0] == 0:
        # First import: don't re-log what was entered by hand.
        newest = conn.execute("SELECT MAX(date) FROM expenses").fetchone()[0]
        if newest:
            since = (date.fromisoformat(newest) + timedelta(days=1)).isoformat()
            print(f"First import: filing lines posted from {since} on — your hand-logged rows end "
                  f"{newest}. (Use --since to change.)")

    prev = None
    for s in stmts:
        if prev and abs(prev["closing"] - s["opening"]) > 0.005:
            print(f"!! {s['file']} opens at {money(s['opening'])} but the statement before it closed "
                  f"at {money(prev['closing'])} — one in between is missing.")
        prev = s
        new = 0
        for tx in s["txs"]:
            new += conn.execute(
                "INSERT OR IGNORE INTO bank (posted, date, description, amount, balance, status) "
                "VALUES (?,?,?,?,?,?)",
                (tx.posted, tx.when, tx.description, tx.amount, tx.balance,
                 "manual" if since and tx.posted < since else None)).rowcount
        print(f"{s['file']}: {s['start']} to {s['end']}, {len(s['txs'])} lines ({new} new), "
              f"closing balance {money(s['closing'])}")
    conn.commit()

    tally = {}
    filed = auto_pass(conn, tally)
    conn.commit()
    if tally:
        print(f"\nFiled {filed} line(s) by rule:")
        for key, total in sorted(tally.items()):
            print(f"  {key:28} {money(total) if ':' in key else str(total) + ' line(s)'}")

    todo = pending(conn)
    if todo and not args.auto:
        print(f"\n{len(todo)} line(s) no rule covers. Enter accepts the suggestion in [brackets].")
        try:
            for n, tx in enumerate(todo):
                if tx not in pending(conn):
                    continue                       # a rule remembered just now placed it
                if not ask(conn, tx, len(todo) - n):
                    break
                filed = auto_pass(conn, tally)
                conn.commit()
                if filed:
                    print(f"  (that rule also filed {filed} more)")
        except (KeyboardInterrupt, EOFError):
            conn.rollback()
            print()
    todo = pending(conn)
    if todo:
        print(f"\n{len(todo)} line(s) still to place — run `python import_statement.py` to go through them:")
        for tx in todo:
            print(f"  {tx.when}  {tx.amount:>10,.2f}  {tx.description}")
    conn.close()
    export.main()


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    main()
