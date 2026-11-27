"""
add_expense.py — interactive expense recorder.

Logs expenses to the `expenses` table, one per loop iteration until Ctrl-C.
Flow: date -> note -> category -> tags -> amount. The note is autocorrected
through EXPANSIONS, then `classify()` suggests a category and tags from it
(keywords.py); both suggestions are editable at the prompt.

Dates accept `MM-DD` or `YYYY-MM-DD`; amounts accept arithmetic ("10+5.50").
A negative amount records a refund.

The primary key is (date, category, tags) — logging the identical triple twice
adds to the amount and merges the notes with "; ", but the same category on the
same day with *different* tags stays its own recallable row.

Use the `investment` category for cash moved to a broker and the fees of
investing. It's a transfer, not consumption: the dashboard nets it against
`investment` income (broker cash swept back, logged by add_income.py) and keeps
both out of the burn rate. Don't file it under `misc`.

This module also holds the prompts the other interactive tools reuse.

Run: python add_expense.py
"""

import ast
import math
import operator
from datetime import date as date_cls

from db import connect, money, upsert
from keywords import CATEGORIES, classify, expand, norm_tags, split_tags


# ---------- input helpers ----------

def prompt(message: str, default: str | None = None) -> str:
    """Ask for input; accept default on empty response, re-ask if no default."""
    suffix = f" [{default}]" if default else ""
    while True:
        response = input(f"{message}{suffix}: ").strip()
        if response:
            return response
        if default is not None:
            return default
        print("  (required)")


def get_date(default: str | None = None) -> str:
    today = date_cls.today()
    default = default or today.isoformat()
    while True:
        raw = prompt("Date (MM-DD or YYYY-MM-DD)", default=default).replace("/", "-")
        parts = raw.split("-")
        try:
            if len(parts) == 2:
                # No year given: the most recent such day, so "12-30" typed in
                # January means last December, not eleven months from now.
                d = date_cls(today.year, int(parts[0]), int(parts[1]))
                if d > today:
                    d = d.replace(year=today.year - 1)
            elif len(parts) == 3:
                d = date_cls(*(int(p) for p in parts))
            else:
                raise ValueError("need 2 or 3 parts")
            return d.isoformat()
        except ValueError:
            print(f"  '{raw}' isn't a valid date. Try MM-DD or YYYY-MM-DD.")


def get_category(categories: list[str] = CATEGORIES, default: str | None = None) -> str:
    options = "/".join(categories)
    default = default if default in categories else None
    while True:
        raw = prompt(f"Category ({options})", default=default).lower()
        if raw in categories:
            return raw
        # Allow unique prefixes ("fo" -> "food") — nice for typing speed
        matches = [c for c in categories if c.startswith(raw)]
        if len(matches) == 1:
            return matches[0]
        print(f"  Unknown category. Choose one of: {options}")


_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _eval_expr(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_eval_expr(node.left), _eval_expr(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_expr(node.operand)
    raise ValueError("unsupported expression")


def parse_amount(raw: str) -> float | None:
    """Evaluate a number or +-*/ arithmetic to 2 dp; None if it isn't one.

    Parses to an AST and walks only numeric literals and the four operators —
    never eval() — so nothing typed at an amount prompt can run code.
    """
    if len(raw) > 200:
        return None
    try:
        value = _eval_expr(ast.parse(raw.replace(",", ""), mode="eval").body)
    except (ValueError, SyntaxError, ArithmeticError, RecursionError, MemoryError):
        return None
    return round(value, 2) if math.isfinite(value) else None


def get_amount(label: str = "Amount (SGD)") -> float:
    while True:
        amount = parse_amount(prompt(label))
        if amount is None:
            print("  Invalid expression — try a number or formula like 10+5.50 or 3*4.20.")
        elif amount == 0:
            print("  Amount can't be zero (use a negative amount for a refund).")
        else:
            return amount


def get_note() -> str | None:
    """Read a note and apply autocorrect (keywords.EXPANSIONS)."""
    raw = input("Note (optional): ").strip()
    if not raw:
        return None
    fixed = expand(raw)
    if fixed != raw:
        print(f"  autocorrected -> {fixed}")
    return fixed


def get_tags(suggested: list[str]) -> str:
    """Show suggested/current tags; let the user accept or edit them.

    Enter accepts; a comma-separated list replaces; '-' clears. Returns the
    stored (comma-joined) form.
    """
    default = norm_tags(suggested)
    raw = input(f"Tags [{default or 'none'}] (Enter=keep, '-'=clear): ").strip()
    if not raw:
        return default
    if raw == "-":
        return ""
    return norm_tags(split_tags(raw))


# ---------- database ----------

def save(conn, date: str, category: str, amount: float, note: str | None,
         tags: str = "", table: str = "expenses") -> None:
    """Add-and-merge one row (see db.upsert), commit, and print a recap."""
    old = upsert(conn, table, date, category, amount, note, tags)
    conn.commit()
    label = f"{date} / {category}" + (f" [{tags}]" if tags else "")
    if old is None:
        print(f"\n  Added: {label} / {money(amount)}")
    else:
        print(f"\n  Updated: {label} / {money(old)} + {money(amount)} = {money(old + amount)}")
    mtd = conn.execute(
        f"SELECT SUM(amount) FROM {table} WHERE category = ? AND date LIKE ?",
        (category, f"{date[:7]}-%"),
    ).fetchone()[0] or 0
    print(f"  Month-to-date {category}: {money(mtd)}")


# ---------- main ----------

def main():
    print("=== Log expenses (Ctrl-C when done) ===")
    count = 0
    with connect() as conn:
        try:
            while True:
                print(f"\n--- Entry {count + 1} ---")
                date = get_date()
                note = get_note()
                suggested_cat, suggested_tags = classify(note)
                category = get_category(default=suggested_cat)
                tags = get_tags(suggested_tags)
                amount = get_amount()
                save(conn, date, category, amount, note, tags)
                count += 1
        except (KeyboardInterrupt, EOFError):
            print(f"\n\nDone. Logged {count} expense(s). Run `python export.py` "
                  "(or `python serve.py`) to refresh the dashboard.")


if __name__ == "__main__":
    main()
