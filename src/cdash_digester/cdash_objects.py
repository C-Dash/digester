"""
CDASH Objects Module  (cdash_objects.py)

Defines BatchDB, the per-batch persistence facade over the db package.

Domain constants live in constants.py and the name parsers in naming.py —
both leaf modules — so db/repositories.py can import them without importing
this module. They are re-exported here for backwards compatibility with
existing callers.
"""

from pathlib import Path

from .constants import DOC_TYPES, PLACE_PROP_KEYS          # noqa: F401  (re-export)
from .naming import (                                       # noqa: F401  (re-export)
    slugify, parse_batch_name, parse_folder_name, parse_media_name,
)
from .db import (
    Database, BatchRepo, FolderRepo, PlaceRepo, DocRepo, MediaRepo, CacheRepo,
    ExportRepo,
)


# ---------------------------------------------------------------------------
# BatchDB  — facade over the db package (Database + repositories)
# ---------------------------------------------------------------------------

class BatchDB:
    """Single per-batch data-access object.

    Historically this class held every SQL statement directly. It is now a thin
    facade: it owns a `db.Database` (connection/schema/migrations) and one
    repository per aggregate, and delegates each method to the relevant repo.

    `_con` is private and stays that way. Services used to reach through it to
    run their own SQL (four export joins, two cascade deletes); that SQL now
    lives in ExportRepo and MediaRepo. If a caller needs a query this facade
    doesn't expose, add a repo method — don't reach for the connection.

    Boolean status columns (ready, name_ready, media_ready, accepted) are stored
    as INTEGER 0/1 and surfaced as Python bools via the row factory.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db = Database(db_path)
        self._con = self._db.con
        self._batch = BatchRepo(self._con)
        self._folders = FolderRepo(self._con)
        self._places = PlaceRepo(self._con)
        self._docs = DocRepo(self._con)
        self._media = MediaRepo(self._con)
        self._caches = CacheRepo(self._con)
        self._export = ExportRepo(self._con)

    # ----------------------------------------------------------------- export

    def folders_for_export(self):
        return self._export.folders_for_export()

    def places_for_export(self):
        return self._export.places_for_export()

    def docs_for_export(self):
        return self._export.docs_for_export()

    def media_for_export(self):
        return self._export.media_for_export()

    def delete_folder_records(self, item_set_id):
        self._media.delete_folder_records(item_set_id)

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

    def set_folder_cache_index(self, item_set_id, folder_index):
        self._caches.set_folder_cache_index(item_set_id, folder_index)

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

    def next_doc_index(self, item_set_id):
        return self._docs.next_doc_index(item_set_id)

    def get_doc(self, doc_item_id):
        return self._docs.get_doc(doc_item_id)

    def get_docs_for_folder(self, item_set_id):
        return self._docs.get_docs_for_folder(item_set_id)

    def increment_doc_pages(self, doc_item_id, capture_date=None, count=1):
        self._docs.increment_doc_pages(doc_item_id, capture_date, count)

    def renumber_doc_pages(self, doc_item_id):
        self._docs.renumber_doc_pages(doc_item_id)

    # ------------------------------------------------------------------ media

    def insert_media(self, doc_item_id, item_set_id, filename, filepath,
                     batch_media_id=None, page_num=0, capture_date=None,
                     file_size_mb=None, pixel_width=None, pixel_height=None,
                     format=None, format_issues="", repair_issues="",
                     ready=False, filename_issues="", name_ready=False):
        return self._media.insert_media(doc_item_id, item_set_id, filename,
                                        filepath, batch_media_id, page_num,
                                        capture_date, file_size_mb, pixel_width,
                                        pixel_height, format, format_issues,
                                        repair_issues, ready, filename_issues,
                                        name_ready)

    def get_media(self, media_id):
        return self._media.get_media(media_id)

    def get_media_for_folder(self, item_set_id):
        return self._media.get_media_for_folder(item_set_id)

    def set_media_status(self, media_id, ready, filename_issues=""):
        self._media.set_media_status(media_id, ready, filename_issues)

    def update_media_filename(self, media_id, filename, filepath):
        self._media.update_media_filename(media_id, filename, filepath)

    def assign_media_to_doc(self, media_id, doc_item_id, page_num,
                            batch_media_id=None):
        self._media.assign_media_to_doc(media_id, doc_item_id, page_num,
                                        batch_media_id)

    def count_not_ready_media(self):
        return self._media.count_not_ready_media()
