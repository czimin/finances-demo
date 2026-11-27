"""
subs.py — a terminal view of your recurring costs.

Reads MONTHLY_SUBS / ANNUAL_SUBS from keywords.py, matches them against the
`subscription`-tagged rows in the DB to show what each one has actually cost,
and estimates your fixed monthly burn. Also surfaces anything that *looks*
recurring but isn't configured yet — the same rule the dashboard uses: a note
appearing in 3+ months with every amount within 20% of its median — so you
know what to add to keywords.py.

    python subs.py
"""
from collections import defaultdict
from statistics import median

from db import connect, money
from keywords import ANNUAL_SUBS, MONTHLY_SUBS


def report_group(conn, title, table, per_month) -> float:
    """Print one cadence group; return its total monthly-equivalent burn."""
    print(f"\n{title}")
    burn = 0.0
    for keyword, (label, category) in table.items():
        rows = conn.execute(
            "SELECT date, amount FROM expenses "
            "WHERE (',' || tags || ',') LIKE '%,subscription,%' AND note LIKE ? ORDER BY date",
            (f"%{keyword}%",),
        ).fetchall()
        if not rows:
            print(f"  {label:16} {category:9} (no charges tagged yet)")
            continue
        months = {d[:7] for d, _ in rows}
        latest = rows[-1][1]
        monthly = latest if per_month else latest / 12
        burn += monthly
        rate = f"{money(latest)}/mo" if per_month else f"{money(latest)}/yr = {money(monthly)}/mo"
        print(f"  {label:16} {category:9} {rate:24} "
              f"({len(months)} mo seen, {money(sum(a for _, a in rows))} to date)")
    return burn


def detect_extra(conn) -> None:
    """Notes that recur at a steady amount but aren't configured subscriptions."""
    configured = [k.lower() for k in (*MONTHLY_SUBS, *ANNUAL_SUBS)]
    seen = defaultdict(list)  # note fragment -> [(month, amount)]
    for date, note, amount in conn.execute(
        "SELECT date, note, amount FROM expenses WHERE note IS NOT NULL "
        "AND category <> 'investment' AND (',' || tags || ',') NOT LIKE '%,subscription,%'"
    ):
        for token in (t.strip().lower() for t in note.split(";")):
            if token and not any(k in token for k in configured):
                seen[token].append((date[:7], amount))
    hits = []
    for token, entries in seen.items():
        months = {m for m, _ in entries}
        mid = median(a for _, a in entries)
        if len(months) >= 3 and mid > 0 and all(abs(a - mid) <= 0.2 * mid for _, a in entries):
            hits.append((token, len(months), mid))
    if hits:
        print("\nLooks recurring but not in keywords.py (consider adding):")
        for token, n, mid in sorted(hits, key=lambda x: -x[2]):
            print(f"  '{token}' — {n} months at about {money(mid)}")


def main():
    with connect() as conn:
        print("=== Subscriptions & recurring costs ===")
        burn = report_group(conn, "Monthly:", MONTHLY_SUBS, per_month=True)
        burn += report_group(conn, "Annual (shown as monthly-equivalent):", ANNUAL_SUBS, per_month=False)
        detect_extra(conn)
        print("\n" + "-" * 48)
        print(f"Estimated fixed monthly burn:  {money(burn)}")
        print(f"Annualized:                    {money(burn * 12)}")


if __name__ == "__main__":
    main()
