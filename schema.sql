-- Primary key is (date, category, tags): two spends in the same category on the
-- same day but with different tags stay as separate, individually recallable
-- rows. Only a re-log with the identical (date, category, tags) adds up.
-- All three tables share this shape. The primary key's own index already serves
-- every date and (date, category) lookup, so there are no secondary indexes.
-- Every script applies this file on start-up, so a missing database or table is
-- simply created.
CREATE TABLE IF NOT EXISTS expenses (
    date     DATE NOT NULL,
    category TEXT NOT NULL,
    amount   REAL NOT NULL,
    note     TEXT,
    tags     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (date, category, tags)
);

CREATE TABLE IF NOT EXISTS assets (
    date     DATE NOT NULL,
    category TEXT NOT NULL,
    amount   REAL NOT NULL,
    note     TEXT,
    tags     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (date, category, tags)
);

CREATE TABLE IF NOT EXISTS income (
    date     DATE NOT NULL,
    category TEXT NOT NULL,
    amount   REAL NOT NULL,
    note     TEXT,
    tags     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (date, category, tags)
);

-- Every line of every imported bank statement (import_statement.py). It is what
-- stops a line being imported twice, and what reconcile.py checks the ledger
-- against. `amount` is signed (negative = money out); `balance` is the running
-- balance after the line, which is what keeps two identical purchases distinct.
-- `status`: NULL = not yet placed; 'expenses:<category>' / 'income:<category>' =
-- filed there; 'manual' = was already in the ledger by hand; 'skip' = a
-- transfer, neither spending nor income.
CREATE TABLE IF NOT EXISTS bank (
    posted      DATE NOT NULL,
    date        DATE NOT NULL,
    description TEXT NOT NULL,
    amount      REAL NOT NULL,
    balance     REAL NOT NULL,
    status      TEXT,
    PRIMARY KEY (posted, amount, balance, description)
);
