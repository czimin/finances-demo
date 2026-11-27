"""
keywords.py — the one file you edit to teach the tracker about your spending.

Three editable tables, all plain dicts so you can add to them any time:

  EXPANSIONS   note autocorrect: short thing you type -> what it should read as.
  MONTHLY_SUBS / ANNUAL_SUBS   recurring services, by billing cadence.
  TAG_RULES    searchable tags + a main-category hint, keyed off note text.

Nothing here is a spending *category* on its own — a category is the single
bucket a row lives in (food, health, learning, …), while tags are cross-cutting
labels you can search by. A gym membership is category `health` AND tag
`subscription`; a piano lesson is category `learning` AND tag `piano`.

Everything is matched case-insensitively. Subscriptions are matched as
whole-note substrings; TAG_RULES too. When you add a service or a shorthand,
just drop a line in the right dict below, then run `python retag.py` to apply
it to the rows already logged.

One category is special: `investment` exists in BOTH CATEGORIES and
INCOME_CATEGORIES and means a transfer, not spending or earning — see the note
above INCOME_CATEGORIES below.

Adding a whole new expense category? It needs a colour and a trend-chart slot in
the dashboard too — see "Adding a new expense category" in README.md.
"""
import re

# --- categories -------------------------------------------------------------
# The single bucket an expense row lives in. (Asset and income lists are just
# below.)
CATEGORIES = [
    "bills", "clothes", "food", "gifts", "health", "holiday", "investment",
    "learning", "leisure", "misc", "software", "transport",
]

# --- assets -----------------------------------------------------------------
# Asset snapshot buckets, written by snapshot.py (from statements where it has
# them, typed in where it doesn't).
ASSET_CATEGORIES = ["bonds", "cash_foreign", "cash_local", "stocks_foreign", "stocks_local"]

# The Local Brokerage statement prints a fund's full name, not its ticker. Any holding
# whose name contains a key here (case-insensitive) is noted by the ticker.
LOCAL_TICKERS = {
    "straits times index etf": "ES3",
}

# --- income -----------------------------------------------------------------
# Income categories, and the tag menu offered when logging a `hustle` entry so
# you can slice self-employed income by source. HUSTLE_TAGS is a suggestion
# list, not a restriction — add to it, or type a new tag at the prompt.
INCOME_CATEGORIES = ["salary", "hustle", "gift", "interest", "investment"]
HUSTLE_TAGS = ["tuition", "freelance", "resale", "design"]

# `investment` is the mirror of the *expense* category of the same name: money
# you move out to a broker is an `investment` expense, money you pull back
# (e.g. sweeping uninvested IBKR cash to your savings account) is `investment`
# income. Neither is real earning or real spending — the dashboard treats the
# pair as transfers, nets them (expenses − income) into "invested per month",
# and keeps both out of the burn rate and the take-home income figure.

# Note-driven income classification (parallels TAG_RULES for expenses):
# keyword -> (tags to attach, category hint). Matched case-insensitively as a
# whole-note substring, AFTER autocorrect — so use the expanded form ("t-bill",
# not "tbill"). The category hint pre-fills the category prompt when logging.
INCOME_TAG_RULES = {
    "t-bill":       (["t-bill"], "interest"),
    "ssb":          (["ssb"], "interest"),
    "savings bond": (["ssb"], "interest"),
    # Cash coming back out of a brokerage account — a transfer, not earnings.
    "ibkr":         (["ibkr", "transfer"], "investment"),
    "uninvested":   (["transfer"], "investment"),
    "withdrawal":   (["transfer"], "investment"),
}

# --- note autocorrect -------------------------------------------------------
# Whole-word, case-insensitive. "abcf gym" -> "ABC Fitness gym".
# Add whatever shorthands you catch yourself typing.
EXPANSIONS = {
    "abcf": "ABC Fitness",
    "sub": "subscription",
    "tbill": "T-bill",
    "ssb": "SSB",
    # "mrt": "MRT",
}

# --- subscriptions ----------------------------------------------------------
# keyword -> (display label, main category). The keyword is matched against the
# note; a match tags the row `subscription` + the cadence (`monthly`/`annual`)
# and, if the row has no category yet, files it under the category here.
# subs.py reports on these two dicts.
MONTHLY_SUBS = {
    "cloudnote":       ("CloudNote", "software"),
    "abc fitness":     ("ABC Fitness", "health"),
    "nova mobile":     ("Nova Mobile", "bills"),
}
ANNUAL_SUBS = {
    "safeguard":       ("SafeGuard Antivirus", "software"),
}

# --- top-ups ----------------------------------------------------------------
# A note containing any of these is a one-off top-up / promo, NOT the recurring
# plan — so it must never get the `subscription` tag even if its keyword (e.g.
# "Nova Mobile") is a known subscription. Add markers as you meet them.
TOPUP_MARKERS = ("1-for-1", "1-1", "top-up", "topup")

# --- searchable tags --------------------------------------------------------
# keyword -> (tags to attach, main-category hint). The category hint only
# applies if a subscription rule above hasn't already set the category.
TAG_RULES = {
    "piano":           (["piano", "music"], "learning"),
    "music":           (["music"], "learning"),
    "abc fitness":     (["gym"], "health"),
    "board game":      (["games"], "leisure"),
    "craft corner":    (["crafts"], "leisure"),
    "escape":          (["activities"], "leisure"),
    "public transport":(["public transport"], "transport"),
    "bali trip":       (["bali-trip"], "holiday"),
    "movie":           (["movie"], "leisure"),
    # Investment costs & contributions. Deliberately narrow keys — a bare "ssb"
    # or "bond" would swallow unrelated notes, so match the fee/transfer wording.
    "ssb fee":         (["ssb"], "investment"),
    "ssb application": (["ssb"], "investment"),
    "t-bill fee":      (["t-bill"], "investment"),
    "brokerage":       (["brokerage"], "investment"),
    "ibkr":            (["ibkr"], "investment"),
    "custody fee":     (["brokerage"], "investment"),
    "trading fee":     (["brokerage"], "investment"),
}

