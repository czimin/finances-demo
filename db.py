"""
db.py — the one place that knows where the database is and how rows merge.

Not a script. Every tool imports from here, so paths are anchored to this file
(the scripts work from any working directory) and the add-and-merge rule for
the (date, category, tags) key lives in exactly one function.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from keywords import norm_tags, split_tags

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "finances.db"
BACKUP_DIR = ROOT / "data" / "backups"
KEEP_BACKUPS = 20
TABLES = ("expenses", "income", "assets")


def money(x: float) -> str:
    """S$1,234.56 (negatives as -S$8.00)."""
    return f"-S${-x:,.2f}" if x < 0 else f"S${x:,.2f}"


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the database, creating it (or any missing table) from schema.sql."""
    path = Path(path)
    fresh = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Idempotent (CREATE TABLE IF NOT EXISTS): builds a new database, and adds
    # any table a newer schema introduced to an existing one.
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    if fresh:
        print(f"(created a new database at {path})")
    return conn


def backup(conn: sqlite3.Connection) -> Path:
    """Copy the database into data/backups/ and keep the newest KEEP_BACKUPS.

    Called by the tools that rewrite or delete rows (edit, retag, snapshot);
    the database is the only copy of the history, so they never start without
    one.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"finances-{datetime.now():%Y%m%d-%H%M%S}.db"
    conn.commit()
    with sqlite3.connect(dest) as copy:
        conn.backup(copy)
    copy.close()
    for old in sorted(BACKUP_DIR.glob("finances-*.db"))[:-KEEP_BACKUPS]:
        old.unlink()
    return dest


def merge_notes(old: str | None, new: str | None) -> str | None:
    return "; ".join(n for n in (old, new) if n) or None


def _check(table: str) -> None:
    if table not in TABLES:       # table names are interpolated into SQL below
        raise ValueError(f"unknown table {table!r}")


def upsert(conn, table, date, category, amount, note, tags="") -> float | None:
    """Insert a row, or add to the row already holding (date, category, tags).

    Returns the amount that was there before (None if the row is new). Does not
    commit.
    """
    _check(table)
    tags = norm_tags(split_tags(tags))
    key = (date, category, tags)
    old = conn.execute(
        f"SELECT amount, note FROM {table} WHERE date=? AND category=? AND tags=?", key
    ).fetchone()
    if old is None:
        conn.execute(
            f"INSERT INTO {table} (date, category, amount, note, tags) VALUES (?,?,?,?,?)",
            (date, category, amount, note, tags),
        )
        return None
    conn.execute(
        f"UPDATE {table} SET amount=?, note=? WHERE date=? AND category=? AND tags=?",
        (round(old["amount"] + amount, 2), merge_notes(old["note"], note), *key),
    )
    return old["amount"]


def relocate(conn, table, rowid, date, category, amount, note, tags) -> bool:
    """Rewrite row `rowid` with new values; if that lands on another row's key,
    fold it into that row instead. Returns True when a merge happened. Does not
    commit.
    """
    _check(table)
    tags = norm_tags(split_tags(tags))
    clash = conn.execute(
        f"SELECT rowid FROM {table} WHERE date=? AND category=? AND tags=? AND rowid<>?",
        (date, category, tags, rowid),
    ).fetchone()
    if clash is None:
        conn.execute(
            f"UPDATE {table} SET date=?, category=?, amount=?, note=?, tags=? WHERE rowid=?",
            (date, category, amount, note, tags, rowid),
        )
        return False
    conn.execute(f"DELETE FROM {table} WHERE rowid=?", (rowid,))
    upsert(conn, table, date, category, amount, note, tags)
    return True
