"""
CDASH Objects Module  (cdash_objects.py)

Defines the data model and persistence layer for a CDASH Import Batch.

Layout
------
  DOC_TYPES        — fixed document-type code → description mapping
  slugify()        — filesystem-safe name normaliser
  parse_batch_name()   \
  parse_folder_name()   > name parsers used by the digester
  parse_media_name()   /
  BatchDB          — all SQLite CRUD; one instance per open batch
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOC_TYPES: dict[str, str] = {
    "VE": "Exterior View",
    "VI": "Interior View",
    "RF": "Research Form",
    "AI": "Architectural Inventory Form",
    "VP": "Plan View",
    "CD": "Correspondence",
    "RN": "Research Notes",
    "HS": "Historic American Buildings Survey",
    "CS": "Contact Sheet",
    "AM": "Published Material",
    "SM": "Supplemental Material",
    "VD": "Detail View",
    "EP": "Ephemera",
    "DM": "Demolition Memo",
    "UC": "Uncategorized",
}

ACCEPTED_SUFFIXES = {".tif", ".tiff", ".jpg", ".jpeg", ".pdf"}

# Place property keys shared by the validator output, the cdash_place working
# table, and the cdash_place_cache table.
PLACE_PROP_KEYS = (
    "place_name", "place_type", "house_num", "street_name", "street_sort",
    "neighborhood", "chc_dist", "item_set_ids", "lat", "lon",
)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Replace spaces with underscores; drop all non-alphanumeric except - and _."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "", text.replace(" - ", "-").replace(" ", "_"))


# ---------------------------------------------------------------------------
# Name parsers
# ---------------------------------------------------------------------------

# Batch folder:  CDB<YYMMDD>[a-z]?-<name>
_BATCH_RE = re.compile(r"^CDB(?P<date>\d{6})(?P<letter>[a-z])?-(?P<name>.+)$")

# Media folder:  [F<index>-]<slug>-OF<item_set_id>
_FOLDER_RE = re.compile(
    r"^(?:F(?P<folder_index>\d+)-)?(?P<slug>.+)-OF(?P<item_set_id>\d+)$"
)

# Ready media stem:
#   <place_slug>_<doc_index>p<page_index>-<doc_type>[-OP<place_id>]
_MEDIA_RE = re.compile(
    r"^(?P<place_slug>.+)_(?P<doc_index>\d{4})p(?P<page_index>\d{4})"
    r"-(?P<doc_type>[A-Z]{2})(?:-OP(?P<place_id>\d+))?$",
    re.IGNORECASE,
)


def parse_batch_name(folder_name: str) -> Optional[dict]:
    """Parse a batch folder name.  Returns a dict or None on mismatch."""
    m = _BATCH_RE.match(folder_name)
    if not m:
        return None
    return {
        "batch_id": f"CDB{m.group('date')}{m.group('letter') or ''}",
        "date":     m.group("date"),
        "letter":   m.group("letter"),
        "name":     m.group("name"),
    }


def parse_folder_name(folder_name: str) -> Optional[dict]:
    """Parse a media folder name.  Returns a dict or None on mismatch."""
    m = _FOLDER_RE.match(folder_name)
    if not m:
        return None
    return {
        "folder_index": int(m.group("folder_index")) if m.group("folder_index") else None,
        "slug":         m.group("slug"),
        "item_set_id":  int(m.group("item_set_id")),
    }


def parse_media_name(stem: str) -> Optional[dict]:
    """Parse a media filename stem (no extension).

    Returns a dict with place_slug, doc_index, page_index, doc_type,
    place_id (int or None), or None if the name is not in ready format.
    """
    m = _MEDIA_RE.match(stem)
    if not m:
        return None
    return {
        "place_slug": m.group("place_slug"),
        "doc_index":  int(m.group("doc_index")),
        "page_index": int(m.group("page_index")),
        "doc_type":   m.group("doc_type").upper(),
        "place_id":   int(m.group("place_id")) if m.group("place_id") else None,
    }


# ---------------------------------------------------------------------------
# BatchDB  — SQLite access layer
# ---------------------------------------------------------------------------

class BatchDB:
    """Manages the batch SQLite database.

    One instance is created per open batch.  All CRUD for every entity
    lives here so that the domain classes (digester, prescreener, etc.)
    share a single, testable data-access object.

    Boolean status columns (ready, name_ready, media_ready) are stored as
    INTEGER 0/1 in SQLite and surfaced as Python bools via the row factory.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = self._bool_row_factory
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------------ row factory

    @staticmethod
    def _bool_row_factory(cursor: sqlite3.Cursor, row: tuple):
        """sqlite3.Row-compatible factory that also converts 0/1 bool columns."""
        _BOOL_COLS = {
            "ready", "name_ready", "media_ready", "accepted",
        }
        fields = [d[0] for d in cursor.description]
        d = {}
        for name, val in zip(fields, row):
            if name in _BOOL_COLS and val is not None:
                d[name] = bool(val)
            else:
                d[name] = val
        return d

    # ------------------------------------------------------------------ schema

    def create_all_tables(self):
        """Create all batch tables (idempotent)."""
        stmts = [
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
                format_note  TEXT,
                repair_issues TEXT,
                ready        INTEGER DEFAULT 0,
                notes        TEXT
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
                qa_note         TEXT,
                repair_issues   TEXT,
                pdf_pages       INTEGER,
                fetched_date    TEXT
            )""",
        ]
        for stmt in stmts:
            self._con.execute(stmt)
        self._con.commit()
        self._migrate()

    def clear_working_tables(self):
        """Delete all rows from the per-scan working tables, preserving
        cdash_batch and any persistent cache tables."""
        for t in ("cdash_media", "cdash_doc", "cdash_place", "cdash_folder"):
            self._con.execute(f"DELETE FROM {t}")
        self._con.commit()

    def _migrate(self):
        """Add new columns to existing databases that predate schema additions."""
        new_cols = [
            ("cdash_batch", "folders_count",   "INTEGER DEFAULT 0"),
            ("cdash_batch", "places_count",     "INTEGER DEFAULT 0"),
            ("cdash_batch", "documents_count",  "INTEGER DEFAULT 0"),
            ("cdash_batch", "media_count",      "INTEGER DEFAULT 0"),
            ("cdash_batch", "repaired_count",   "INTEGER DEFAULT 0"),
        ]
        for table, col, defn in new_cols:
            try:
                self._con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                self._con.commit()
            except Exception:
                pass  # column already exists

    # ------------------------------------------------------------------ batch

    def upsert_batch(self, batch_id: str, name: str, batch_folder_path: str,
                     initialized_date: str, ready: bool = False, note: str = ""):
        existing = self._con.execute(
            "SELECT id FROM cdash_batch WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if existing:
            self._con.execute(
                """UPDATE cdash_batch
                   SET name=?, batch_folder_path=?, initialized_date=?,
                       ready=?, note=?
                   WHERE batch_id=?""",
                (name, batch_folder_path, initialized_date,
                 int(ready), note, batch_id),
            )
        else:
            self._con.execute(
                """INSERT INTO cdash_batch
                   (batch_id, name, batch_folder_path, initialized_date, ready, note)
                   VALUES (?,?,?,?,?,?)""",
                (batch_id, name, batch_folder_path, initialized_date,
                 int(ready), note),
            )
        self._con.commit()

    def get_batch(self) -> Optional[dict]:
        return self._con.execute("SELECT * FROM cdash_batch LIMIT 1").fetchone()

    def set_batch_ready(self, ready: bool, note: str = ""):
        self._con.execute(
            "UPDATE cdash_batch SET ready=?, note=?", (int(ready), note)
        )
        self._con.commit()

    def set_rejected_count(self, count: int):
        self._con.execute(
            "UPDATE cdash_batch SET rejected_count=?", (count,)
        )
        self._con.commit()

    def count_batch_stats(self) -> dict:
        """Live COUNT(*) for the four DB-derived batch counts."""
        q = self._con.execute
        return {
            "folders":   q("SELECT COUNT(*) AS n FROM cdash_folder").fetchone()["n"],
            "places":    q("SELECT COUNT(*) AS n FROM cdash_place").fetchone()["n"],
            "documents": q("SELECT COUNT(*) AS n FROM cdash_doc").fetchone()["n"],
            "media":     q("SELECT COUNT(*) AS n FROM cdash_media").fetchone()["n"],
        }

    def update_batch_counts(self, folders: int, places: int, documents: int,
                            media: int, rejects: int, repaired: int):
        self._con.execute(
            "UPDATE cdash_batch SET folders_count=?, places_count=?, "
            "documents_count=?, media_count=?, rejected_count=?, repaired_count=?",
            (folders, places, documents, media, rejects, repaired),
        )
        self._con.commit()

    def recalculate_batch_ready(self) -> bool:
        """Batch is ready iff every folder has name_ready=1 AND media_ready=1."""
        row = self._con.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN name_ready=1 AND media_ready=1 THEN 1 ELSE 0 END) AS n_ready
               FROM cdash_folder"""
        ).fetchone()
        ready = bool(row and row["total"] > 0 and row["total"] == row["n_ready"])
        self._con.execute("UPDATE cdash_batch SET ready=?", (int(ready),))
        self._con.commit()
        return ready

    # ------------------------------------------------------------------ folder

    def upsert_folder(self, item_set_id: int, cdash_folder_name: str,
                      os_folder_name: str, batch_folder_id: str = "",
                      name_ready: bool = False,
                      media_ready: bool = False, notes: str = ""):
        existing = self._con.execute(
            "SELECT folder_number FROM cdash_folder WHERE item_set_id=?",
            (item_set_id,),
        ).fetchone()
        if existing:
            self._con.execute(
                """UPDATE cdash_folder
                   SET batch_folder_id=?, cdash_folder_name=?, os_folder_name=?,
                       name_ready=?, media_ready=?, notes=?
                   WHERE item_set_id=?""",
                (batch_folder_id, cdash_folder_name, os_folder_name,
                 int(name_ready), int(media_ready), notes, item_set_id),
            )
        else:
            self._con.execute(
                """INSERT INTO cdash_folder
                   (batch_folder_id, cdash_folder_name, item_set_id, os_folder_name,
                    name_ready, media_ready, notes)
                   VALUES (?,?,?,?,?,?,?)""",
                (batch_folder_id, cdash_folder_name, item_set_id, os_folder_name,
                 int(name_ready), int(media_ready), notes),
            )
        self._con.commit()

    def get_folders(self) -> list:
        return self._con.execute(
            "SELECT * FROM cdash_folder ORDER BY folder_number"
        ).fetchall()

    def get_folder_by_item_set(self, item_set_id: int) -> Optional[dict]:
        return self._con.execute(
            "SELECT * FROM cdash_folder WHERE item_set_id=?", (item_set_id,)
        ).fetchone()

    def set_folder_name_ready(self, item_set_id: int, ready: bool, notes: str = ""):
        self._con.execute(
            "UPDATE cdash_folder SET name_ready=?, notes=? WHERE item_set_id=?",
            (int(ready), notes, item_set_id),
        )
        self._con.commit()

    def assign_folder_index(self, item_set_id: int, index: int):
        self._con.execute(
            "UPDATE cdash_folder SET folder_number=? WHERE item_set_id=?",
            (index, item_set_id),
        )
        self._con.commit()

    def recalculate_folder_status(self, item_set_id: int):
        """Set media_ready = True iff every media file in the folder is ready."""
        row = self._con.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN ready=1 THEN 1 ELSE 0 END) AS n_ready
               FROM cdash_media WHERE item_set_id=?""",
            (item_set_id,),
        ).fetchone()
        media_ready = bool(row and row["total"] > 0 and row["total"] == row["n_ready"])
        self._con.execute(
            "UPDATE cdash_folder SET media_ready=? WHERE item_set_id=?",
            (int(media_ready), item_set_id),
        )
        self._con.commit()

    # ------------------------------------------------------------------ place

    def upsert_place(self, place_item_id: int, place_name: str,
                     place_type=None, house_num=None, street_name=None,
                     street_sort=None, neighborhood=None, chc_dist=None,
                     item_set_ids=None, lat=None, lon=None,
                     ready: bool = True, notes: str = ""):
        existing = self._con.execute(
            "SELECT place_item_id FROM cdash_place WHERE place_item_id=?",
            (place_item_id,),
        ).fetchone()
        if existing:
            self._con.execute(
                """UPDATE cdash_place
                   SET place_name=?, place_type=?, house_num=?, street_name=?,
                       street_sort=?, neighborhood=?, chc_dist=?, item_set_ids=?,
                       lat=?, lon=?, ready=?, notes=?
                   WHERE place_item_id=?""",
                (place_name, place_type, house_num, street_name, street_sort,
                 neighborhood, chc_dist, item_set_ids, lat, lon,
                 int(ready), notes, place_item_id),
            )
        else:
            self._con.execute(
                """INSERT INTO cdash_place
                   (place_item_id, place_name, place_type, house_num, street_name,
                    street_sort, neighborhood, chc_dist, item_set_ids,
                    lat, lon, ready, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (place_item_id, place_name, place_type, house_num, street_name,
                 street_sort, neighborhood, chc_dist, item_set_ids, lat, lon,
                 int(ready), notes),
            )
        self._con.commit()

    def get_place(self, place_item_id: int) -> Optional[dict]:
        return self._con.execute(
            "SELECT * FROM cdash_place WHERE place_item_id=?", (place_item_id,)
        ).fetchone()

    # ----------------------------------------------------- persistent caches

    def get_folder_cache(self, item_set_id: int) -> Optional[dict]:
        return self._con.execute(
            "SELECT * FROM cdash_folder_cache WHERE item_set_id=?", (item_set_id,)
        ).fetchone()

    def upsert_folder_cache(self, item_set_id: int, cdash_folder_name: str,
                            folder_index: int, status: str = "valid"):
        self._con.execute(
            """INSERT INTO cdash_folder_cache
                   (item_set_id, cdash_folder_name, folder_index, status, fetched_date)
               VALUES (?,?,?,?,?)
               ON CONFLICT(item_set_id) DO UPDATE SET
                   cdash_folder_name=excluded.cdash_folder_name,
                   folder_index=excluded.folder_index,
                   status=excluded.status,
                   fetched_date=excluded.fetched_date""",
            (item_set_id, cdash_folder_name, folder_index, status,
             datetime.now().isoformat(timespec="seconds")),
        )
        self._con.commit()

    def max_folder_cache_index(self) -> int:
        """Highest folder_index recorded in the folder cache (0 if empty)."""
        row = self._con.execute(
            "SELECT MAX(folder_index) AS m FROM cdash_folder_cache"
        ).fetchone()
        return row["m"] if row and row["m"] is not None else 0

    def get_place_cache(self, place_item_id: int) -> Optional[dict]:
        return self._con.execute(
            "SELECT * FROM cdash_place_cache WHERE place_item_id=?", (place_item_id,)
        ).fetchone()

    def upsert_place_cache(self, place_item_id: int, props: dict,
                           status: str = "valid"):
        cols = ", ".join(PLACE_PROP_KEYS)
        vals = [props.get(k) for k in PLACE_PROP_KEYS]
        placeholders = ", ".join("?" for _ in PLACE_PROP_KEYS)
        updates = ", ".join(f"{k}=excluded.{k}" for k in PLACE_PROP_KEYS)
        self._con.execute(
            f"""INSERT INTO cdash_place_cache
                    (place_item_id, {cols}, status, fetched_date)
                VALUES (?, {placeholders}, ?, ?)
                ON CONFLICT(place_item_id) DO UPDATE SET
                    {updates},
                    status=excluded.status,
                    fetched_date=excluded.fetched_date""",
            (place_item_id, *vals, status,
             datetime.now().isoformat(timespec="seconds")),
        )
        self._con.commit()

    def get_file_cache(self, filepath: str) -> Optional[dict]:
        return self._con.execute(
            "SELECT * FROM cdash_file_cache WHERE filepath=?", (filepath,)
        ).fetchone()

    def upsert_file_cache(self, filepath: str, file_size_bytes: int,
                          mtime_ns: int, accepted: bool, props: dict):
        self._con.execute(
            """INSERT INTO cdash_file_cache
                   (filepath, file_size_bytes, mtime_ns, accepted, file_size_mb,
                    pixel_width, pixel_height, format, capture_date, date_source,
                    qa_note, repair_issues, pdf_pages, fetched_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(filepath) DO UPDATE SET
                   file_size_bytes=excluded.file_size_bytes,
                   mtime_ns=excluded.mtime_ns,
                   accepted=excluded.accepted,
                   file_size_mb=excluded.file_size_mb,
                   pixel_width=excluded.pixel_width,
                   pixel_height=excluded.pixel_height,
                   format=excluded.format,
                   capture_date=excluded.capture_date,
                   date_source=excluded.date_source,
                   qa_note=excluded.qa_note,
                   repair_issues=excluded.repair_issues,
                   pdf_pages=excluded.pdf_pages,
                   fetched_date=excluded.fetched_date""",
            (filepath, file_size_bytes, mtime_ns, int(accepted),
             props.get("file_size_mb"), props.get("pixel_width"),
             props.get("pixel_height"), props.get("format"),
             props.get("capture_date"), props.get("date_source"),
             props.get("qa_note"), ", ".join(props.get("repair_issues", [])),
             props.get("pdf_pages"),
             datetime.now().isoformat(timespec="seconds")),
        )
        self._con.commit()

    def update_file_cache_path(self, old_path: str, new_path: str):
        """Re-key a file-cache row after the file is renamed on disk.
        Drops any stale row already occupying new_path first."""
        self._con.execute(
            "DELETE FROM cdash_file_cache WHERE filepath=?", (new_path,)
        )
        self._con.execute(
            "UPDATE cdash_file_cache SET filepath=? WHERE filepath=?",
            (new_path, old_path),
        )
        self._con.commit()

    # -------------------------------------------------------------------- doc

    def insert_doc(self, place_item_id: int, item_set_id: int,
                   folder_doc_sequence: int, doc_type_code: str,
                   doc_title: str, batch_doc_id: str,
                   date_accepted: str = None,
                   ready: bool = False, notes: str = "") -> int:
        doc_type_description = DOC_TYPES.get(doc_type_code, "Uncategorized")
        cur = self._con.execute(
            """INSERT INTO cdash_doc
               (place_item_id, item_set_id, folder_doc_sequence, doc_title,
                doc_type_code, doc_type_description, date_accepted,
                batch_doc_id, num_pages, ready, notes)
               VALUES (?,?,?,?,?,?,?,?,0,?,?)""",
            (place_item_id, item_set_id, folder_doc_sequence, doc_title,
             doc_type_code, doc_type_description, date_accepted,
             batch_doc_id, int(ready), notes),
        )
        self._con.commit()
        return cur.lastrowid

    def get_doc(self, doc_item_id: int) -> Optional[dict]:
        return self._con.execute(
            "SELECT * FROM cdash_doc WHERE doc_item_id=?", (doc_item_id,)
        ).fetchone()

    def get_docs_for_folder(self, item_set_id: int) -> list:
        return self._con.execute(
            """SELECT * FROM cdash_doc WHERE item_set_id=?
               ORDER BY folder_doc_sequence""",
            (item_set_id,),
        ).fetchall()

    def increment_doc_pages(self, doc_item_id: int,
                            capture_date: str = None, count: int = 1):
        """Increment num_pages by count; keep date_accepted as the earliest capture date."""
        self._con.execute(
            "UPDATE cdash_doc SET num_pages = num_pages + ? WHERE doc_item_id=?",
            (count, doc_item_id),
        )
        if capture_date:
            existing = self._con.execute(
                "SELECT date_accepted FROM cdash_doc WHERE doc_item_id=?",
                (doc_item_id,),
            ).fetchone()
            if existing and (
                existing["date_accepted"] is None
                or capture_date < existing["date_accepted"]
            ):
                self._con.execute(
                    "UPDATE cdash_doc SET date_accepted=? WHERE doc_item_id=?",
                    (capture_date, doc_item_id),
                )
        self._con.commit()

    def set_doc_ready(self, doc_item_id: int, ready: bool, notes: str = ""):
        self._con.execute(
            "UPDATE cdash_doc SET ready=?, notes=? WHERE doc_item_id=?",
            (int(ready), notes, doc_item_id),
        )
        self._con.commit()

    def renumber_doc_pages(self, doc_item_id: int):
        """Reassign page_num 1..N in filename-alphabetical order."""
        rows = self._con.execute(
            "SELECT media_id FROM cdash_media WHERE doc_item_id=? ORDER BY filename",
            (doc_item_id,),
        ).fetchall()
        for i, row in enumerate(rows, start=1):
            self._con.execute(
                "UPDATE cdash_media SET page_num=? WHERE media_id=?",
                (i, row["media_id"]),
            )
        self._con.execute(
            "UPDATE cdash_doc SET num_pages=? WHERE doc_item_id=?",
            (len(rows), doc_item_id),
        )
        self._con.commit()

    # ------------------------------------------------------------------ media

    def insert_media(self, doc_item_id: Optional[int], item_set_id: int,
                     filename: str, filepath: str, batch_media_id: str = None,
                     page_num: int = 0,
                     capture_date: str = None, file_size_mb: float = None,
                     pixel_width: int = None, pixel_height: int = None,
                            format_note: str = None, repair_issues: str = "",
                     ready: bool = False, notes: str = "") -> int:
        cur = self._con.execute(
            """INSERT INTO cdash_media
               (doc_item_id, item_set_id, filename, batch_media_id, filepath,
                page_num, capture_date, file_size_mb, pixel_width, pixel_height,
                     format_note, repair_issues, ready, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_item_id, item_set_id, filename, batch_media_id, filepath,
                 page_num, capture_date, file_size_mb, pixel_width, pixel_height,
                 format_note, repair_issues, int(ready), notes),
        )
        self._con.commit()
        return cur.lastrowid

    def get_media(self, media_id: int) -> Optional[dict]:
        return self._con.execute(
            "SELECT * FROM cdash_media WHERE media_id=?", (media_id,)
        ).fetchone()

    def get_media_for_folder(self, item_set_id: int) -> list:
        return self._con.execute(
            """SELECT m.*, d.doc_type_code
               FROM cdash_media m
               LEFT JOIN cdash_doc d ON m.doc_item_id = d.doc_item_id
               WHERE m.item_set_id=?
               ORDER BY m.filename""",
            (item_set_id,),
        ).fetchall()

    def update_media_prescreener_props(self, media_id: int,
                                       file_size_mb: float,
                                       pixel_width: int,
                                       pixel_height: int,
                                       format_note: str,
                                       capture_date: str,
                                       date_source: str = None,
                                       repair_issues: str = ""):
        self._con.execute(
            """UPDATE cdash_media
               SET file_size_mb=?, pixel_width=?, pixel_height=?,
                   format_note=?, capture_date=?, date_source=?,
                   repair_issues=?
               WHERE media_id=?""",
            (file_size_mb, pixel_width, pixel_height,
             format_note, capture_date, date_source, repair_issues, media_id),
        )
        self._con.commit()

    def set_media_status(self, media_id: int, ready: bool, notes: str = ""):
        self._con.execute(
            "UPDATE cdash_media SET ready=?, notes=? WHERE media_id=?",
            (int(ready), notes, media_id),
        )
        self._con.commit()

    def update_media_filename(self, media_id: int, filename: str, filepath: str):
        self._con.execute(
            "UPDATE cdash_media SET filename=?, filepath=? WHERE media_id=?",
            (filename, filepath, media_id),
        )
        self._con.commit()

    def assign_media_to_doc(self, media_id: int, doc_item_id: int, page_num: int,
                            batch_media_id: str = None):
        self._con.execute(
            """UPDATE cdash_media
               SET doc_item_id=?, page_num=?, batch_media_id=?
               WHERE media_id=?""",
            (doc_item_id, page_num, batch_media_id, media_id),
        )
        self._con.commit()

    # --------------------------------------------------------------- rejects

    def count_not_ready_media(self) -> int:
        """Count media files with ready=False across all folders."""
        row = self._con.execute(
            "SELECT COUNT(*) AS n FROM cdash_media WHERE ready=0"
        ).fetchone()
        return row["n"] if row else 0

    # ------------------------------------------------------------------ close

    def close(self):
        self._con.close()
