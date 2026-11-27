"""
add_income.py — interactive income recorder.

Same building blocks as add_expense.py, but writes to the `income` table.
Flow: date -> note -> category -> tags -> amount. The note comes first so it can
seed suggestions: INCOME_TAG_RULES (keywords.py) maps note keywords to a
category + tags (e.g. a "T-bill"/"SSB" note -> `interest`). When the category
is `hustle`, tags come from the HUSTLE_TAGS menu instead (tuition,
freelance, resale, design, …); salary/gift usually have no tags. Logging the same
(date, category, tags) twice adds to the amount.

Use the `investment` category for cash coming *back* out of a broker (e.g.
sweeping uninvested IBKR cash to your savings account) — an "IBKR"/"uninvested"
note pre-fills it. It isn't earnings: the dashboard nets it against `investment`
*expenses* to get what you actually invested, and keeps it out of income totals.

Run: python add_income.py
"""
from add_expense import get_amount, get_category, get_date, get_note, get_tags, prompt, save
from db import connect
from keywords import HUSTLE_TAGS, INCOME_CATEGORIES, classify_income, norm_tags, split_tags


def get_income_tags(category: str, suggested: list[str]) -> str:
    """Prompt for tags. `hustle` gets the HUSTLE_TAGS menu (prefix-expanded);
    every other category reuses the note-suggestion accept/edit flow."""
    if category != "hustle":
        return get_tags(suggested)
    raw = prompt(f"Hustle type ({'/'.join(HUSTLE_TAGS)}; comma-sep, blank=none)", default="")
    tokens = []
    for tok in split_tags(raw.lower()):
        tok = tok.replace(" ", "-")
        matches = [h for h in HUSTLE_TAGS if h.startswith(tok)]
        tokens.append(matches[0] if len(matches) == 1 else tok)
    return norm_tags(tokens)


def main():
    print("=== Log income (Ctrl-C when done) ===")
    count = 0
    with connect() as conn:
        try:
            while True:
                print(f"\n--- Entry {count + 1} ---")
                date = get_date()
                note = get_note()
                suggested_cat, suggested_tags = classify_income(note)
                category = get_category(INCOME_CATEGORIES, default=suggested_cat)
                tags = get_income_tags(category, suggested_tags)
                amount = get_amount()
                save(conn, date, category, amount, note, tags, table="income")
                count += 1
        except (KeyboardInterrupt, EOFError):
            print(f"\n\nDone. Logged {count} income entry(ies). Run `python export.py` "
                  "(or `python serve.py`) to refresh the dashboard.")


if __name__ == "__main__":
    main()
