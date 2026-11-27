# Personal Finances Dashboard — Design Brief

Feed this file **and** `sample_data.json` to Claude Design. The brief tells you
what to build; the JSON shows the exact data shape and gives realistic content
to wire the UI against.

## Goal

One interactive dashboard for a single user that does two jobs:

1. **Look back** — explore recorded spending and assets: filter by category,
   search notes by keyword, compare categories, watch net worth grow.
2. **Look forward** — project net worth into the future from user-adjustable
   assumptions (income, spending, investment return) typed/slid **directly in
   the dashboard**, and estimate the user's **FI (financial independence)
   number** and progress toward it.

No login, no backend — a static front-end that loads one JSON file and does all
filtering, aggregation, and projection math in the browser. All amounts are in
**SGD**.

## Layout: a grid of self-contained cards

The dashboard is a responsive grid of **cards**. Each card is independent and
carries **its own controls** — adjust a card's filters and only that card
updates. (No global filter bar.)

Two cards are centerpieces, described in full below: **Expenses** (look back)
and **Projections & FI** (look forward). The rest are supporting.

## Data

The dashboard reads one JSON payload (`sample_data.json` here; in production
`export.py` writes the same shape to `dashboard_data.js` as `window.__DATA__`). It is **raw rows**, not pre-aggregated —
deliberate, because filters (especially the note keyword search) must recompute
totals on the fly.

```jsonc
{
  "generated_at": "2026-07-17T...Z",
  "currency": "SGD",
  "meta": {
    "expense_categories": ["bills","clothes","food","gifts","health","holiday","investment",
                           "learning","leisure","misc","software","transport"],
    "expense_tags":       ["activities","annual","bali-trip","crafts","games",
                           "gym","monthly","music","piano","subscription"],
    "asset_categories":   ["bonds","cash_foreign","cash_local","stocks_foreign","stocks_local"],
    "asset_tags":         ["cspx","eimi","ibkr","mmf","vwra"],
    "partial_snapshots":  { "2026-08-31": ["bonds","cash_local"] },
    "income_categories":  ["gift","hustle","interest","investment","salary"],
    "income_tags":        ["design","tuition"],
    "subscriptions":      [ { "keyword": "cloudnote", "label": "CloudNote", "cadence": "monthly" }, ... ],
    "date_min": "2026-01-01", "date_max": "2026-07-14",
    "amount_min": -39.9, "amount_max": 5000.0
  },
  "expenses": [ { "date": "2026-06-17", "category": "misc", "amount": 4.12, "note": "ShopMall", "tags": "" }, ... ],
  "assets":   [ { "date": "2026-05-31", "category": "stocks_local", "amount": 1950.00, "note": null, "tags": "" }, ... ],
  "income":   [ { "date": "2026-05-24", "category": "hustle", "amount": 240.0, "note": "tutoring", "tags": "tuition" }, ... ],
  "prices":   { "generated_at": "...", "months": 8, "fast": 50, "slow": 200,
                "holdings":  [ { "ticker": "CSPX", "symbol": "CSPX.L", "currency": "USD",
                                 "dates": ["2025-11-14", ...], "close": [612.4, ...],
                                 "fast": [598.1, ...], "slow": [571.9, ...] }, ... ],
                "watchlist": [ ...same shape... ] }   // or null
}
```

Notes about the data:
- `note` is optional free text and may merge several entries with `; `
  (e.g. `"ShopMall; Dollar Depot"`). Many rows have `note: null`.
- `tags` is a **comma-separated** string of cross-cutting labels (possibly empty,
  e.g. `"gym,monthly,subscription"` — stored sorted). Tags are orthogonal to `category`:
  a row has exactly one category but any number of tags. `subscription` marks a
  recurring cost (paired with `monthly` or `annual` for cadence); others
  (`piano`, `games`, `crafts`, …) exist so the user can slice spending across
  categories. `meta.expense_tags` lists every distinct tag in use.