# --- bank statement import --------------------------------------------------
# How import_statement.py files a bank transaction without asking you.
# keyword -> (category, note). The keyword is matched case-insensitively
# against the bank's own description ("DEBIT PURCHASE xx-1234 BUS/MRT 9012…");
# the first match from the top wins, so put specific keys above general ones.
# The note is what gets logged, and tags are derived from it by TAG_RULES and
# the subscription lists above — so "piano lesson" arrives tagged
# `music, piano` with nothing extra to configure.
#
# Anything that matches no rule is left for you: import_statement.py asks, and
# offers to remember the answer by adding a line here.
BANK_RULES = {
    "bus/mrt":           ("transport", "public transport"),
    "abc fitness":       ("health", "ABC Fitness gym membership"),
    "cloudnote":         ("software", "CloudNote subscription"),
    "harmony music":     ("learning", "piano lesson"),
    "freshfood mart":    ("food", "groceries"),
    "greenleaf grocer":  ("food", "groceries"),
    "bean house":        ("food", "soy milk"),
    "vendmaster":        ("food", "vending machine"),
    "kopi corner":       ("food", "coffee"),
    "starbucks":         ("food", "coffee"),
    "toast & co":        ("food", "breakfast"),
    "bubble lab":        ("food", "bubble tea"),
    "juice junction":    ("food", "juice"),
    "quickmart 24":      ("food", "convenience store"),
    "shopmall":          ("misc", "ShopMall"),
    "dollar depot":      ("misc", "Dollar Depot"),
    "homeware hub":      ("misc", "Homeware Hub"),
    "puzzle room":       ("leisure", "escape room"),
    "ridenow":           ("transport", "RideNow"),
    "trailhead outdoor": ("leisure", "Trailhead Outdoor"),
    "cineplex online":   ("leisure", "movie"),
    "ramen ya":          ("food", "ramen"),
    "froyo bar":         ("food", "frozen yogurt"),
    "noodle house":      ("food", "noodles"),
    # (import_statement.py adds remembered expense rules above this line)
}

# Money coming IN. keyword -> (income category, note). Checked before
# BANK_RULES; a deposit that instead matches BANK_RULES is a refund and is
# logged as a negative expense in that category.
BANK_INCOME_RULES = {
    "interest credit":   ("interest", "bank interest"),
    "bonus interest":    ("interest", "bank interest"),
    "cdp-sb":            ("interest", "SSB interest"),
    "cash rebate":       ("interest", "cash rebate"),
    "tan ah kow":        ("gift", "from family"),       # a PayNow sender's name
    # (import_statement.py adds remembered income rules above this line)
}

# --- functions (you shouldn't need to touch below here) ---------------------

_EXPAND_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in EXPANSIONS) + r")\b", re.IGNORECASE
) if EXPANSIONS else None


def expand(note: str | None) -> str | None:
    """Apply EXPANSIONS to a note (whole-word, case-insensitive)."""
    if not note or _EXPAND_RE is None:
        return note
    return _EXPAND_RE.sub(lambda m: EXPANSIONS[m.group(0).lower()], note)


def _apply_rules(text: str, rules: dict, category: str | None, tags: list[str]):
    """Add the tags of every rule whose keyword is in `text`; the first match
    supplies the category if nothing has set one yet."""
    for kw, (kw_tags, cat) in rules.items():
        if kw in text:
            tags += [t for t in kw_tags if t not in tags]
            category = category or cat
    return category


def classify(note: str | None) -> tuple[str | None, list[str]]:
    """Infer (category, tags) from an expense note. Category may be None.

    Subscriptions win the category race (they carry an explicit one); TAG_RULES
    only fill the category if a subscription hasn't already.
    """
    text = (note or "").lower()
    tags: list[str] = []
    category = None
    for cadence, table in (("monthly", MONTHLY_SUBS), ("annual", ANNUAL_SUBS)):
        rules = {kw: (["subscription", cadence], cat) for kw, (_label, cat) in table.items()}
        category = _apply_rules(text, rules, category, tags)
    category = _apply_rules(text, TAG_RULES, category, tags)

    # A one-off top-up is never a subscription, whatever keyword it matched.
    if any(m in text for m in TOPUP_MARKERS):
        tags = [t for t in tags if t not in ("subscription", "monthly", "annual")]
    return category, tags


def classify_income(note: str | None) -> tuple[str | None, list[str]]:
    """Infer (category, tags) from an income note via INCOME_TAG_RULES."""
    tags: list[str] = []
    return _apply_rules((note or "").lower(), INCOME_TAG_RULES, None, tags), tags


def split_tags(stored: str | None) -> list[str]:
    """Parse the stored comma form back into a list."""
    return [t for t in (s.strip() for s in (stored or "").split(",")) if t]


def norm_tags(tags: list[str]) -> str:
    """Serialize tags to the stored form: lowercase, deduped, sorted,
    comma-joined. Sorted because tags are part of the primary key — the same
    set of tags must always produce the same key, whatever order it was typed in.
    """
    return ",".join(sorted({t.strip().lower() for t in tags} - {""}))
