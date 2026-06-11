"""
CDASH Digester Module  (digester.py)

The Digester is the main controller.  It:
  - validates and initialises the batch folder structure
  - orchestrates the folder and media scan pipeline
  - provides helpers for interactive metadata assignment
  - exports the four CSV output files

The GUI runs Digester methods on a QThread worker so the UI stays
responsive.  The log callback is the only coupling to the UI.

CLI test harness
----------------
  python -m cdash_digester.digester
Copies CDB260320-Test_batch to a temp folder, scans it, and prints the
status summary.  The GUI is not launched.
"""

import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .cdash_objects import (
    BatchDB, DOC_TYPES, ACCEPTED_SUFFIXES,
    parse_batch_name, parse_folder_name, parse_media_name, slugify,
)
from .prescreener import screen_file
from .repair_media import parse_repair_issues, repair_file
from .validator import CDASHValidator


class Digester:
    """Orchestrates batch initialisation, scanning, and CSV export."""

    def __init__(self, batch_path: Path,
                 log: Callable[[str, str], None] = None):
        self.batch_path = Path(batch_path)
        self.log = log or (lambda msg, lvl: None)
        self.db: Optional[BatchDB] = None
        self._validator = CDASHValidator()

    # ------------------------------------------------------------------ paths

    @property
    def catalog_path(self) -> Path:
        return self.batch_path / "catalog"

    @property
    def media_path(self) -> Path:
        return self.batch_path / "media"

    @property
    def db_path(self) -> Path:
        return self.catalog_path / "batch_db.sqlite"

    # ------------------------------------------------------------------ init

    def load_or_initialize(self):
        """Validate batch structure; open an existing DB or create a fresh one.

        Does NOT scan media — that is a separate step (scan_batch).
        Logs errors and returns without setting self.db if the folder is invalid.
        """
        parsed = parse_batch_name(self.batch_path.name)
        if not parsed:
            self.log(
                f"Invalid batch folder name: '{self.batch_path.name}' "
                f"(expected CDB<YYMMDD>-<name>)",
                "error",
            )
            return

        if not self.media_path.is_dir():
            self.log("No 'media' sub-folder found — cannot open batch.", "error")
            return

        # Create Catalog directory; stamp the existing log if one is present
        if not self.catalog_path.exists():
            self.catalog_path.mkdir()
            self.log("Created catalog folder.", "info")
        else:
            log_file = self.catalog_path / "batch.log"
            if log_file.exists():
                with open(log_file, "a", encoding="utf-8") as f:
                    ts = datetime.now().isoformat(timespec="seconds")
                    f.write(f"\n{'='*60}\nSession opened {ts}\n{'='*60}\n")

        # Open or create the database
        self.db = BatchDB(self.db_path)
        self.db.create_all_tables()

        if not self.db.get_batch():
            self.db.upsert_batch(
                batch_id=parsed["batch_id"],
                name=parsed["name"],
                batch_folder_path=str(self.batch_path),
                initialized_date=datetime.now().date().isoformat(),
            )
            self.log(f"Batch {parsed['batch_id']} initialised.", "info")
        else:
            not_ready = self.db.count_not_ready_media()
            self.log(
                f"Batch {parsed['batch_id']} opened "
                f"({not_ready} not-ready file(s) on record).",
                "info",
            )

    # ------------------------------------------------------------------ scan

    def scan_batch(self):
        """Full batch scan: rebuild DB from scratch, scan all folders and media."""
        if self.db is None:
            self.log("No open batch — call load_or_initialize first.", "error")
            return

        parsed = parse_batch_name(self.batch_path.name)
        if not parsed:
            return

        # Rebuild the database
        self.db.close()
        if self.db_path.exists():
            self.db_path.unlink()
        self.db = BatchDB(self.db_path)
        self.db.create_all_tables()
        self.db.upsert_batch(
            batch_id=parsed["batch_id"],
            name=parsed["name"],
            batch_folder_path=str(self.batch_path),
            initialized_date=datetime.now().date().isoformat(),
        )
        self.log(f"Scanning batch {parsed['batch_id']} …", "info")

        folder_dirs = sorted(
            p for p in self.media_path.iterdir() if p.is_dir()
        )
        for idx, folder_dir in enumerate(folder_dirs, start=1):
            self._scan_folder(folder_dir, folder_index=idx,
                              batch_id=parsed["batch_id"])

        self.db.recalculate_batch_ready()
        counts = self._collect_and_store_counts()
        batch = self.db.get_batch()
        self.log(
            f"Scan complete — ready: {batch['ready']}  "
            + self._counts_summary(counts),
            "info",
        )

    def validate_folder(self, item_set_id: int):
        """Re-scan a single folder (Folder → Scan Selected Folder)."""
        if self.db is None:
            return
        folder_row = self.db.get_folder_by_item_set(item_set_id)
        if not folder_row:
            self.log(f"Folder {item_set_id} not found in DB.", "error")
            return
        folder_dir = self.media_path / folder_row["os_folder_name"]
        if not folder_dir.is_dir():
            self.log(f"Folder not found on disk: {folder_dir}", "error")
            return

        self._delete_folder_records(item_set_id)
        self._scan_media_in_folder(
            folder_dir, item_set_id,
            batch_folder_id=folder_row.get("batch_folder_id") or "",
        )
        self.db.recalculate_batch_ready()
        self.log(f"Re-scanned: {folder_row['os_folder_name']}", "info")

    def _delete_folder_records(self, item_set_id: int):
        """Remove media and doc records for one folder before re-scanning."""
        con = self.db._con
        con.execute("DELETE FROM cdash_media WHERE item_set_id=?", (item_set_id,))
        con.execute("DELETE FROM cdash_doc   WHERE item_set_id=?", (item_set_id,))
        con.commit()

    # -------------------------------------------------- folder scan internals

    def _scan_folder(self, folder_dir: Path, folder_index: int, batch_id: str):
        self.log(f"Folder: {folder_dir.name}", "info")

        parsed = parse_folder_name(folder_dir.name)
        if not parsed:
            self.log(
                f"  Cannot parse folder name — skipping: {folder_dir.name}",
                "warning",
            )
            return

        item_set_id = parsed["item_set_id"]
        batch_folder_id = f"{batch_id}F{folder_index}"

        # Validate item_set_id via API
        api_status, api_name = self._validator.validate_folder(item_set_id)
        if "Valid" in api_status:
            cdash_folder_name = api_name
            name_ready = True
            self.log(f"  Validated folder: {api_name}", "info")
        else:
            self.log(f"  Folder ID {item_set_id}: {api_status}", "warning")
            cdash_folder_name = parsed["slug"]
            name_ready = False

        # Rename folder to validated, identified form if necessary
        canonical = f"F{folder_index}-{slugify(cdash_folder_name)}-OF{item_set_id}"
        if folder_dir.name != canonical:
            new_path = folder_dir.parent / canonical
            try:
                folder_dir.rename(new_path)
                self.log(f"  Renamed -> {canonical}", "info")
                folder_dir = new_path
            except OSError as exc:
                self.log(f"  Rename failed: {exc}", "error")

        self.db.upsert_folder(
            item_set_id=item_set_id,
            cdash_folder_name=cdash_folder_name,
            os_folder_name=folder_dir.name,
            batch_folder_id=batch_folder_id,
            name_ready=name_ready,
        )
        self.db.assign_folder_index(item_set_id, folder_index)

        self._scan_media_in_folder(folder_dir, item_set_id, batch_folder_id)

    def _scan_media_in_folder(self, folder_dir: Path,
                               item_set_id: int, batch_folder_id: str):
        """Screen and register every media file in one folder.

        Format-rejected files stay in place; their issues are recorded in
        cdash_media.notes and they continue through name parsing and place
        validation so all problems are captured in a single scan.
        """
        media_files = sorted(
            f for f in folder_dir.iterdir()
            if f.is_file() and f.suffix.lower() in ACCEPTED_SUFFIXES
        )

        # doc_index → {"doc_item_id", "place_slug", "doc_type", "page_count"}
        doc_tracker: dict = {}
        slug_place_tracker: dict = {}   # place_slug → place_id
        doc_seq = 0  # folder_doc_sequence counter

        for filepath in media_files:
            self.log(f"  {filepath.name}", "info")
            notes_parts: list = []
            repair_issues = ""

            # 1. Format screening
            accepted, props = screen_file(filepath)
            repair_issues = ", ".join(props.get("repair_issues", []))
            if not accepted:
                notes_parts.append(props.get("qa_note", "Format rejected"))
                self.log(f"    Format issue: {props.get('qa_note')}", "warning")

            # 2. Name parsing
            parsed = parse_media_name(filepath.stem)
            rel_path = str(filepath.relative_to(self.batch_path))

            if not parsed:
                notes_parts.append("Name not in ready format")
                self.db.insert_media(
                    doc_item_id=None,
                    item_set_id=item_set_id,
                    filename=filepath.name,
                    filepath=rel_path,
                    capture_date=props.get("capture_date"),
                    file_size_mb=props.get("file_size_mb"),
                    pixel_width=props.get("pixel_width"),
                    pixel_height=props.get("pixel_height"),
                    format_note=props.get("format"),
                    repair_issues=repair_issues,
                    ready=False,
                    notes=", ".join(notes_parts),
                )
                self.log("    Not-ready name.", "info")
                continue

            place_slug = parsed["place_slug"]
            doc_index = parsed["doc_index"]
            doc_type = parsed["doc_type"]
            place_id = parsed["place_id"]

            
            if doc_index in doc_tracker and (place_slug == doc_tracker[doc_index]["place_slug"] and place_id == None):
                # same document no place_id
                place_id = doc_tracker[doc_index]["place_id"]
                doc_type = doc_tracker[doc_index]["doc_type"]
            elif place_id is None and place_slug in slug_place_tracker:
                # same slug seen before — inherit place_id only, not doc_type
                place_id = slug_place_tracker[place_slug]


            # 3. Place validation
            place_name = None
            if place_id is not None:
                cached = self.db.get_place(place_id)
                if cached:
                    place_name = cached["place_name"]
                else:
                    p_status, p_props = self._validator.validate_place(place_id)
                    if "Valid" in p_status and p_props:
                        place_name = p_props.get("place_name") or parsed["place_slug"]
                        self.db.upsert_place(
                            place_item_id=place_id,
                            place_name=place_name,
                            place_type=p_props.get("place_type"),
                            house_num=p_props.get("house_num"),
                            street_name=p_props.get("street_name"),
                            street_sort=p_props.get("street_sort"),
                            neighborhood=p_props.get("neighborhood"),
                            chc_dist=p_props.get("chc_dist"),
                            item_set_ids=p_props.get("item_set_ids"),
                            lat=p_props.get("lat"),
                            lon=p_props.get("lon"),
                        )
                    else:
                        self.log(f"    Place {place_id}: {p_status}", "error")
                        notes_parts.append(f"Place {place_id}: {p_status}")
                        place_name = parsed["place_slug"]


            if place_id is None:
                notes_parts.append("No place ID in filename")
            else:
                slug_place_tracker[place_slug] = place_id

            media_ready = not notes_parts

            # 4. Doc tracking / creation
            if doc_index not in doc_tracker:  # New Document
                doc_seq += 1
                batch_doc_id = (
                    f"{batch_folder_id}-{parsed['place_slug']}-{doc_seq:04d}-{doc_type}"
                )
                doc_title = (
                    f"{place_name or parsed['place_slug']} — "
                    f"{DOC_TYPES.get(doc_type, doc_type)}"
                )
                doc_item_id = self.db.insert_doc(
                    place_item_id=place_id,
                    item_set_id=item_set_id,
                    folder_doc_sequence=doc_seq,
                    doc_type_code=doc_type,
                    doc_title=doc_title,
                    batch_doc_id=batch_doc_id,
                    date_accepted=props.get("capture_date"),
                    ready=media_ready,
                )
                doc_tracker[doc_index] = {
                    "doc_item_id":  doc_item_id,
                    "place_slug":   parsed["place_slug"],
                    "doc_type":     doc_type,
                    "doc_seq":      doc_seq,
                    "place_id":     place_id,
                    "page_count":   0,
                }
            else:
                doc_item_id = doc_tracker[doc_index]["doc_item_id"]
                if parsed["place_slug"] != doc_tracker[doc_index]["place_slug"]:
                    msg = (
                        f"doc_index {doc_index:04d} conflicts with existing "
                        f"place name — skipped."
                    )
                    self.log(f"    ERROR: {msg}", "error")
                    self.db.insert_media(
                        doc_item_id=None,
                        item_set_id=item_set_id,
                        filename=filepath.name,
                        filepath=rel_path,
                        repair_issues=repair_issues,
                        ready=False,
                        notes=msg,
                    )
                    continue

            # 5. Page number
            doc_tracker[doc_index]["page_count"] += 1
            page_num = doc_tracker[doc_index]["page_count"]
            entry = doc_tracker[doc_index]
            batch_media_id = (
                f"{batch_folder_id}-{entry['place_slug']}"
                f"_{entry['doc_seq']:04d}p{page_num:04d}-{entry['doc_type']}"
            )
            self.db.increment_doc_pages(
                doc_item_id,
                props.get("capture_date"),
                count=props.get("pdf_pages") or 1,
            )

            # 6. Rename file only when format is ok and place is fully known
            if place_id is not None and place_name:
                new_stem = (
                    f"{slugify(place_name)}_{doc_index:04d}p{page_num:04d}"
                    f"-{doc_type}-OP{place_id}"
                )
                new_name = f"{new_stem}{filepath.suffix.lower()}"
                if new_name != filepath.name:
                    new_path = filepath.parent / new_name
                    try:
                        filepath.rename(new_path)
                        filepath = new_path
                        rel_path = str(filepath.relative_to(self.batch_path))
                        self.log(f"    -> {new_name}", "info")
                    except OSError as exc:
                        self.log(f"    Rename failed: {exc}", "error")
                        notes_parts.append(f"Rename failed: {exc}")

            # 7. Register media
            self.db.insert_media(
                doc_item_id=doc_item_id,
                item_set_id=item_set_id,
                filename=filepath.name,
                filepath=rel_path,
                batch_media_id=batch_media_id,
                page_num=page_num,
                capture_date=props.get("capture_date"),
                file_size_mb=props.get("file_size_mb"),
                pixel_width=props.get("pixel_width"),
                pixel_height=props.get("pixel_height"),
                format_note=props.get("format"),
                repair_issues=repair_issues,
                ready=media_ready,
                notes=", ".join(notes_parts),
            )

        self.db.recalculate_folder_status(item_set_id)

    # ---------------------------------------------------------- place helper

    def validate_place(self, place_id: int) -> Tuple[str, str]:
        """Validate a place ID, register in DB if valid.
        Returns (status, place_name).
        """
        if self.db:
            cached = self.db.get_place(place_id)
            if cached:
                return "Valid CDASH place (cached)", cached["place_name"]

        status, props = self._validator.validate_place(place_id)
        if "Valid" in status and props and self.db:
            place_name = props.get("place_name", "")
            self.db.upsert_place(
                place_item_id=place_id,
                place_name=place_name,
                place_type=props.get("place_type"),
                house_num=props.get("house_num"),
                street_name=props.get("street_name"),
                street_sort=props.get("street_sort"),
                neighborhood=props.get("neighborhood"),
                chc_dist=props.get("chc_dist"),
                item_set_ids=props.get("item_set_ids"),
                lat=props.get("lat"),
                lon=props.get("lon"),
            )
            return status, place_name
        return status, ""

    # -------------------------------------------------- interactive assign

    def _current_doc_type(self, media_id: int) -> Optional[str]:
        """Return the doc_type_code from the existing doc for this media file."""
        row = self.db.get_media(media_id)
        if not row or not row.get("doc_item_id"):
            return None
        doc = self.db.get_doc(row["doc_item_id"])
        return doc["doc_type_code"] if doc else None

    def _current_place_info(self, media_id: int) -> Tuple[Optional[int], Optional[str]]:
        """Return (place_id, place_name) from the existing doc for this media file."""
        row = self.db.get_media(media_id)
        if not row or not row.get("doc_item_id"):
            return None, None
        doc = self.db.get_doc(row["doc_item_id"])
        if not doc or not doc.get("place_item_id"):
            return None, None
        place_row = self.db.get_place(doc["place_item_id"])
        place_name = place_row["place_name"] if place_row else None
        return doc["place_item_id"], place_name

    def assign_media_to_doc(self, media_ids: List[int], place_id: int,
                            doc_type_code: str, is_multi_page: bool) -> bool:
        """Assign selected media files to a new document.

        is_multi_page=True  → all files become pages of one document.
        is_multi_page=False → each file becomes a separate single-page document.
        Returns True on success.
        """
        if not self.db:
            return False

        place_no_change = place_id is None
        type_no_change  = doc_type_code is None

        # Validate place when a new place_id was provided.
        if not place_no_change:
            p_status, place_name = self.validate_place(place_id)
            if "Valid" not in p_status:
                self.log(f"Place validation failed: {p_status}", "error")
                return False
            place_slug = slugify(place_name)
        else:
            place_name = None   # resolved per-file (or from first file for multi-page)
            place_slug = None

        first = self.db.get_media(media_ids[0])
        if not first:
            return False
        item_set_id = first["item_set_id"]

        folder_row = self.db.get_folder_by_item_set(item_set_id)
        batch_folder_id = (folder_row.get("batch_folder_id") or "") if folder_row else ""

        docs = self.db.get_docs_for_folder(item_set_id)
        next_seq = max((d["folder_doc_sequence"] for d in docs), default=0) + 1

        # For multi-page No Change doc_type: use first file's type.
        if type_no_change:
            first_type = self._current_doc_type(media_ids[0])
            if first_type is None:
                self.log(
                    "Cannot determine doc type — first file has no existing document.",
                    "error",
                )
                return False
            doc_type_code = first_type   # overridden per-file in single-page branch

        # For No Change place: resolve from first file; for multi-page set globals now.
        if place_no_change:
            first_place_id, first_place_name = self._current_place_info(media_ids[0])
            if first_place_id is None:
                self.log(
                    "Cannot determine place — first file has no existing place.", "error"
                )
                return False
            if is_multi_page:
                place_id   = first_place_id
                place_name = first_place_name
                place_slug = slugify(first_place_name)

        doc_title = f"{place_name} - {DOC_TYPES.get(doc_type_code, doc_type_code)}"

        try:
            if is_multi_page:
                batch_doc_id = (
                    f"{batch_folder_id}-{place_slug}-{next_seq:04d}-{doc_type_code}"
                )
                doc_id = self.db.insert_doc(
                    place_item_id=place_id,
                    item_set_id=item_set_id,
                    folder_doc_sequence=next_seq,
                    doc_type_code=doc_type_code,
                    doc_title=doc_title,
                    batch_doc_id=batch_doc_id,
                    ready=True,
                )
                for page_num, media_id in enumerate(sorted(media_ids), start=1):
                    batch_media_id = (
                        f"{batch_folder_id}-{place_slug}"
                        f"_{next_seq:04d}p{page_num:04d}-{doc_type_code}"
                    )
                    self.db.assign_media_to_doc(media_id, doc_id, page_num,
                                                batch_media_id)
                    self._rename_media(media_id, place_id, place_slug,
                                       next_seq, page_num, doc_type_code)
                    self.db.set_media_status(media_id, True)
                self.db.renumber_doc_pages(doc_id)
            else:
                for page_num, media_id in enumerate(sorted(media_ids), start=1):
                    # Resolve per-file place when No Change.
                    if place_no_change:
                        eff_place_id, eff_place_name = self._current_place_info(media_id)
                        if eff_place_id is None:
                            self.log(
                                f"Skipping media {media_id}: no existing place.", "warning"
                            )
                            next_seq += 1
                            continue
                        eff_slug = slugify(eff_place_name)
                    else:
                        eff_place_id, eff_place_name, eff_slug = place_id, place_name, place_slug

                    # Resolve per-file doc_type when No Change.
                    effective_type = (
                        self._current_doc_type(media_id) if type_no_change else doc_type_code
                    )
                    if effective_type is None:
                        self.log(
                            f"Skipping media {media_id}: no existing doc type.", "warning"
                        )
                        next_seq += 1
                        continue

                    eff_title    = f"{eff_place_name} — {DOC_TYPES.get(effective_type, effective_type)}"
                    batch_doc_id = f"{batch_folder_id}-{eff_slug}-{next_seq:04d}-{effective_type}"
                    doc_id = self.db.insert_doc(
                        place_item_id=eff_place_id,
                        item_set_id=item_set_id,
                        folder_doc_sequence=next_seq,
                        doc_type_code=effective_type,
                        doc_title=eff_title,
                        batch_doc_id=batch_doc_id,
                        ready=True,
                    )
                    batch_media_id = (
                        f"{batch_folder_id}-{eff_slug}"
                        f"_{next_seq:04d}p0001-{effective_type}"
                    )
                    self.db.assign_media_to_doc(media_id, doc_id, 1, batch_media_id)
                    self._rename_media(media_id, eff_place_id, eff_slug,
                                       next_seq, 1, effective_type)
                    self.db.set_media_status(media_id, True)
                    self.db.renumber_doc_pages(doc_id)
                    next_seq += 1

            self.db.recalculate_folder_status(item_set_id)
            self.db.recalculate_batch_ready()
            return True

        except Exception as exc:
            self.log(f"assign_media_to_doc failed: {exc}", "error")
            return False

    def _rename_media(self, media_id: int, place_id: int, place_slug: str,
                      doc_seq: int, page_num: int, doc_type: str):
        row = self.db.get_media(media_id)
        if not row:
            return
        old_path = self.batch_path / row["filepath"]
        new_name = (
            f"{place_slug}_{doc_seq:04d}p{page_num:04d}"
            f"-{doc_type}-OP{place_id}{old_path.suffix.lower()}"
        )
        new_path = old_path.parent / new_name
        try:
            if old_path != new_path:
                old_path.rename(new_path)
            self.db.update_media_filename(
                media_id, new_name,
                str(new_path.relative_to(self.batch_path)),
            )
        except OSError as exc:
            self.log(f"  Rename failed for {row['filename']}: {exc}", "error")

    def repair_media_files(self, media_ids: List[int]):
        """Repair selected media files that have recorded repair issues.

        Each repaired file is copied back over its original, then every
        affected folder is rescanned so the DB reflects the corrected files.
        """
        if not self.db:
            self.log("No open batch.", "error")
            return

        repaired = 0
        failed = 0
        skipped = 0
        affected_folders: set = set()

        for media_id in media_ids:
            row = self.db.get_media(media_id)
            if not row:
                skipped += 1
                self.log(f"Media ID {media_id} not found.", "warning")
                continue

            issues = parse_repair_issues(row.get("repair_issues"))
            if not issues:
                skipped += 1
                self.log(f"Skipping {row['filename']}: no repair issues.", "info")
                continue

            filepath = self.batch_path / row["filepath"]
            if not filepath.exists():
                failed += 1
                self.log(f"Cannot repair {row['filename']}: file not found.", "error")
                continue

            self.log(f"Repairing {row['filename']} ({', '.join(issues)})…", "info")
            success, message = repair_file(filepath, issues, catalog_path=self.catalog_path)
            if success:
                repaired += 1
                affected_folders.add(row["item_set_id"])
                self.log(f"  {message}", "success")
            else:
                failed += 1
                self.log(f"  {message}", "error")

        self.log(
            f"Repair complete: {repaired} repaired, {failed} failed, {skipped} skipped.",
            "info",
        )
        counts = self._collect_and_store_counts()
        self.log(self._counts_summary(counts), "info")

        for item_set_id in affected_folders:
            self.validate_folder(item_set_id)

    # --------------------------------------------------------------- counts

    def _read_rejects_csv_counts(self) -> tuple:
        """Return (total_rows, repaired_rows) from Catalog/rejects.csv."""
        csv_path = self.catalog_path / "rejects.csv"
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            total    = len(rows)
            repaired = sum(
                1 for r in rows
                if r.get("Repair_Action", "").startswith("Repaired:")
            )
            return total, repaired
        except FileNotFoundError:
            return 0, 0

    def _collect_and_store_counts(self) -> dict:
        """Compute all six counts, persist to DB, and return as a dict."""
        stats = self.db.count_batch_stats()
        rejects, repaired = self._read_rejects_csv_counts()
        stats["rejects"]  = rejects
        stats["repaired"] = repaired
        self.db.update_batch_counts(
            folders=stats["folders"], places=stats["places"],
            documents=stats["documents"], media=stats["media"],
            rejects=rejects, repaired=repaired,
        )
        return stats

    @staticmethod
    def _counts_summary(counts: dict) -> str:
        lines = [
            "\nBatch Summary",
            f"  Folders:   {counts['folders']}",
            f"  Places:    {counts['places']}",
            f"  Documents: {counts['documents']}",
            f"  Media:     {counts['media']}",
            f"  Rejects:   {counts['rejects']}",
            f"  Repaired:  {counts['repaired']}",
        ]
        return "\n".join(lines)

    # ----------------------------------------------------------------- status

    def get_status_summary(self) -> str:
        if not self.db:
            return "No batch open."
        batch = self.db.get_batch()
        if not batch:
            return "No batch record in DB."
        folders = self.db.get_folders()
        n_ready = sum(1 for f in folders if f["name_ready"] and f["media_ready"])
        lines = [
            f"Batch:   {batch['batch_id']}  ready={batch['ready']}",
            f"Folders: {n_ready}/{len(folders)} ready",
            f"Not ready: {batch.get('rejected_count', 0)}",
            "",
        ]
        for f in folders:
            lines.append(
                f"  {f['os_folder_name']:<45s}"
                f"  name={'ok' if f['name_ready'] else 'NO':2s}"
                f"  media={'ok' if f['media_ready'] else 'NO'}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ CSV

    def export_csv(self):
        """Export batch.csv, place.csv, document.csv, media.csv to catalog/."""
        if not self.db:
            self.log("No open batch.", "error")
            return
        batch = self.db.get_batch()
        if not batch or not batch["ready"]:
            self.log("Batch is not Ready — CSV export skipped.", "warning")
            return
        counts = self._collect_and_store_counts()
        batch = self.db.get_batch()
        self._write_batch_csv(batch)
        self._write_folder_csv()
        self._write_place_csv()
        self._write_document_csv()
        self._write_media_csv()
        self.log(
            "CSV files written to catalog/.  " + self._counts_summary(counts),
            "info",
        )

    def _write_batch_csv(self, batch: dict):
        out = self.catalog_path / "batch.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "id", "batch_id", "mnemonic_name", "batch_folder_path",
                "initialized_date", "status", "qa_note",
                "folders", "places", "documents", "media", "rejects", "repaired",
            ])
            w.writeheader()
            w.writerow({
                "id":                batch["id"],
                "batch_id":          batch["batch_id"],
                "mnemonic_name":     batch["name"],
                "batch_folder_path": batch["batch_folder_path"],
                "initialized_date":  batch["initialized_date"],
                "status":            "go" if batch["ready"] else "no-go",
                "qa_note":           batch.get("note", ""),
                "folders":           batch.get("folders_count", 0),
                "places":            batch.get("places_count", 0),
                "documents":         batch.get("documents_count", 0),
                "media":             batch.get("media_count", 0),
                "rejects":           batch.get("rejected_count", 0),
                "repaired":          batch.get("repaired_count", 0),
            })

    def _write_folder_csv(self):
        out = self.catalog_path / "folder.csv"
        rows = self.db._con.execute(
            "SELECT * FROM cdash_folder ORDER BY folder_number"
        ).fetchall()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "ResourceTemplate", "ResourceClass", "folder", "itemSetID",
            ])
            w.writeheader()
            for r in rows:
                w.writerow({
                    "ResourceTemplate": "CDASH Folder",
                    "ResourceClass":    "bibo:Collection",
                    "folder":           r["cdash_folder_name"],
                    "itemSetID":        r["item_set_id"],
                })

    def _write_place_csv(self):
        out = self.catalog_path / "place.csv"
        rows = self.db._con.execute(
            """SELECT p.*,
                      f.cdash_folder_name,
                      f.item_set_id AS folder_item_set_id
               FROM cdash_place p
               LEFT JOIN cdash_doc d ON d.place_item_id = p.place_item_id
               LEFT JOIN cdash_folder f ON f.item_set_id = d.item_set_id
               GROUP BY p.place_item_id"""
        ).fetchall()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "resourceTemplate", "resourceClass", "identifier",
                "placeItem", "placeName", "PlaceItemID", "placeType",
                "lat", "lon", "houseNum", "streetName", "streetSort",
                "Neighborhood", "chcDist", "Folder", "ItemSetID",
            ])
            w.writeheader()
            for p in rows:
                w.writerow({
                    "resourceTemplate": "CDASH Place",
                    "resourceClass":    "dcterms:Location",
                    "identifier":       p["place_item_id"],
                    "placeItem":        p["place_name"],
                    "placeName":        p["place_name"],
                    "PlaceItemID":      p["place_item_id"],
                    "placeType":        p["place_type"],
                    "lat":              p["lat"],
                    "lon":              p["lon"],
                    "houseNum":         p["house_num"],
                    "streetName":       p["street_name"],
                    "streetSort":       p["street_sort"],
                    "Neighborhood":     p["neighborhood"],
                    "chcDist":          p["chc_dist"],
                    "Folder":           p["cdash_folder_name"] or "",
                    "ItemSetID":        p["folder_item_set_id"] or "",
                })

    def _write_document_csv(self):
        out = self.catalog_path / "document.csv"
        rows = self.db._con.execute(
            """SELECT d.*,
                      p.place_name, p.street_sort, p.neighborhood,
                      p.chc_dist, p.lat AS place_lat, p.lon AS place_lon,
                      f.cdash_folder_name,
                      f.item_set_id AS folder_item_set_id
               FROM cdash_doc d
               LEFT JOIN cdash_place  p ON d.place_item_id = p.place_item_id
               LEFT JOIN cdash_folder f ON d.item_set_id   = f.item_set_id
               ORDER BY f.folder_number, d.folder_doc_sequence"""
        ).fetchall()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "resourceTemplate", "resourceClass", "identifier", "title",
                "Type", "bibliographicCitation", "rights",
                "placeitem", "placeItemID", "Folder", "ItemSetID",
                "numPages", "streetsort", "Neighborhood", "chcDist",
                "dateAccepted", "lat", "lon",
            ])
            w.writeheader()
            for d in rows:
                w.writerow({
                    "resourceTemplate":    "CDASH Document",
                    "resourceClass":       "bibo:Document",
                    "identifier":          d["batch_doc_id"],
                    "title":               d["doc_title"],
                    "Type":                d["doc_type_description"],
                    "bibliographicCitation": (
                        "Cambridge Historical Commission, "
                        "Digital Architectural Survey and History."
                    ),
                    "rights":              "Rights status not evaluated.",
                    "placeitem":           d["place_item_id"],
                    "placeItemID":         d["place_item_id"],
                    "Folder":              d["cdash_folder_name"],
                    "ItemSetID":           d["folder_item_set_id"],
                    "numPages":            d["num_pages"],
                    "streetsort":          d["street_sort"],
                    "Neighborhood":        d["neighborhood"],
                    "chcDist":             d["chc_dist"],
                    "dateAccepted":        d["date_accepted"],
                    "lat":                 d["place_lat"],
                    "lon":                 d["place_lon"],
                })

    def _write_media_csv(self):
        out = self.catalog_path / "media.csv"
        rows = self.db._con.execute(
            """SELECT m.*, d.doc_type_code, d.batch_doc_id, d.num_pages AS doc_pages
               FROM cdash_media m
               LEFT JOIN cdash_doc d ON m.doc_item_id = d.doc_item_id
               ORDER BY m.item_set_id, m.filename"""
        ).fetchall()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "ResourceTemplate", "Title", "identifier",
                "Relation", "type", "Source", "number", "dateAccepted",
                "format_note", "doc_pages",
            ])
            w.writeheader()
            for m in rows:
                w.writerow({
                    "ResourceTemplate": "CDASH Media",
                    "Title":            m["filename"],
                    "identifier":       m["batch_media_id"] or "",
                    "Relation":         m["batch_doc_id"] or "",
                    "type":             m["doc_type_code"] or "",
                    "Source":           self.batch_path.name + "/" + m["filepath"].replace("\\", "/"),
                    "number":           m["page_num"],
                    "dateAccepted":     m["capture_date"],
                    "format_note":      m["format_note"] or "",
                    "doc_pages":        m["doc_pages"] or "",
                })

    def reopen_db(self):
        """No-op — kept for API compatibility. check_same_thread=False is set
        on the connection so the DB is usable from any thread sequentially."""
        pass

    # ------------------------------------------------------------------ close

    def close(self):
        self._validator.close()
        if self.db:
            self.db.close()


# ---------------------------------------------------------------------------
# CLI test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import shutil
    import tempfile

    # Locate the test batch (two levels up from src/cdash_digester/)
    _project_root = Path(__file__).parent.parent.parent
    _src = _project_root / "CDB260430-Test_batch"
    if not _src.is_dir():
        print(f"Test batch not found: {_src}")
        sys.exit(1)

    _tmp = _src.name + "_tmp"
    shutil.copytree(str(_src), str(_tmp))
    print(f"Test batch copied to: {_tmp}\n")

    def _log(msg: str, level: str = "info"):
        print(f"[{level.upper():7s}] {msg}")

    digester = Digester(_tmp, _log)
    digester.load_or_initialize()
    digester.scan_batch()
    print()
    print(digester.get_status_summary())
    digester.close()