- Expense `amount` can be **negative** (refunds). Don't assume positive.
- `assets` rows are snapshots taken **once a month** (last day of the month). Net
  worth for a snapshot = sum of all asset amounts on that date; should a month
  ever hold more than one snapshot date, use the **latest date in the month** for
  that month's net worth (never sum them, or you double-count). **From 2026-07 on**,
  `stocks_foreign` is broken
  down per holding — several rows on the same date, each tagged with its ticker
  (`cspx`, `vwra`, …; note holds description, quantity, price, average cost, and
  unrealized P&L in the holding's own currency), read from the user's IBKR
  Activity Statement CSV — and broker cash appears as `cash_local`/`cash_foreign`
  rows tagged `ibkr`. Earlier months have one untagged row per category.
  Summing per (date, category) therefore stays correct for every month;
  `meta.asset_tags` lists the tags in use.
- `meta.subscriptions` lists the configured recurring services (from
  `keywords.py`). Group `subscription`-tagged rows by the service whose `keyword`
  appears in the note, so differently-worded notes for one service are one line.
- `meta.date_min`/`date_max` span every expense **and** income row; the amount
  bounds are expenses only.
- `meta.partial_snapshots` maps a snapshot date to the asset categories it is
  still **missing** — a statement that hadn't arrived when the month was logged.
  Those snapshots' totals are understated, so the net-worth line and the asset
  bars must mark the month (a `*` on its label plus a note saying what's absent)
  rather than draw the dip as if the money were gone.
- `income` rows have the same shape (income categories: `salary, hustle, gift,
  interest, investment`; `meta.income_categories` / `meta.income_tags` list
  what's in use).
  `hustle` (self-employed) income carries a `tags` value naming its source —
  e.g. `tuition`, `freelance`, `resale`, `design` — so hustle earnings can be
  broken down by type the way expenses are. **Important:** `income` can be
  **empty** (`[]`, `income_categories: []`) — a ledger may start with no
  income at all. Every income-driven element must degrade gracefully to the
  zero-income case, and the Projections card must let the user type a
  hypothetical income even when no income data exists.
- **`investment` is a transfer category, present on both sides.** An
  `investment` *expense* is cash sent to the broker; `investment` *income* is
  cash swept back out (uninvested IBKR cash returning to savings). Neither is
  consumption or earning, so both are excluded from the burn rate, from the
  average-spend medallion, and from the take-home-income figure. Their
  difference — `investment expenses − investment income`, per month — is the
  cash actually put to work, and it seeds the Projections card's "% of savings
  invested". The expenses card hides investment rows by default behind a dashed
  `+ investment` chip that reveals them (persisted alongside the other
  Projections inputs in localStorage).
- `prices` is **not from the database**: `scripts/ma_cross_alert.py` (the one
  script with market-data packages) saves it to `data/prices.json` and
  `export.py` copies it in. It is `null` until that script has run. Per ticker:
  the last `months` of trading days with the daily close and the `fast`/`slow`
  simple moving averages (computed over years of history, so they are filled
  from the first day; `null` only where a fund is too new). `holdings` are the
  tickers in the latest asset snapshot (minus any the script skips); `watchlist` is the
  alert script's ticker list minus anything held. Prices are in each
  `currency`, not SGD.
- The current month is usually **partial** (last expense date ≠ month-end).
  Exclude it (or clearly flag it) when computing monthly averages, so it
  doesn't drag averages down.

---

## Centerpiece 1: the Expenses card

Three capabilities, all composable (applying several narrows the result):

1. **Filter by category** — multi-select dropdown of `expense_categories`.
   Empty selection = all categories.

2. **Inter-category comparison** — compare spending *across* categories, not
   just one total. When multiple categories are selected, show them side by
   side:
   - a **grouped/stacked bar** of total per selected category, and
   - a **multi-line trend** over months with one line per selected category.
   Include each category's share of the (filtered) total. A small toggle
   between "totals" and "over time" is welcome.

3. **Keyword search on notes** — a free-text box. Case-insensitive substring
   match on `note` (search the merged note string, so `shopmall` matches
   `"ShopMall; Dollar Depot"`). Rows with `note: null` are excluded when a keyword is
   present. Bonus: treat space-separated words as AND. Show how many rows matched.

4. **Filter by tag** — multi-select of `meta.expense_tags`. A row matches if its
   `tags` string contains the selected tag(s). This is how the user pulls a
   cross-cutting slice — e.g. tag `music` gathers lessons and books alike,
   whatever their notes say, and `subscription` gathers every recurring
   cost regardless of category. Composes with the other filters.

The card also shows, reflecting the current filters:
- a **transactions table** (date, category, amount, note), sortable, newest
  first by default;
- the **filtered total**, transaction count, and average per transaction.

Supporting controls: a **date-range slider** and an **amount-range slider**
bounded by the `meta` min/max values, a one-click **Reset**, and a short
"active filters" summary. An empty result should say so, not render blank.

---

## Centerpiece 2: the Projections & FI card

This card answers "when am I financially independent, and what moves the date?"
Everything here recomputes **live** as the user drags sliders / edits numbers.

### Inputs (the user adjusts these directly in the dashboard)

Each input is a slider **plus** an editable number field (sliders for feel,
typing for precision). Sensible defaults, derived from the data where possible:

| Input | Default | Notes |
|---|---|---|
| Monthly take-home income | avg monthly income from `income` data (full months); **S$0 when empty** | income data may be empty — the field lets the user type a hypothetical figure |
| Monthly spending | everyday spend as an exponentially weighted mean (half-life 4 mo, up to 12 full months) + lumpy spend (`holiday`, tagged `annual`) spread evenly over those months; shown with a ±1 s.e. note | editable — recorded data may not capture everything |
| % of monthly savings invested | net `investment` flow ÷ savings over the last 3 full months, else 80% | remainder accumulates as cash at 0% |
| Compound annual return (nominal) | long-run asset-class assumptions (stocks 6.5%, bonds 3%) weighted by the latest complete snapshot's stocks/bonds mix; 6% without one | range ~0–12%; geometric — the Monte Carlo median compounds at it |
| Annual return volatility (σ) | same weighting (stocks 16%, bonds 1%); 15% without a snapshot | used by Monte Carlo mode only |
| Retirement length | 50 years | range 30–60; moving it re-derives the SWR |
| Safe withdrawal rate | from retirement length: 30y 4.0%, 40y 3.6%, 50y 3.4%, 60y 3.3% (interpolated) | range 2.5–5%; drives the FI number |
| Annual inflation | 2.5% | real terms is the default view; toggle to nominal |
| Projection horizon | 30 years | range 5–50 |

Monthly savings = income − spending (show it; warn visibly if negative).
Starting point = latest net worth from `assets`. Persist input values in
`localStorage` so they survive reloads.

**Negative savings is a normal state**, not an edge case (income can be
S$0). The projection must handle it: the line slopes down, and instead of a
"years to FI" readout the card shows **runway** — "at this burn rate your net
worth lasts ~X years (until MMM YYYY)". Draw down cash first, then investments
(which still compound while they last). When the user raises income above
spending, the card flips back to FI mode.

### Outputs

- **Projection chart**: net worth from today over the horizon, compounding
  monthly. Draw the historical net-worth line (from `assets`) leading into the
  projected line so past and future read as one story. Distinguish projected
  from actual (e.g. dashed).
- **Monte Carlo mode** — a toggle: *Deterministic / Monte Carlo*. In Monte
  Carlo mode, run ~1,000 simulations client-side (monthly returns drawn
  log-normal, centred so the median path compounds at the user's return, with
  volatility σ; both annual inputs converted to monthly). Replace the single projected line with a **band**
  (10th–90th percentile, median line in the middle) and show the
  **probability of reaching FI within the horizon** as a percentage. Keep it
  fast — recompute on a debounce, and it must not jank the sliders.
- **FI number**: annual spending ÷ SWR (e.g. S$30k/yr at 4% → S$750k). Use the
  *user's* spending input (annualized), not just recorded data. Show it big.
- **FI progress**: current net worth ÷ FI number, as a progress bar/percent.
- **Years to FI**: where the projected line (median line, in Monte Carlo mode)
  crosses the FI number — mark the crossing on the chart and state it plainly
  with a calendar year/date. If FI is never reached within the horizon, say so
  honestly (or show runway instead — see above).
- **Milestones & Coast FI** — markers on the projection line, each with the
  date it's crossed:
  - **1 year of expenses saved** (12 × monthly spending input);
  - **round net-worth milestones** (next S$50k, S$100k, then every S$100k);
  - **Coast FI**: the amount which, left untouched and compounding at the
    expected return with **zero further contributions**, still reaches the FI
    number by a user-set **coast target year** (extra input, default 35 years
    out). Show the Coast FI number and, if the projection crosses it, the date
    — "after this point, saving is optional."
  Render milestones as subtle tick marks/labels, not chart clutter; a small
  list under the chart is fine too.
- Nice-to-have: a compact **scenario comparison** — pin the current inputs as
  "Scenario A", tweak, and see both lines overlaid.

Show a one-line caveat that this is an estimate, not advice, and that only
a few months of history back the defaults.

---

## Supporting cards

- **Summary** — latest net worth + change vs. previous month, average monthly
  spend over the last 6 complete months, current monthly savings estimate (from
  `income` data when present, else the Projections income input).
- **Net worth over time** — line chart from `assets`; toggle to a **stacked
  area by asset category** to see allocation shift (e.g. cash → stocks).
- **Asset breakdown** — stacked bar by month + latest month's allocation as
  percentages (donut or bar, your call). Where a month's `stocks_foreign` rows
  carry ticker tags (2026-07 onward), offer a drill-down of that slice into
  individual holdings (tag + note give name, quantity, price, avg cost,
  unrealized P&L); months without tags just show the category total.
- **Income & savings rate** — monthly income (bar) vs. spending (bar/line)
  and the resulting savings rate per month, from `income` + `expenses`. With
  no income data, show an empty-but-inviting
  state ("no income recorded yet"), not a broken chart.
- **Price trends** (full width) — one small chart per ticker, holdings and
  watchlist as two separate groups: daily close plus the 50- and 200-day SMAs
  in their own colours, golden/death crosses marked, a hover readout of that
  day's values, and a status of where the 50-day sits against the 200-day.
  With `prices: null`, an empty state that says which script to run.
- **Recurring-cost radar** — separates **fixed monthly burn** from one-off
  spending. Any row tagged `subscription` counts as recurring automatically;
  use the `monthly`/`annual` tag to normalize it to a monthly figure (annual ÷
  12). Beyond those, *detect* further recurring items by note: normalize notes
  (lowercase, split on `; `), and flag a note token that appears in ≥3 distinct
  months with similar amounts (±20%). Show: estimated fixed cost per month, the
  list of recurring items (name, monthly amount, months seen, total to date,
  and whether it was tagged or auto-detected), and fixed vs. variable share of
  total spending. Detection is fuzzy — label auto-detected ones "detected" and
  let the user dismiss false positives (persist dismissals in `localStorage`).

These can have lighter controls (a date-range slider) but no keyword search.

## Style & behavior

- Clean, modern, responsive grid. Light/dark friendly. Money as `S$1,234.56`;
  large derived figures (FI number) may round to `S$750k` style.
- Re-filtering and projection recompute must feel instant (all client-side).
- Use a charting lib of your choice (Recharts is fine).

## Out of scope

No data entry/editing of the underlying records and no backend persistence —
the dashboard is read-only over the JSON. (Exception: projection inputs persist
to `localStorage`.) The Python side (`add_expense.py`, `export.py`) produces
the JSON separately.
