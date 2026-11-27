"""
export.py — dump finances.db into the data file the dashboard reads.
Run after any data change (serve.py does it for you). Safe to run anytime.

    python export.py

Writes dashboard_data.js (`window.__DATA__ = {...}`), which dashboard.html loads
with a plain <script> tag — so the dashboard works both served and opened by
double-click. It holds raw rows, not aggregates: the dashboard filters and
totals in the browser, which it must, because filtering by note keyword can't be
precomputed. The payload's shape matches design/sample_data.json — see
design/DASHBOARD_BRIEF.md.
"""
import json
from datetime import date, datetime, timezone

from db import ROOT, TABLES, connect
from keywords import ANNUAL_SUBS, MONTHLY_SUBS

JS_PATH = ROOT / "dashboard_data.js"
# Written by scripts/ma_cross_alert.py (it alone has the market-data packages):
# recent closes plus 50/200-day SMAs for holdings and the watchlist.
PRICES_PATH = ROOT / "data" / "prices.json"


def prices() -> dict | None:
    try:
        return json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def partial_snapshots(assets: list[dict]) -> dict[str, list[str]]:
    """{snapshot date: [categories it is still missing]}.

    A snapshot is *partial* when it lacks something the previous one had — a
    statement that hasn't arrived yet, not a balance that went to zero. Its net
    worth is understated, so the dashboard marks the point rather than plotting
    a dip that never happened.

    A category is missing if it vanished outright, or if its untagged headline
    row (the bank balance, the CDP face value) vanished *and* the category
    total more than halved. Both halves are needed: on their own, the
    structural test misreads 2026-07's switch of stocks_foreign from one total
    to per-holding rows, and the value test misreads a real cash_foreign
    transfer out in 2026-03. Together they catch a headline figure that hasn't
    been filled in without crying wolf over a broker's row layout changing.
    """
    bare_on: dict[str, set] = {}
    total_on: dict[str, dict] = {}
    for r in assets:
        totals = total_on.setdefault(r["date"], {})
        totals[r["category"]] = totals.get(r["category"], 0) + r["amount"]
        bare = bare_on.setdefault(r["date"], set())
        if not r["tags"]:
            bare.add(r["category"])
    partial = {}
    dates = sorted(total_on)
    for prev, cur in zip(dates, dates[1:]):
        gutted = {c for c in bare_on[prev] - bare_on[cur]
                  if total_on[cur].get(c, 0) < total_on[prev][c] / 2}
        missing = sorted(gutted | (total_on[prev].keys() - total_on[cur].keys()))
        if missing:
            partial[cur] = missing
    return partial


def build() -> dict:
    with connect() as conn:
        expenses, income, assets = (
            [dict(r) for r in conn.execute(
                f"SELECT date, category, amount, note, tags FROM {t} ORDER BY date, category, tags")]
            for t in TABLES)
    conn.close()

    cats = lambda rows: sorted({r["category"] for r in rows})
    tags = lambda rows: sorted({t for r in rows for t in r["tags"].split(",") if t})
    # The dashboard's date axis spans every dated cash flow — income logged after
    # the last expense must still fall inside it, or its filters hide the row.
    dates = [r["date"] for r in expenses + income] or [date.today().isoformat()]
    amts = [r["amount"] for r in expenses] or [0]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "currency": "SGD",
        "meta": {
            "expense_categories": cats(expenses),
            "expense_tags": tags(expenses),
            "asset_categories": cats(assets),
            "asset_tags": tags(assets),
            "partial_snapshots": partial_snapshots(assets),
            "income_categories": cats(income),
            "income_tags": tags(income),
            # Configured recurring services, so the dashboard groups their charges
            # by service rather than by however each note happened to be worded.
            "subscriptions": [
                {"keyword": kw.lower(), "label": label, "cadence": cadence}
                for cadence, table in (("monthly", MONTHLY_SUBS), ("annual", ANNUAL_SUBS))
                for kw, (label, _cat) in table.items()],
            "date_min": min(dates),
            "date_max": max(dates),
            "amount_min": min(amts),
            "amount_max": max(amts),
        },
        "expenses": expenses,
        "assets": assets,
        "income": income,
        "prices": prices(),
    }


def main():
    data = build()
    # "</" is escaped so a note can never close a <script> block if this payload
    # is ever inlined into a page.
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    tmp = JS_PATH.with_suffix(".js.tmp")
    tmp.write_text("window.__DATA__=" + payload + ";\n", encoding="utf-8")
    tmp.replace(JS_PATH)      # atomic: the dashboard never sees a half-written file
    print(f"Wrote {JS_PATH.name} ({JS_PATH.stat().st_size:,} bytes: {len(data['expenses'])} expenses, "
          f"{len(data['assets'])} asset rows, {len(data['income'])} income rows)")


if __name__ == "__main__":
    main()
