"""
edit_expense.py — find and fix existing expense (or income) rows.

The loggers only append; this is the tool for corrections: move to another
date, recategorize, retag, reword, adjust an amount, split one row into several,
or delete. Rows are found by date or note keyword, then targeted by their SQLite
rowid so there's never ambiguity.

    python edit_expense.py            # expenses
    python edit_expense.py --income   # the income table

Any edit that would collide with an existing (date, category, tags) row merges
into it (amounts add, notes join with '; ') instead of failing. The database is
backed up to data/backups/ before the first change of a session.
"""
import sys

from add_expense import get_amount, get_category, get_date, get_tags, parse_amount, prompt
from db import backup, connect, money, relocate, upsert
from keywords import CATEGORIES, INCOME_CATEGORIES, split_tags


def show(rows) -> None:
    for i, r in enumerate(rows, 1):
        tags = f" [{r['tags']}]" if r["tags"] else ""
        print(f"  {i:>2}. {r['date']}  {r['category']:10} {money(r['amount']):>11}  {r['note'] or ''}{tags}")


def find_rows(conn, table):
    term = prompt("Find rows (date fragment or note keyword; blank=all)", default="")
    # Escape LIKE wildcards so searching "50%" or "top_up" matches literally.
    like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return conn.execute(
        f"SELECT rowid, date, category, amount, note, tags FROM {table} "
        "WHERE date LIKE ? ESCAPE '\\' OR COALESCE(note,'') LIKE ? ESCAPE '\\' "
        "ORDER BY date, category, tags",
        (like, like),
    ).fetchall()


def get_note_edit(current: str | None) -> str | None:
    raw = prompt("  note ('-' clears)", default=current or "")
    return None if raw in ("", "-") else raw


def split_row(conn, table, categories, r) -> bool:
    total = round(r["amount"], 2)
    print(f"  Splitting {money(total)}. Enter each part; the last one takes what's left.")
    parts, remaining = [], total
    while remaining:
        raw = prompt(f"Part {len(parts) + 1} amount (remaining {money(remaining)}, blank=use it)",
                     default="")
        amount = remaining if raw == "" else parse_amount(raw)
        if amount is None or amount == 0 or abs(amount) > abs(remaining) or (amount > 0) != (total > 0):
            print(f"  Enter an amount between 0 and {money(remaining)}.")
            continue
        category = get_category(categories, default=r["category"])
        note = get_note_edit(r["note"])
        tags = get_tags(split_tags(r["tags"]))
        parts.append((category, amount, note, tags))
        remaining = round(remaining - amount, 2)
    if len(parts) < 2:
        print("  only one part — nothing to split.")
        return False
    conn.execute(f"DELETE FROM {table} WHERE rowid=?", (r["rowid"],))
    for category, amount, note, tags in parts:
        upsert(conn, table, r["date"], category, amount, note, tags)
    print(f"  split into {len(parts)} rows.")
    return True


def edit_one(conn, table, categories, r) -> None:
    """Apply one edit to row `r`. Nothing is written until the choice is complete."""
    print("\n  " + "-" * 40)
    show([r])
    action = prompt("  [w]hen [c]ategory [t]ags [n]ote [a]mount [s]plit [d]elete [q]uit",
                    default="q").lower()
    date, category, amount = r["date"], r["category"], r["amount"]
    note, tags = r["note"], r["tags"]
    if action == "d":
        if prompt("  really delete? (y/n)", default="n").lower() == "y":
            conn.execute(f"DELETE FROM {table} WHERE rowid=?", (r["rowid"],))
            print("  deleted.")
        return
    if action == "s":
        split_row(conn, table, categories, r)
        return
    if action == "w":
        date = get_date(default=date)
    elif action == "c":
        category = get_category(categories, default=category)
    elif action == "t":
        tags = get_tags(split_tags(tags))
    elif action == "n":
        note = get_note_edit(note)
    elif action == "a":
        amount = get_amount("  New amount (SGD)")
    else:
        if action != "q":
            print("  unknown action.")
        return
    merged = relocate(conn, table, r["rowid"], date, category, amount, note, tags)
    print(f"  merged into the existing {date}/{category} row." if merged else "  updated.")


def main():
    income = "--income" in sys.argv[1:]
    table, categories = ("income", INCOME_CATEGORIES) if income else ("expenses", CATEGORIES)
    with connect() as conn:
        print(f"=== Edit {table} (Ctrl-C to stop) ===")
        backed_up = False
        try:
            while True:
                rows = find_rows(conn, table)
                if not rows:
                    print("  no matches.")
                    continue
                show(rows)
                pick = prompt("Row # to edit (blank=search again)", default="")
                if not pick:
                    continue
                if not (pick.isdigit() and 1 <= int(pick) <= len(rows)):
                    print("  invalid choice.")
                    continue
                if not backed_up:
                    backup(conn)
                    backed_up = True
                try:
                    edit_one(conn, table, categories, rows[int(pick) - 1])
                    conn.commit()
                except BaseException:
                    conn.rollback()      # Ctrl-C mid-edit must not leave half a split
                    raise
        except (KeyboardInterrupt, EOFError):
            print("\nDone. Run `python export.py` (or `python serve.py`) to refresh the dashboard.")


if __name__ == "__main__":
    main()
