"""
returns.py — how your investments actually did, separated from what you put in.

    python returns.py

A portfolio grows for two reasons: you add money, and the market moves. Net
worth mixes them up; this pulls them apart using what the month-end snapshots
already hold.

  Holdings      For each stock position in the latest snapshot: what you paid
                (quantity x average cost), what it's worth, and the gain — in
                the holding's own currency, so the exchange rate doesn't blur it.

  Month by month  For each pair of consecutive snapshots: the change in value,
                split into money you added (the change in cost basis) and the
                market's move, and the month's return on what was invested.
                Months are chained into a time-weighted return, which is not
                distorted by when you happened to add money.

It needs per-holding snapshots (quantity and cost basis are read from each row's
note), which start with the first snapshot taken from an IBKR statement (2026-07
in the demo data). Before that the snapshots are bare totals, so growth can't be
split — those months are listed as totals only.

A handful of months is far too short to estimate a long-run return: treat the
annualised figure as a description of what happened, not a forecast.
"""
import re
from collections import defaultdict

from db import connect, money

# "<name>: <qty> @ <CCY> <price>; avg <cost>; ..."   (IBKR rows)
FOREIGN = re.compile(r":\s*([\d.,]+)\s+@\s+([A-Z]{3})\s+([\d.,]+);\s*avg\s+([\d.,]+)")
# "<TICKER> <qty> @ <price> (avg <cost>)"            (Local Brokerage rows, SGD)
LOCAL = re.compile(r"(?:Local Brokerage: |\+ )(\S+)\s+([\d.,]+)\s+@\s+([\d.,]+)(?:\s+\(avg\s+([\d.,]+)\))?")
num = lambda s: float(s.replace(",", ""))


def positions(rows):
    """{ticker: dict(qty, price, cost, ccy, sgd)} for one snapshot's stock rows."""
    out = {}
    for r in rows:
        note = r["note"] or ""
        if r["category"] == "stocks_foreign" and r["tags"] and (m := FOREIGN.search(note)):
            qty, ccy, price, avg = num(m.group(1)), m.group(2), num(m.group(3)), num(m.group(4))
            out[r["tags"].upper()] = dict(qty=qty, price=price, cost=qty * avg, ccy=ccy, sgd=r["amount"],
                                          cat=r["category"])
        elif r["category"] == "stocks_local":
            for m in LOCAL.finditer(note):
                qty, price = num(m.group(2)), num(m.group(3))
                # Older rows carry no average cost; cost is then unknown (None).
                cost = qty * num(m.group(4)) if m.group(4) else None
                out[m.group(1).upper()] = dict(qty=qty, price=price, cost=cost, ccy="SGD", sgd=qty * price,
                                               cat=r["category"])
    return out


