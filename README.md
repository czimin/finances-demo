# Finances

A tiny personal-finance tracker, with a financial-independence projection. No AI services required. A SQLite database records
expenses, income and monthly asset snapshots. Fast and light scripts log
data, read your statements and feed an interactive dashboard. An optional daily
script watches your funds' moving averages, alerts you to golden and death
crosses (and warns when one is approaching), and charts the prices on the dashboard. Everything is in
**SGD**, formatted `S$1,234.56`. The ledger and dashboard need only the Python
standard library; the [price-alert script](#price-charts--moving-average-alerts--ma_cross_alertbat)
needs pandas, yfinance and requests.

**AI disclosure:** I designed the project's features, scripts, code architecture
and dependencies. Claude Code wrote more than 90% of the
code and docs under my supervision, subject to my review. The dashboard's visual design was
made with Claude Design.

| I want to… | Run |
|---|---|
| log a month of spending and income from my bank statement | `python import_statement.py` |
| log spending by hand | `python add_expense.py` |
| log income by hand | `python add_income.py` |
| record the month-end snapshot from my statements | `python snapshot.py` |
| look at the dashboard | `python serve.py` |
| refresh the dashboard's price charts (and get moving-average alerts) | double-click `ma_cross_alert.bat`, then `python serve.py` |
| fix, split, move or delete a row | `python edit_expense.py` (`--income` for income) |
| see recurring costs | `python subs.py` |
| check the ledger against my bank balance | `python reconcile.py` |
| see how my investments did, apart from what I put in | `python returns.py` |
| read my IBKR holdings live (read-only) | `python scripts/ibkr_mcp.py login`, then `positions` |
| delete the statement PDFs/CSVs from `data/` once they're in | double-click `clear_statements.bat` |
| apply new keyword rules to old rows | `python retag.py` |

The scripts work from any folder. If `data/finances.db` doesn't exist, the first
script you run creates it from `schema.sql`.

### Try it with demo data

This repository ships without any real financial data. To explore it, seed a
fictional dataset (a made-up person's spending, income and month-end snapshots
for 2026, plus simulated price charts) and open the dashboard:

```
python scripts/make_demo_data.py
python serve.py
```

The generator is deterministic and refuses to overwrite an existing
`data/finances.db` unless you pass `--force`. Delete `data/` to start a real
ledger of your own.

---

## The mental model: categories *and* tags

- **Category** — the *single* bucket a row lives in.
  `bills, clothes, food, gifts, health, holiday, investment, learning, leisure, misc, software, transport`
- **Tags** — *any number* of cross-cutting, searchable labels on the same row.
  `subscription` (+ `monthly`/`annual`), `piano`, `music`, `games`,
  `crafts`, `gym`, `activities`, …

| Spend | Category | Tags |
|---|---|---|
| Gym membership | `health` | `gym, monthly, subscription` |
| Piano lesson | `learning` | `music, piano` |
| Board game | `leisure` | `games` |
| S$1,000 sent to IBKR | `investment` | `ibkr` |

A "subscription" isn't a spending category — a gym sub is really health, a
music school's monthly fee is really learning — so `subscription` is a tag that cuts across
categories. Both category and tags are **suggested from the note** by
[`keywords.py`](#keywordspy--the-file-you-edit).

**`investment` is a transfer, not a spend.** Money sent to the broker is an
`investment` *expense*; money swept back out is `investment` *income*. The
dashboard nets the two and keeps both out of your burn rate, your income and
your savings rate. A dashed `+ investment` chip in the expenses card reveals the
hidden rows.

### How rows are keyed (and when they merge)

The primary key is **`(date, category, tags)`**:

- Same category, same day, **different tags** → separate rows.
- The **identical** `(date, category, tags)` again → the amount is **added** and
  the notes joined with `; `.

Tags are stored sorted (`gym,monthly,subscription`), so the order you type them
in never matters.

---

## Everyday use

### Import a bank statement — `python import_statement.py`

Save your Local Bank account statement PDFs into `data/` (any names) and run it. Each
transaction is filed by the `BANK_RULES` in `keywords.py` — a bus fare becomes
`transport / public transport`, the gym debit becomes the tagged ABC Fitness
subscription. Whatever no rule covers is shown to you one at a time:

```
  2026-09-18  OUT S$6.50   DEBIT PURCHASE | xx-1234 SUNNY BAKERY
  [e]xpense, [n]ot spending (transfer), [l]ater, [q]uit [e]:
  Note [sunny bakery]: bread
  Category (bills/clothes/food/…): food
  Remember this? Type the words that identify it [sunny bakery], Enter=yes, '-'=no:
```

Say yes to *remember* and a rule is added to `keywords.py`, so that merchant
files itself from then on (and any other waiting lines it matches are filed on
the spot). `[n]` is for money that only moved — a top-up of your own wallet, a
transfer between your accounts. `[l]` leaves a line for another day.

- **Nothing is imported twice** — every statement line is remembered, so
  re-running or overlapping statements only picks up new lines.
- **Hand-logged rows are safe.** The first import only files lines posted after
  your newest hand-logged expense (`--since YYYY-MM-DD` overrides), and a line
  whose amount is already in the ledger on that day is skipped.
- Card purchases are dated **the day you paid**, not the day the bank posted them.
- A currency-conversion fee is folded into the purchase it belongs to.
- A deposit that matches an expense rule is a **refund** (a negative expense).
- `--auto` files what the rules cover and asks nothing.

The same PDF gives `snapshot.py` your month-end bank balance.

### Log spending by hand — `python add_expense.py`

Loops until `Ctrl-C`. Asks date → note → category → tags → amount.

- **Dates**: `MM-DD` or `YYYY-MM-DD`, default today. `MM-DD` means the most
  recent such day (so `12-30` typed in January is last December).
- **Amounts**: numbers or arithmetic (`10+5.50`, `3*4.20`). A **negative**
  amount is a refund.
- **Autocorrect**: the note is expanded via `EXPANSIONS` (`abcf gym` →
  `ABC Fitness gym`).
- **Suggestions**: category and tags are pre-filled from the note. Enter
  accepts; for tags, type a comma-separated list to replace or `-` to clear.

### Log income — `python add_income.py`

Same flow, into the `income` table. Categories: `salary, hustle, gift,
interest, investment`. A "T-bill"/"SSB" note pre-fills `interest`; an
"IBKR"/"uninvested" note pre-fills `investment` (cash coming back from the
broker — a transfer, not earnings). Picking `hustle` offers the `HUSTLE_TAGS`
menu (`tuition, freelance, resale, design`; a prefix is enough).

### Look at the dashboard — `python serve.py`

Re-exports the data, serves the dashboard on `http://127.0.0.1:8000` and opens
it, then stops by itself once the page has loaded (the open tab keeps
working; a reload needs another run). Flags: `--port`, `--no-export`,
`--no-open`, `--keep` (keep serving until Ctrl-C). You can also just double-click
`dashboard.html` — run `python export.py` first if the data has changed.

The server is reachable from this computer only, and serves only the dashboard
page and its data file — never the database or your statements. The page's
only outside request is for its two Google Fonts.

The **PRICE TRENDS** card charts your holdings and watchlist with their 50- and
200-day moving averages. It is filled by the price-alert script below (the demo
data comes with simulated prices); until that has run, the card says so.

### Price charts & moving-average alerts — `ma_cross_alert.bat`

A daily check on the funds you hold or watch. For each ticker it reports:

- a **golden or death cross**: the 50-day average crossing above or below the
  200-day;
- the **price crossing its 50-day average**, an earlier and noisier signal;
- an **"approaching" warning** when the gap between the two averages is
  narrowing fast enough to cross within about 10 trading days;
- the most recent cross in the last five years, and where the 50-day stands
  against the 200-day today.

Each run also saves eight months of closes with both averages to
`data/prices.json`, for your watchlist and for the holdings in your latest
snapshot. That file feeds the dashboard's PRICE TRENDS card, so run
`python serve.py` afterwards.

This is the one part of the project that needs packages. Set up a virtual
environment once:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then double-click `ma_cross_alert.bat`, which runs the script with that
environment's Python. On macOS or Linux, run
`.venv/bin/python scripts/ma_cross_alert.py`. To have it run by itself, point
Windows Task Scheduler at the `.bat` for weekdays after the market closes, or
use cron.

Settings are at the top of `scripts/ma_cross_alert.py`:

- `TICKERS`: the Yahoo Finance symbols to check (London listings end in `.L`).
  Tickers you don't hold make up the watchlist.
- `NTFY_TOPIC`: alerts go to your phone through the free [ntfy](https://ntfy.sh)
  app if you set this to a topic name only you know. Leave it empty to only print
  alerts on screen.
- `SYMBOLS`: the Yahoo symbol for a holding that isn't in `TICKERS`
  (`es3` → `ES3.SI`). A holding without one is left off the charts, with a
  warning.
- `CHART_SKIP`: holdings to leave off the charts.

It downloads prices from Yahoo Finance (through `yfinance`), and posts to ntfy
only if you set a topic. It sends nothing from your ledger.

---

## Month-end snapshot — `python snapshot.py`

Once a month, record what everything is worth. Net worth for a month = the sum
of that month-end's asset rows.

1. Save the month's statements into `data/` under any names:
   - **IBKR** Activity Statement as **CSV** → `stocks_foreign`, one row per
     holding (tagged with the ticker; the note keeps quantity, price, average
     cost and unrealized P&L), plus any IBKR cash.
   - **Local Brokerage** *Monthly Account Statement* PDF → `stocks_local`. Its money-market
     fund balance is recorded as cash.
   - **CDP** statement PDF → `bonds`, at face value.
   - **Local Bank** account statement PDF → your bank balance (`cash_local`).
2. Run `python snapshot.py` and pick the month (it lists what it found).
3. Type in what no statement covers: any
   **foreign cash in its own currency** (`GBP 120.00`; `=` repeats last month's
   amount at the new rate). A missing statement's category can be typed in as a
   total too, or skipped with Enter.
4. Check the preview — every row, the exchange rates used, the net worth, and
   warnings for anything that moved more than 30% / S$2,000 or a holding that
   appeared or vanished — then confirm with `y`.

Good to know:

- The snapshot is dated the **last day of the statement's month**, whenever you
  run it. Back-filling an old month works the same way.
- The USD→SGD rate comes from the IBKR statement's own totals. Other currencies
  are looked up for the month-end date at `api.frankfurter.dev`, which is
  `snapshot.py`'s only network call (unless you use `--ibkr-mcp`). It sends only
  a date and a currency code. Offline, it asks you for the rate.
- **Snapshots replace, they never add.** Re-running a month overwrites what each
  source wrote before; the preview shows exactly which existing rows go.
- Skip a category and the month is marked **partial** (`*`) on the dashboard, so
  a half-logged month doesn't read as a crash. Run the script again for that
  month when the figure arrives.
- `--dry-run` previews without asking or writing anything; `--month 2026-09`
  skips the month prompt.
- `--ibkr-mcp` takes the IBKR holdings live from IBKR's MCP server instead of a
  CSV (see below). A CSV still wins when there is one, and live data is only
  accepted in the 10 days after the month-end, since it shows today's prices.
- A statement it can't read is reported with the reason (a Local Brokerage *trade
  confirmation*, for instance, carries no balance) and that category falls back
  to typing.

---

## IBKR live, through its MCP server — `python scripts/ibkr_mcp.py`

Interactive Brokers runs a hosted [Model Context Protocol](https://modelcontextprotocol.io)
server, normally used by AI assistants. `scripts/ibkr_mcp.py` is a small MCP
client in plain Python, so the ledger can use it without any AI involved.

```
python scripts/ibkr_mcp.py login        # once: opens IBKR's sign-in page in your browser
python scripts/ibkr_mcp.py status       # who is signed in, and until when
python scripts/ibkr_mcp.py logout       # forget the stored token
python scripts/ibkr_mcp.py tools        # what IBKR's server offers
python scripts/ibkr_mcp.py positions    # your holdings, as snapshot.py would record them
python scripts/ibkr_mcp.py call <tool> '{"arg": "value"}'
python snapshot.py --ibkr-mcp           # month-end snapshot with live IBKR holdings
```

- **Sign-in** is IBKR's own OAuth page. The script registers itself with IBKR,
  waits for the redirect on `127.0.0.1:33418`, and exchanges the code using
  PKCE. Your password goes to IBKR, never through the script. The token is
  saved in `data/ibkr_mcp.json` and refreshed automatically.
- **Read-only.** It asks IBKR only for the `mcp.read` scope, so the token can't
  stage or place orders, and it also refuses to call any order-like tool.
  To revoke access: Client Portal > Settings > Manage Third-Party Consents.
- **Server address:** IBKR's MCP server answers at two addresses. `login` tries
  `https://api.ibkr.com/v1/api/mcp` first (the one behind IBKR's connector in
  Claude's marketplace). If that refuses this client, it falls back to
  `https://api.ibkr.com/v1/api/mcp-public` (the one IBKR publishes for your own
  MCP clients). An address only counts once its tool list loads, and the one that
  worked is remembered. If IBKR shows an error page during sign-in instead of
  sending you back, press Ctrl-C to move on to the other address. `--url` picks one.
- `positions` finds the server's positions tool by name (`--tool` if it guesses
  wrong) and reads IBKR's usual position fields. If the reply isn't in a shape it
  recognises, it says so instead of guessing, and `snapshot.py` falls back to
  typing the total.

---

## `keywords.py` — the file you edit

Plain Python lists and dicts:

```python
CATEGORIES        = ["bills", "clothes", "food", ...]              # expense buckets
EXPANSIONS        = {"abcf": "ABC Fitness", "sub": "subscription"}   # note autocorrect
MONTHLY_SUBS      = {"cloudnote": ("CloudNote", "software")}       # keyword -> (label, category)
ANNUAL_SUBS       = {"safeguard": ("SafeGuard Antivirus", "software")}
TOPUP_MARKERS     = ("1-for-1", "1-1", "top-up", "topup")          # never tagged 'subscription'
TAG_RULES         = {"piano": (["piano", "music"], "learning")}   # keyword -> (tags, category hint)
BANK_RULES        = {"bus/mrt": ("transport", "public transport")}  # bank description -> (category, note)
BANK_INCOME_RULES = {"interest credit": ("interest", "bank interest")}
INCOME_CATEGORIES = ["salary", "hustle", "gift", "interest", "investment"]
INCOME_TAG_RULES  = {"ibkr": (["ibkr", "transfer"], "investment")}
HUSTLE_TAGS       = ["tuition", "freelance", "resale", "design"]
LOCAL_TICKERS     = {"straits times index etf": "ES3"}            # Local Brokerage fund name -> ticker
```

Add a subscription, a shorthand, a tag rule or a hustle source by adding a line,
then run `python retag.py` to apply it to rows already logged.

### Adding a new expense category

1. Add it to `CATEGORIES` in `keywords.py`.
2. Give it a colour in `CATC` in `design/Treasury.dc.html`. If it is also an
   income category, give it one in `ICATC` too.
3. Make room for it on the dashboard's category trend chart. That chart has one
   hand-written `<polyline>` per category (`trend.p0` … `trend.p11`), and
   `renderVals()` caps it with `slice(0,12)` and `i<12`. **All 12 are in use**,
   so a 13th category needs another `<polyline>` (`trend.p12`), and both caps
   raised to 13. Otherwise it is silently left off the trend lines and legend.
4. Rebuild: `python scripts/build_dashboard.py`.

A category appears in the dashboard's filters once a row uses it.

---

## Corrections & maintenance

### Fix a row — `python edit_expense.py` (`--income` for the income table)

Search by date or note keyword, pick a row, then:

- `w` move to another date · `c` recategorize · `t` retag · `n` reword · `a` amount
- `s` **split** into several rows (the last part takes whatever is left)
- `d` delete

An edit that lands on an existing `(date, category, tags)` row merges into it.

### Re-apply keywords — `python retag.py`

Normalizes notes with `EXPANSIONS` and adds any tags the rules now infer (your
manual tags are kept). Preview with `--dry-run`.

### Recurring costs — `python subs.py`

Each configured subscription's rate and total to date, an estimated fixed
monthly burn, and anything that *looks* recurring (same note in 3+ months at a
steady amount) but isn't configured yet.

### Does the ledger match the bank? — `python reconcile.py`

Month by month: what your bank balance did, against income minus expenses as
logged. The difference is the **unexplained** amount — money that left without
being logged (or moved to a broker or into bonds without an `investment` entry),
or arrived without being logged as income. It also lists statement lines still
waiting to be placed. A few dollars of noise is normal (a purchase is logged the
day you paid but may post to the bank next month).

### How did the investments do? — `python returns.py`

For each holding: what you paid, what it's worth, the gain — in its own
currency. Then month by month: how much of the change in value was money you
added and how much was the market, chained into a time-weighted return. It needs
per-holding snapshots, which start with your first snapshot from an IBKR
statement (2026-07 in the demo data); earlier months are shown as totals. A few months is far too short to read as a long-run return.

### Backups

`edit_expense.py`, `retag.py`, `snapshot.py` and `import_statement.py` copy the database to
`data/backups/` before changing anything (the newest 20 are kept). To undo,
copy the backup you want over `data/finances.db`. These protect against
mistakes, not a dead disk — copy `data/` somewhere else now and then.

### Changing the dashboard

`dashboard.html` is generated, so don't edit it by hand. Its source is
`design/Treasury.dc.html` (the markup and the logic, made with Claude Design).
After changing that file, run `python scripts/build_dashboard.py`. It adds a
small runtime, embeds `design/chest.jpeg`, and makes the page read
`dashboard_data.js` instead of the design's sample data.

If the data's shape changes, update `export.py`, `design/sample_data.json` and
`design/DASHBOARD_BRIEF.md` together. If the dashboard ever needs another
outside resource, such as a new font host, add it to `CSP` in `serve.py`, or the
browser will block it.

### Note formats are a contract

The notes on stock snapshot rows are parsed back by other code, so change a
format only together with everything that reads it:

- IBKR rows: `"<description>: <qty> @ <CCY> <close>; avg <cost>; P&L <±pnl>"`,
  written by `read_ibkr` in `snapshot.py` and by `scripts/ibkr_mcp.py`.
- Local Brokerage rows: `"Local Brokerage: <TICKER> <qty> @ <close> (avg <cost>)"`,
  written by `read_local_brokerage` in `snapshot.py`.

The readers are the `FOREIGN` and `LOCAL` patterns in `returns.py`, the
dashboard's holdings sparklines, `held_tickers()` in `scripts/ma_cross_alert.py`,
and `scripts/make_demo_data.py`, which writes the same formats.

---

## Data model

`schema.sql` — three tables of the same shape, keyed `(date, category, tags)`:

- **`expenses`** — `amount` may be negative (refunds).
- **`income`** — same merge rule. `hustle` rows are tagged by source.
- **`assets`** — month-end snapshots. Categories: `bonds, cash_foreign,
  cash_local, stocks_foreign, stocks_local`. `stocks_foreign` is one row per
  holding (tag = ticker); cash rows are the bank (untagged) plus `ibkr`,
  `mmf`, `local_brokerage`. A typed-in stock total is one untagged row (in the
  demo data, every month before 2026-07).

A fourth table, **`bank`**, holds every line of every imported bank statement
and where it was filed — that is what prevents double imports and what
`reconcile.py` checks against.

`tags` is a comma-separated string.

---

## Files

```
clear_statements.bat  delete the statement PDFs/CSVs from data/ (asks first)
ma_cross_alert.bat moving-average alerts + dashboard price charts (runs scripts/ma_cross_alert.py)
requirements.txt   packages for ma_cross_alert.py only (pandas, yfinance, requests)
import_statement.py  log expenses + income from bank statement PDFs
reconcile.py       bank balance vs ledger, month by month
returns.py         investment gains vs money added
add_expense.py     log expenses by hand (also holds the shared prompts)
add_income.py      log income
snapshot.py        month-end asset snapshot from statements
edit_expense.py    correct / move / split / delete rows
retag.py           re-apply keywords.py across history
subs.py            recurring-cost report
export.py          database -> dashboard_data.js
serve.py           export + local server + open the dashboard
keywords.py        THE file you edit: categories, expansions, subs, tag rules
db.py              shared: database path, merge rule, backups
schema.sql         database schema
dashboard.html     the dashboard (generated from design/ — see "Changing the dashboard")
scripts/           build_dashboard.py (regenerates dashboard.html), pdftext.py + pdfitems.py (PDF readers),
                   make_demo_data.py (seeds a fictional dataset), ibkr_mcp.py (IBKR MCP client),
                   ma_cross_alert.py (price alerts; writes data/prices.json)
design/            dashboard design source, brief, sample data
data/              the database, its backups, and statements you drop in
```
