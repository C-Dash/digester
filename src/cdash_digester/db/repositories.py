"""Per-aggregate repositories.

Each repo wraps the shared SQLite connection and holds the SQL for one entity.
The bodies are moved verbatim from the former monolithic BatchDB so behavior is
identical; BatchDB now delegates to these.
"""

import sqlite3
from datetime import datetime
from typing import Optional

from ..cdash_objects import DOC_TYPES, PLACE_PROP_KEYS
from ..models import Batch, Folder, Place, Doc, Media


class _Repo:
    def __init__(self, con: sqlite3.Connection):
        self._con = con


# --------------------------------------------------------------------- batch

class BatchRepo(_Repo):

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

    def get_batch(self) -> Optional[Batch]:
        return Batch.from_row(
            self._con.execute("SELECT * FROM cdash_batch LIMIT 1").fetchone()
        )

    def set_batch_ready(self, ready: bool, note: str = ""):
        self._con.execute(
            "UPDATE cdash_batch SET ready=?, note=?", (int(ready), note)
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


# -------------------------------------------------------------------- folder

class FolderRepo(_Repo):

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
        return [Folder.from_row(r) for r in self._con.execute(
            "SELECT * FROM cdash_folder ORDER BY folder_number"
        ).fetchall()]

    def get_folder_by_item_set(self, item_set_id: int) -> Optional[Folder]:
        return Folder.from_row(self._con.execute(
            "SELECT * FROM cdash_folder WHERE item_set_id=?", (item_set_id,)
        ).fetchone())

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


# --------------------------------------------------------------------- place

class PlaceRepo(_Repo):

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

    def get_place(self, place_item_id: int) -> Optional[Place]:
        return Place.from_row(self._con.execute(
            "SELECT * FROM cdash_place WHERE place_item_id=?", (place_item_id,)
        ).fetchone())


# ----------------------------------------------------------------------- doc

class DocRepo(_Repo):

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

    def get_doc(self, doc_item_id: int) -> Optional[Doc]:
        return Doc.from_row(self._con.execute(
            "SELECT * FROM cdash_doc WHERE doc_item_id=?", (doc_item_id,)
        ).fetchone())

    def get_docs_for_folder(self, item_set_id: int) -> list:
        return [Doc.from_row(r) for r in self._con.execute(
            """SELECT * FROM cdash_doc WHERE item_set_id=?
               ORDER BY folder_doc_sequence""",
            (item_set_id,),
        ).fetchall()]

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


# --------------------------------------------------------------------- media

class MediaRepo(_Repo):

    def insert_media(self, doc_item_id: Optional[int], item_set_id: int,
                     filename: str, filepath: str, batch_media_id: str = None,
                     page_num: int = 0,
                     capture_date: str = None, file_size_mb: float = None,
                     pixel_width: int = None, pixel_height: int = None,
                     format: str = None, format_issues: str = "",
                     repair_issues: str = "",
                     ready: bool = False, filename_issues: str = "") -> int:
        cur = self._con.execute(
            """INSERT INTO cdash_media
               (doc_item_id, item_set_id, filename, batch_media_id, filepath,
                page_num, capture_date, file_size_mb, pixel_width, pixel_height,
                     format, format_issues, repair_issues, ready, filename_issues)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_item_id, item_set_id, filename, batch_media_id, filepath,
                 page_num, capture_date, file_size_mb, pixel_width, pixel_height,
                 format, format_issues, repair_issues, int(ready), filename_issues),
        )
        self._con.commit()
        return cur.lastrowid

    def get_media(self, media_id: int) -> Optional[Media]:
        return Media.from_row(self._con.execute(
            "SELECT * FROM cdash_media WHERE media_id=?", (media_id,)
        ).fetchone())

    def get_media_for_folder(self, item_set_id: int) -> list:
        return self._con.execute(
            """SELECT m.*, d.doc_type_code, d.num_pages
               FROM cdash_media m
               LEFT JOIN cdash_doc d ON m.doc_item_id = d.doc_item_id
               WHERE m.item_set_id=?
               ORDER BY m.filename""",
            (item_set_id,),
        ).fetchall()

    def set_media_status(self, media_id: int, ready: bool, filename_issues: str = ""):
        self._con.execute(
            "UPDATE cdash_media SET ready=?, filename_issues=? WHERE media_id=?",
            (int(ready), filename_issues, media_id),
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

    def count_not_ready_media(self) -> int:
        """Count media files with ready=False across all folders."""
        row = self._con.execute(
            "SELECT COUNT(*) AS n FROM cdash_media WHERE ready=0"
        ).fetchone()
        return row["n"] if row else 0


# ------------------------------------------------------- persistent caches

class CacheRepo(_Repo):

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
                    format_issues, repair_issues, pdf_pages, fetched_date)
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
                   format_issues=excluded.format_issues,
                   repair_issues=excluded.repair_issues,
                   pdf_pages=excluded.pdf_pages,
                   fetched_date=excluded.fetched_date""",
            (filepath, file_size_bytes, mtime_ns, int(accepted),
             props.get("file_size_mb"), props.get("pixel_width"),
             props.get("pixel_height"), props.get("format"),
             props.get("capture_date"), props.get("date_source"),
             "|".join(props.get("format_issues", [])),
             ", ".join(props.get("repair_issues", [])),
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
