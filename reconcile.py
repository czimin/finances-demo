"""
reconcile.py — does the ledger explain what actually happened to your bank balance?

    python reconcile.py

Month by month, it compares two things that should agree:

    what the bank balance did     closing balance - opening balance
    what you logged               income - expenses (transfers included: money
                                  sent to a broker really does leave the bank)

The difference is the *unexplained* amount. Negative means money left the bank
that the ledger doesn't know about — unlogged spending. Positive means money
arrived that was never logged as income, or an expense was logged that didn't
come out of this account (cash, another card).

The bank side comes from imported statements (import_statement.py) where they
exist — exact — and otherwise from the bank-balance rows of your month-end
snapshots. A few dollars of noise is normal: card purchases are logged on the
day you paid but can post to the bank in the following month.

It then lists what would close the gap: statement lines still waiting to be
placed, and lines marked as transfers (neither spending nor income).
"""
from collections import defaultdict

from db import connect, money

TOLERANCE = 20.00     # under this, a month counts as reconciled (posting-date noise)


def main():
    with connect() as conn:
        # Bank side, exact: first and last running balance of each statement month.
        bank = {}
        for month, opening, closing, n in conn.execute("""
            SELECT substr(posted, 1, 7) AS m,
                   (SELECT balance - amount FROM bank b WHERE substr(b.posted,1,7) = substr(bank.posted,1,7)
                     ORDER BY posted, rowid LIMIT 1),
                   (SELECT balance FROM bank b WHERE substr(b.posted,1,7) = substr(bank.posted,1,7)
                     ORDER BY posted DESC, rowid DESC LIMIT 1),
                   COUNT(*)
            FROM bank GROUP BY m"""):
            bank[month] = (opening, closing, "statement")
        # Bank side, from snapshots: the untagged cash_local row is the bank balance.
        snaps = dict(conn.execute(
            "SELECT substr(date, 1, 7), amount FROM assets WHERE category='cash_local' AND tags='' ORDER BY date"))
        months = sorted(snaps)
        for prev, cur in zip(months, months[1:]):
            y, m = int(cur[:4]), int(cur[5:])
            consecutive = prev == f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"
            if cur not in bank and consecutive:
                bank[cur] = (snaps[prev], snaps[cur], "snapshots")

        logged = defaultdict(lambda: [0.0, 0.0])        # month -> [income, expenses]
        for i, table in enumerate(("income", "expenses")):
            for month, total in conn.execute(f"SELECT substr(date,1,7), SUM(amount) FROM {table} GROUP BY 1"):
                logged[month][i] = total
        # Lines marked as transfers moved the balance without being income or spending.
        transfers = dict(conn.execute(
            "SELECT substr(posted,1,7), SUM(amount) FROM bank WHERE status='skip' GROUP BY 1"))
        waiting = dict(conn.execute(
            "SELECT substr(posted,1,7), COUNT(*) FROM bank WHERE status IS NULL GROUP BY 1"))

        if not bank:
            print("Nothing to reconcile yet: import a bank statement (python import_statement.py) or "
                  "record two consecutive month-end snapshots with a bank balance.")
            return

        print("=== Bank balance vs ledger ===\n")
        print(f"{'month':8} {'bank change':>13} {'income':>11} {'expenses':>11} {'transfers':>11} "
              f"{'unexplained':>12}  source")
        worst = []
        for month in sorted(bank):
            opening, closing, source = bank[month]
            income, expenses = logged[month]
            moved = transfers.get(month, 0.0)
            gap = (closing - opening) - (income - expenses) - moved
            flag = "" if abs(gap) <= TOLERANCE else "  <-- check"
            if month in waiting:
                flag += f"  ({waiting[month]} statement line(s) not placed yet)"
            print(f"{month:8} {closing - opening:>+13,.2f} {income:>11,.2f} {expenses:>11,.2f} "
                  f"{moved:>+11,.2f} {gap:>+12,.2f}  {source}{flag}")
            if abs(gap) > TOLERANCE:
                worst.append((month, gap))

        print("\nunexplained = bank change - (income - expenses) - transfers")
        if not worst:
            print(f"Every month reconciles to within {money(TOLERANCE)}.")
        for month, gap in worst:
            what = ("more left the bank than you logged — unlogged spending, or money moved to a broker "
                    "or into bonds without an `investment` entry" if gap < 0 else
                    "less left the bank than you logged — unlogged income, or spending paid from "
                    "somewhere else (cash, another account)")
            print(f"  {month}: {money(abs(gap))} {what}.")

        todo = conn.execute(
            "SELECT date, amount, description FROM bank WHERE status IS NULL ORDER BY posted, rowid").fetchall()
        if todo:
            print(f"\n{len(todo)} statement line(s) still to place (total {money(sum(r['amount'] for r in todo))}) "
                  "— run `python import_statement.py`:")
            for r in todo[:15]:
                print(f"  {r['date']}  {r['amount']:>10,.2f}  {r['description'][:70]}")
            if len(todo) > 15:
                print(f"  … and {len(todo) - 15} more")


if __name__ == "__main__":
    main()
