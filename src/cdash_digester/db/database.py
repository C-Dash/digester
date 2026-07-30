"""Database — owns the SQLite connection, schema, migrations, row factory.

Extracted from the former monolithic BatchDB. The connection (`con`) is shared
with the repository classes; behavior (WAL, foreign keys, bool columns) is
unchanged.
"""

import sqlite3
from pathlib import Path

# Boolean status columns stored as INTEGER 0/1 and surfaced as Python bools.
_BOOL_COLS = {"ready", "name_ready", "media_ready", "accepted"}

# Per-scan working tables (cleared on a full rescan); caches/batch are preserved.
_WORKING_TABLES = ("cdash_media", "cdash_doc", "cdash_place", "cdash_folder")

# Persistent caches (validator + prescreener results); survive a working-table
# clear but can be purged on demand to force re-fetch/re-screen.
_CACHE_TABLES = ("cdash_folder_cache", "cdash_place_cache", "cdash_file_cache")

_SCHEMA = [
    # ------ batch
    """CREATE TABLE IF NOT EXISTS cdash_batch (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id          TEXT,
        name              TEXT,
        batch_folder_path TEXT,
        initialized_date  TEXT,
        rejected_count    INTEGER DEFAULT 0,
        ready             INTEGER DEFAULT 0,
        note              TEXT,
        folders_count     INTEGER DEFAULT 0,
        places_count      INTEGER DEFAULT 0,
        documents_count   INTEGER DEFAULT 0,
        media_count       INTEGER DEFAULT 0,
        repaired_count    INTEGER DEFAULT 0
    )""",
    # ------ folder
    """CREATE TABLE IF NOT EXISTS cdash_folder (
        folder_number     INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_folder_id   TEXT,
        cdash_folder_name TEXT,
        item_set_id       INTEGER UNIQUE,
        os_folder_name    TEXT,
        name_ready        INTEGER DEFAULT 0,
        media_ready       INTEGER DEFAULT 0,
        notes             TEXT
    )""",
    # ------ place
    """CREATE TABLE IF NOT EXISTS cdash_place (
        place_item_id INTEGER PRIMARY KEY,
        place_name    TEXT,
        place_type    TEXT,
        house_num     TEXT,
        street_name   TEXT,
        street_sort   TEXT,
        neighborhood  TEXT,
        chc_dist      TEXT,
        item_set_ids  TEXT,
        lat           REAL,
        lon           REAL,
        ready         INTEGER DEFAULT 0,
        notes         TEXT
    )""",
    # ------ doc
    """CREATE TABLE IF NOT EXISTS cdash_doc (
        doc_item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        place_item_id        INTEGER REFERENCES cdash_place(place_item_id),
        folder_doc_sequence  INTEGER,
        item_set_id          INTEGER REFERENCES cdash_folder(item_set_id),
        doc_title            TEXT,
        doc_type_code        TEXT,
        doc_type_description TEXT,
        date_accepted        TEXT,
        batch_doc_id         TEXT,
        num_pages            INTEGER DEFAULT 0,
        ready                INTEGER DEFAULT 0,
        notes                TEXT
    )""",
    # ------ media
    """CREATE TABLE IF NOT EXISTS cdash_media (
        media_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_item_id    INTEGER REFERENCES cdash_doc(doc_item_id),
        item_set_id    INTEGER REFERENCES cdash_folder(item_set_id),
        filename       TEXT,
        batch_media_id TEXT,
        filepath       TEXT,
        page_num     INTEGER,
        capture_date TEXT,
        date_source  TEXT,
        file_size_mb REAL,
        pixel_width  INTEGER,
        pixel_height INTEGER,
        format         TEXT,
        format_issues  TEXT,
        repair_issues TEXT,
        ready        INTEGER DEFAULT 0,
        filename_issues TEXT
    )""",
    # ------ folder cache (persists across scans; validator results)
    """CREATE TABLE IF NOT EXISTS cdash_folder_cache (
        item_set_id       INTEGER PRIMARY KEY,
        cdash_folder_name TEXT,
        folder_index      INTEGER,
        status            TEXT,
        fetched_date      TEXT
    )""",
    # ------ place cache (persists across scans; validator results)
    """CREATE TABLE IF NOT EXISTS cdash_place_cache (
        place_item_id INTEGER PRIMARY KEY,
        place_name    TEXT,
        place_type    TEXT,
        house_num     TEXT,
        street_name   TEXT,
        street_sort   TEXT,
        neighborhood  TEXT,
        chc_dist      TEXT,
        item_set_ids  TEXT,
        lat           REAL,
        lon           REAL,
        status        TEXT,
        fetched_date  TEXT
    )""",
    # ------ file cache (persists across scans; prescreener results)
    """CREATE TABLE IF NOT EXISTS cdash_file_cache (
        filepath        TEXT PRIMARY KEY,
        file_size_bytes INTEGER,
        mtime_ns        INTEGER,
        accepted        INTEGER,
        file_size_mb    REAL,
        pixel_width     INTEGER,
        pixel_height    INTEGER,
        format          TEXT,
        capture_date    TEXT,
        date_source     TEXT,
        format_issues   TEXT,
        repair_issues   TEXT,
        pdf_pages       INTEGER,
        fetched_date    TEXT
    )""",
]

# Columns added to databases that predate later schema additions.
_MIGRATIONS = [
    ("cdash_batch", "folders_count",   "INTEGER DEFAULT 0"),
    ("cdash_batch", "places_count",    "INTEGER DEFAULT 0"),
    ("cdash_batch", "documents_count", "INTEGER DEFAULT 0"),
    ("cdash_batch", "media_count",     "INTEGER DEFAULT 0"),
    ("cdash_batch", "repaired_count",  "INTEGER DEFAULT 0"),
]


class Database:
    """SQLite connection, schema, migrations, and bool-aware row factory."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.con = sqlite3.connect(str(db_path), check_same_thread=False)
        self.con.row_factory = self._bool_row_factory
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _bool_row_factory(cursor: sqlite3.Cursor, row: tuple):
        """sqlite3.Row-compatible factory that also converts 0/1 bool columns."""
        fields = [d[0] for d in cursor.description]
        d = {}
        for name, val in zip(fields, row):
            if name in _BOOL_COLS and val is not None:
                d[name] = bool(val)
            else:
                d[name] = val
        return d

    def create_all_tables(self):
        """Create all batch tables (idempotent), then run migrations."""
        for stmt in _SCHEMA:
            self.con.execute(stmt)
        self.con.commit()
        self._migrate()

    def _migrate(self):
        """Add new columns to existing databases that predate schema additions."""
        for table, col, defn in _MIGRATIONS:
            try:
                self.con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                self.con.commit()
            except Exception:
                pass  # column already exists

    def clear_working_tables(self):
        """Delete all rows from the per-scan working tables, preserving
        cdash_batch and any persistent cache tables."""
        for t in _WORKING_TABLES:
            self.con.execute(f"DELETE FROM {t}")
        self.con.commit()

    def clear_caches(self) -> int:
        """Delete all rows from the persistent cache tables. Returns rows removed."""
        total = 0
        for t in _CACHE_TABLES:
            total += self.con.execute(f"DELETE FROM {t}").rowcount
        self.con.commit()
        return total

    def close(self):
        self.con.close()
