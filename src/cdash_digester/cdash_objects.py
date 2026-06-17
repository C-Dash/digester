"""
CDASH Objects Module  (cdash_objects.py)

Defines the domain constants, name parsers, and the BatchDB persistence facade.

Layout
------
  DOC_TYPES        — fixed document-type code → description mapping
  slugify()        — filesystem-safe name normaliser
  parse_batch_name()   \
  parse_folder_name()   > name parsers used by the digester
  parse_media_name()   /
  BatchDB          — thin facade over db.Database + the repository classes;
                     preserves the historical single-object CRUD API.
"""

import re
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
# BatchDB  — facade over the db package (Database + repositories)
# ---------------------------------------------------------------------------

class BatchDB:
    """Single per-batch data-access object.

    Historically this class held every SQL statement directly. It is now a thin
    facade: it owns a `db.Database` (connection/schema/migrations) and one
    repository per aggregate, and delegates each method to the relevant repo.
    The public API (method names, signatures, `_con`, `db_path`) is unchanged so
    existing callers (digester, prescreener, GUI) need no changes.

    Boolean status columns (ready, name_ready, media_ready, accepted) are stored
    as INTEGER 0/1 and surfaced as Python bools via the row factory.
    """

    def __init__(self, db_path: Path):
        # Local import avoids an import cycle (db.repositories imports this
        # module's constants), since the db package is only needed at runtime.
        from .db import (
            Database, BatchRepo, FolderRepo, PlaceRepo,
            DocRepo, MediaRepo, CacheRepo,
        )
        self.db_path = db_path
        self._db = Database(db_path)
        self._con = self._db.con           # kept public for direct-SQL callers
        self._batch = BatchRepo(self._con)
        self._folders = FolderRepo(self._con)
        self._places = PlaceRepo(self._con)
        self._docs = DocRepo(self._con)
        self._media = MediaRepo(self._con)
        self._caches = CacheRepo(self._con)

    # ------------------------------------------------------------ schema/lifecycle

    def create_all_tables(self):
        self._db.create_all_tables()

    def clear_working_tables(self):
        self._db.clear_working_tables()

    def clear_caches(self):
        return self._db.clear_caches()

    def close(self):
        self._db.close()

    # ------------------------------------------------------------------ batch

    def upsert_batch(self, batch_id, name, batch_folder_path,
                     initialized_date, ready=False, note=""):
        self._batch.upsert_batch(batch_id, name, batch_folder_path,
                                 initialized_date, ready, note)

    def get_batch(self):
        return self._batch.get_batch()

    def set_batch_ready(self, ready, note=""):
        self._batch.set_batch_ready(ready, note)

    def set_rejected_count(self, count):
        self._batch.set_rejected_count(count)

    def count_batch_stats(self):
        return self._batch.count_batch_stats()

    def update_batch_counts(self, folders, places, documents,
                            media, rejects, repaired):
        self._batch.update_batch_counts(folders, places, documents,
                                        media, rejects, repaired)

    def recalculate_batch_ready(self):
        return self._batch.recalculate_batch_ready()

    # ------------------------------------------------------------------ folder

    def upsert_folder(self, item_set_id, cdash_folder_name, os_folder_name,
                      batch_folder_id="", name_ready=False,
                      media_ready=False, notes=""):
        self._folders.upsert_folder(item_set_id, cdash_folder_name,
                                    os_folder_name, batch_folder_id,
                                    name_ready, media_ready, notes)

    def get_folders(self):
        return self._folders.get_folders()

    def get_folder_by_item_set(self, item_set_id):
        return self._folders.get_folder_by_item_set(item_set_id)

    def set_folder_name_ready(self, item_set_id, ready, notes=""):
        self._folders.set_folder_name_ready(item_set_id, ready, notes)

    def assign_folder_index(self, item_set_id, index):
        self._folders.assign_folder_index(item_set_id, index)

    def recalculate_folder_status(self, item_set_id):
        self._folders.recalculate_folder_status(item_set_id)

    # ------------------------------------------------------------------ place

    def upsert_place(self, place_item_id, place_name, place_type=None,
                     house_num=None, street_name=None, street_sort=None,
                     neighborhood=None, chc_dist=None, item_set_ids=None,
                     lat=None, lon=None, ready=True, notes=""):
        self._places.upsert_place(place_item_id, place_name, place_type,
                                  house_num, street_name, street_sort,
                                  neighborhood, chc_dist, item_set_ids,
                                  lat, lon, ready, notes)

    def get_place(self, place_item_id):
        return self._places.get_place(place_item_id)

    # ----------------------------------------------------- persistent caches

    def get_folder_cache(self, item_set_id):
        return self._caches.get_folder_cache(item_set_id)

    def upsert_folder_cache(self, item_set_id, cdash_folder_name,
                            folder_index, status="valid"):
        self._caches.upsert_folder_cache(item_set_id, cdash_folder_name,
                                         folder_index, status)

    def max_folder_cache_index(self):
        return self._caches.max_folder_cache_index()

    def get_place_cache(self, place_item_id):
        return self._caches.get_place_cache(place_item_id)

    def upsert_place_cache(self, place_item_id, props, status="valid"):
        self._caches.upsert_place_cache(place_item_id, props, status)

    def get_file_cache(self, filepath):
        return self._caches.get_file_cache(filepath)

    def upsert_file_cache(self, filepath, file_size_bytes, mtime_ns,
                          accepted, props):
        self._caches.upsert_file_cache(filepath, file_size_bytes, mtime_ns,
                                       accepted, props)

    def update_file_cache_path(self, old_path, new_path):
        self._caches.update_file_cache_path(old_path, new_path)

    # -------------------------------------------------------------------- doc

    def insert_doc(self, place_item_id, item_set_id, folder_doc_sequence,
                   doc_type_code, doc_title, batch_doc_id,
                   date_accepted=None, ready=False, notes=""):
        return self._docs.insert_doc(place_item_id, item_set_id,
                                     folder_doc_sequence, doc_type_code,
                                     doc_title, batch_doc_id, date_accepted,
                                     ready, notes)

    def get_doc(self, doc_item_id):
        return self._docs.get_doc(doc_item_id)

    def get_docs_for_folder(self, item_set_id):
        return self._docs.get_docs_for_folder(item_set_id)

    def increment_doc_pages(self, doc_item_id, capture_date=None, count=1):
        self._docs.increment_doc_pages(doc_item_id, capture_date, count)

    def set_doc_ready(self, doc_item_id, ready, notes=""):
        self._docs.set_doc_ready(doc_item_id, ready, notes)

    def renumber_doc_pages(self, doc_item_id):
        self._docs.renumber_doc_pages(doc_item_id)

    # ------------------------------------------------------------------ media

    def insert_media(self, doc_item_id, item_set_id, filename, filepath,
                     batch_media_id=None, page_num=0, capture_date=None,
                     file_size_mb=None, pixel_width=None, pixel_height=None,
                     format_note=None, repair_issues="", ready=False, notes=""):
        return self._media.insert_media(doc_item_id, item_set_id, filename,
                                        filepath, batch_media_id, page_num,
                                        capture_date, file_size_mb, pixel_width,
                                        pixel_height, format_note, repair_issues,
                                        ready, notes)

    def get_media(self, media_id):
        return self._media.get_media(media_id)

    def get_media_for_folder(self, item_set_id):
        return self._media.get_media_for_folder(item_set_id)

    def update_media_prescreener_props(self, media_id, file_size_mb,
                                       pixel_width, pixel_height, format_note,
                                       capture_date, date_source=None,
                                       repair_issues=""):
        self._media.update_media_prescreener_props(
            media_id, file_size_mb, pixel_width, pixel_height, format_note,
            capture_date, date_source, repair_issues)

    def set_media_status(self, media_id, ready, notes=""):
        self._media.set_media_status(media_id, ready, notes)

    def update_media_filename(self, media_id, filename, filepath):
        self._media.update_media_filename(media_id, filename, filepath)

    def assign_media_to_doc(self, media_id, doc_item_id, page_num,
                            batch_media_id=None):
        self._media.assign_media_to_doc(media_id, doc_item_id, page_num,
                                        batch_media_id)

    def count_not_ready_media(self):
        return self._media.count_not_ready_media()