def main():
    with connect() as conn:
        snaps = defaultdict(list)
        for r in conn.execute(
                "SELECT date, category, amount, note, tags FROM assets "
                "WHERE category IN ('stocks_foreign', 'stocks_local') ORDER BY date"):
            snaps[r["date"]].append(r)
    if not snaps:
        print("No stock snapshots yet — run python snapshot.py first.")
        return
    dates = sorted(snaps)
    held = {d: positions(snaps[d]) for d in dates}
    grand = {d: sum(r["amount"] for r in snaps[d]) for d in dates}
    cats = {d: {r["category"] for r in snaps[d]} for d in dates}

    latest = next((d for d in reversed(dates) if held[d]), None)
    if latest:
        print(f"=== Holdings on {latest} (each in its own currency) ===\n")
        print(f"{'':6} {'cost':>12} {'value':>12} {'gain':>11} {'%':>7}   {'worth in SGD':>13}")
        by_ccy = defaultdict(lambda: [0.0, 0.0])
        for t, p in sorted(held[latest].items(), key=lambda kv: -kv[1]["sgd"]):
            value = p["qty"] * p["price"]
            if p["cost"] is None:
                print(f"{t:6} {'(cost not recorded)':>25} {p['ccy']} {value:>8,.0f} {'':>19}   {money(p['sgd']):>13}")
                continue
            by_ccy[p["ccy"]][0] += p["cost"]
            by_ccy[p["ccy"]][1] += value
            print(f"{t:6} {p['ccy']} {p['cost']:>8,.0f} {p['ccy']} {value:>8,.0f} {value - p['cost']:>+11,.0f} "
                  f"{(value / p['cost'] - 1) * 100:>+6.1f}%   {money(p['sgd']):>13}")
        for ccy, (cost, value) in sorted(by_ccy.items()):
            print(f"{'all ' + ccy:6} {ccy} {cost:>8,.0f} {ccy} {value:>8,.0f} {value - cost:>+11,.0f} "
                  f"{(value / cost - 1) * 100:>+6.1f}%")
        print("\ngain = value - what you paid, since you bought. It is unrealised, and excludes "
              "dividends already paid out.")

    print("\n=== Month by month (stocks, SGD) ===\n")
    print(f"{'month':8} {'value':>13} {'change':>12} {'you added':>12} {'market':>12} {'return':>8}")
    chain, chained = 1.0, 0
    for prev, cur in zip([None] + dates, dates):
        if prev is None:
            print(f"{cur[:7]:8} {money(grand[cur]):>13}")
            continue
        # Compare like with like: a category one snapshot lacks (its statement
        # hadn't arrived) is left out of both sides rather than read as a sale.
        common = cats[prev] & cats[cur]
        left_out = sorted((cats[prev] | cats[cur]) - common)
        total = {d: sum(r["amount"] for r in snaps[d] if r["category"] in common) for d in (prev, cur)}
        change = total[cur] - total[prev]
        a, b = ({t: p for t, p in held[d].items() if p["cat"] in common} for d in (prev, cur))
        # Both snapshots must be fully per-holding, or cost basis isn't known.
        complete = all(abs(sum(p["sgd"] for p in h.values()) - total[d]) < 0.01 * total[d] + 1
                       for h, d in ((a, prev), (b, cur))) and a and b
        y, m = int(cur[:4]), int(cur[5:7])
        consecutive = prev[:7] == f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"
        if not complete:
            print(f"{cur[:7]:8} {money(grand[cur]):>13} {grand[cur] - grand[prev]:>+12,.2f} {'?':>12} {'?':>12} "
                  f"{'':>8}  totals only — can't split")
            continue
        # Money added = change in cost basis, converted at this snapshot's own
        # SGD-per-unit rate for that holding (value in SGD / value in currency).
        added = 0.0
        for t in set(a) | set(b):
            ref = b.get(t) or a[t]
            rate = ref["sgd"] / (ref["qty"] * ref["price"]) if ref["qty"] * ref["price"] else 1.0
            none = {"cost": 0.0, "qty": 0.0}
            was, now = a.get(t, none), b.get(t, none)
            if was["cost"] is None or now["cost"] is None:
                # No cost basis on one side: value the change in quantity at today's price.
                added += (now["qty"] - was["qty"]) * ref["price"] * rate
            else:
                added += (now["cost"] - was["cost"]) * rate
        market = change - added
        base = total[prev] + added / 2                  # modified Dietz: money added mid-month
        ret = market / base if base > 0 else 0.0
        note = "" if consecutive else "  (not consecutive months)"
        if left_out:
            note += f"  (without {', '.join(left_out)}: no statement in one of the months)"
        print(f"{cur[:7]:8} {money(total[cur]):>13} {change:>+12,.2f} {added:>+12,.2f} {market:>+12,.2f} "
              f"{ret * 100:>+7.2f}%{note}")
        chain *= 1 + ret
        chained += 1 if consecutive else 0

    if chained:
        print(f"\nTime-weighted return over those {chained} month(s): {(chain - 1) * 100:+.2f}%"
              + (f"  (that pace for a year would be {(chain ** (12 / chained) - 1) * 100:+.1f}% — "
                 f"{chained} month(s) is far too few to read as a trend)" if chained else ""))
        print("'market' includes exchange-rate moves on foreign holdings, since the values are in SGD.")
    else:
        print("\nNot enough per-holding history yet to compute a return — it needs two consecutive "
              "month-end snapshots made from statements.")


if __name__ == "__main__":
    main()
