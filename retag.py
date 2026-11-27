"""
retag.py — normalize notes and re-derive tags for every expense, using keywords.py.

Two passes per row, neither touching category or amount:
  - the note is run through EXPANSIONS (so old "abcf gym" becomes "ABC Fitness
    gym", matching the subscription keywords);
  - tags inferred from the (expanded) note are unioned into whatever the row
    already has, so manual tags you added are kept.

Run it after editing keywords.py to propagate new rules across your history.

    python retag.py            # apply (backs the database up first)
    python retag.py --dry-run  # show what would change, touch nothing

Because tags are part of the primary key, a row whose tags change to match an
existing sibling (same date+category+tags) is merged into it.
"""
import sys

from db import backup, connect, relocate
from keywords import classify, expand, norm_tags, split_tags


def main():
    dry = "--dry-run" in sys.argv[1:]
    changed = 0
    with connect() as conn:
        rowids = [r[0] for r in conn.execute("SELECT rowid FROM expenses ORDER BY date, category, tags")]
        for rowid in rowids:
            # Re-read each row: an earlier merge may have changed or removed it.
            r = conn.execute(
                "SELECT date, category, amount, note, tags FROM expenses WHERE rowid=?", (rowid,)
            ).fetchone()
            if r is None:
                continue
            new_note = expand(r["note"])
            new_tags = norm_tags(split_tags(r["tags"]) + classify(new_note)[1])
            if new_tags == r["tags"] and new_note == r["note"]:
                continue
            if not changed and not dry:
                backup(conn)
            changed += 1
            note_change = f"  note -> {new_note!r}" if new_note != r["note"] else ""
            print(f"  {r['date']} {r['category']:10} [{r['tags']}] -> [{new_tags}]  {r['note']!r}{note_change}")
            if not dry and relocate(conn, "expenses", rowid, r["date"], r["category"],
                                    r["amount"], new_note, new_tags):
                print("    (merged into the row that already had those tags)")

    print(f"\n{'would change' if dry else 'changed'} {changed} row(s).")
    if changed and not dry:
        print("Run `python export.py` (or `python serve.py`) to refresh the dashboard.")


if __name__ == "__main__":
    main()
