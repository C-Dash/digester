"""ScanService and FolderScanner — the batch/folder/media scan pipeline.

Extracted from Digester.scan_batch / _scan_folder / _scan_media_in_folder /
validate_folder. The per-folder media loop (doc tracking, place inheritance,
page numbering, renaming) is lifted into FolderScanner, which holds the
previously-local scan state as instance attributes. Logic and log messages are
preserved verbatim.
"""

from datetime import datetime
from pathlib import Path

from ..cdash_objects import (
    DOC_TYPES, ACCEPTED_SUFFIXES,
    parse_batch_name, parse_folder_name, parse_media_name, slugify,
)


class ScanService:
    def __init__(self, dig):
        self._dig = dig

    # ------------------------------------------------------------ full scan

    def scan_batch(self):
        """Full batch scan: rebuild working tables, scan all folders and media."""
        dig = self._dig
        if dig.db is None:
            dig.log("No open batch — call load_or_initialize first.", "error")
            return

        parsed = parse_batch_name(dig.batch_path.name)
        if not parsed:
            return

        # Rebuild the working tables in place, preserving the DB file and any
        # persistent cache tables (cdash_batch is kept and updated below).
        dig.db.create_all_tables()
        dig.db.clear_working_tables()
        dig.db.upsert_batch(
            batch_id=parsed["batch_id"],
            name=parsed["name"],
            batch_folder_path=str(dig.batch_path),
            initialized_date=datetime.now().date().isoformat(),
        )
        dig.log(f"Scanning batch {parsed['batch_id']} …", "info")

        folder_dirs = sorted(
            p for p in dig.media_path.iterdir() if p.is_dir()
        )
        # Folder indices are persistent: a folder already in the cache keeps its
        # index (so it is never renamed/renumbered); a newly added folder gets
        # the next available index.
        next_index = dig.db.max_folder_cache_index() + 1
        for folder_dir in folder_dirs:
            fparsed = parse_folder_name(folder_dir.name)
            if not fparsed:
                self._scan_folder(folder_dir, folder_index=None,
                                  batch_id=parsed["batch_id"])
                continue
            cached = dig.db.get_folder_cache(fparsed["item_set_id"])
            if cached and cached["folder_index"] is not None:
                folder_index = cached["folder_index"]
            else:
                folder_index = next_index
                next_index += 1
            self._scan_folder(folder_dir, folder_index=folder_index,
                              batch_id=parsed["batch_id"])

        dig.db.recalculate_batch_ready()
        counts = dig._collect_and_store_counts()
        batch = dig.db.get_batch()
        dig.log(
            f"Scan complete — ready: {batch['ready']}  "
            + dig._counts_summary(counts),
            "info",
        )

    # ------------------------------------------------------- single folder

    def rescan_folder(self, item_set_id: int):
        """Re-scan a single folder (Folder → Rescan Selected Folder)."""
        dig = self._dig
        if dig.db is None:
            return
        folder_row = dig.db.get_folder_by_item_set(item_set_id)
        if not folder_row:
            dig.log(f"Folder {item_set_id} not found in DB.", "error")
            return
        folder_dir = dig.media_path / folder_row["os_folder_name"]
        if not folder_dir.is_dir():
            dig.log(f"Folder not found on disk: {folder_dir}", "error")
            return

        self._delete_folder_records(item_set_id)
        self._scan_media_in_folder(
            folder_dir, item_set_id,
            batch_folder_id=folder_row.get("batch_folder_id") or "",
        )
        dig.db.recalculate_batch_ready()
        dig.log(f"Re-scanned: {folder_row['os_folder_name']}", "info")

    def _delete_folder_records(self, item_set_id: int):
        """Remove media and doc records for one folder before re-scanning."""
        con = self._dig.db._con
        con.execute("DELETE FROM cdash_media WHERE item_set_id=?", (item_set_id,))
        con.execute("DELETE FROM cdash_doc   WHERE item_set_id=?", (item_set_id,))
        con.commit()

    # -------------------------------------------------- folder scan internals

    def _scan_folder(self, folder_dir: Path, folder_index: int, batch_id: str):
        dig = self._dig
        dig.log(f"Folder: {folder_dir.name}", "info")

        parsed = parse_folder_name(folder_dir.name)
        if not parsed:
            dig.log(
                f"  Cannot parse folder name — not ready: {folder_dir.name}",
                "warning",
            )
            # Register the folder as not name-ready so the problem surfaces in
            # the batch. There is no item_set_id to key on (NULL) and no media
            # is scanned for it.
            dig.db.upsert_folder(
                item_set_id=None,
                cdash_folder_name=folder_dir.name,
                os_folder_name=folder_dir.name,
                name_ready=False,
                notes="Folder name not in CDASH format",
            )
            return

        item_set_id = parsed["item_set_id"]
        batch_folder_id = f"{batch_id}F{folder_index}"

        # Resolve the folder name (cache → validator API). On failure fall back
        # to the parsed slug, leaving the folder not name-ready.
        name, name_ready = dig._validation.resolve_folder_name(
            item_set_id, folder_index)
        cdash_folder_name = name if name is not None else parsed["slug"]

        # Rename folder to validated, identified form if necessary
        canonical = f"F{folder_index}-{slugify(cdash_folder_name)}-OF{item_set_id}"
        if folder_dir.name != canonical:
            new_path = folder_dir.parent / canonical
            try:
                folder_dir.rename(new_path)
                dig.log(f"  Renamed -> {canonical}", "info")
                folder_dir = new_path
            except OSError as exc:
                dig.log(f"  Rename failed: {exc}", "error")

        dig.db.upsert_folder(
            item_set_id=item_set_id,
            cdash_folder_name=cdash_folder_name,
            os_folder_name=folder_dir.name,
            batch_folder_id=batch_folder_id,
            name_ready=name_ready,
        )
        dig.db.assign_folder_index(item_set_id, folder_index)

        self._scan_media_in_folder(folder_dir, item_set_id, batch_folder_id)

    def _scan_media_in_folder(self, folder_dir: Path,
                              item_set_id: int, batch_folder_id: str):
        FolderScanner(self._dig, folder_dir, item_set_id, batch_folder_id).run()


class FolderScanner:
    """Screens and registers every media file in one folder.

    Holds the per-folder scan state (document tracker, slug→place map, document
    sequence counter) that was previously local to _scan_media_in_folder.

    Format-rejected files stay in place; their issues are recorded in
    cdash_media.notes and they continue through name parsing and place
    validation so all problems are captured in a single scan.
    """

    def __init__(self, dig, folder_dir: Path, item_set_id: int,
                 batch_folder_id: str):
        self._dig = dig
        self.folder_dir = folder_dir
        self.item_set_id = item_set_id
        self.batch_folder_id = batch_folder_id
        # doc_index → {doc_item_id, place_slug, doc_type, doc_seq, place_id, page_count}
        self.doc_tracker: dict = {}
        self.slug_place_tracker: dict = {}   # place_slug → place_id
        self.doc_seq = 0                     # folder_doc_sequence counter

    def run(self):
        dig = self._dig
        media_files = sorted(
            f for f in self.folder_dir.iterdir()
            if f.is_file() and f.suffix.lower() in ACCEPTED_SUFFIXES
        )
        for filepath in media_files:
            self._process_file(filepath)
        dig.db.recalculate_folder_status(self.item_set_id)

    def _process_file(self, filepath: Path):
        dig = self._dig
        item_set_id = self.item_set_id
        batch_folder_id = self.batch_folder_id

        dig.log(f"  {filepath.name}", "info")
        notes_parts: list = []
        repair_issues = ""

        # 1. Format screening (cache → prescreener)
        accepted, props = dig._screening.screen(filepath)
        repair_issues = ", ".join(props.get("repair_issues", []))
        if not accepted:
            notes_parts.append(props.get("qa_note", "Format rejected"))
            dig.log(f"    Format issue: {props.get('qa_note')}", "warning")

        # 2. Name parsing
        parsed = parse_media_name(filepath.stem)
        rel_path = str(filepath.relative_to(dig.batch_path))

        if not parsed:
            notes_parts.append("Name not in ready format")
            dig.db.insert_media(
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
            dig.log("    Not-ready name.", "info")
            return

        place_slug = parsed["place_slug"]
        doc_index = parsed["doc_index"]
        doc_type = parsed["doc_type"]
        place_id = parsed["place_id"]

        if doc_index in self.doc_tracker and (place_slug == self.doc_tracker[doc_index]["place_slug"] and place_id == None):
            # same document no place_id
            place_id = self.doc_tracker[doc_index]["place_id"]
            doc_type = self.doc_tracker[doc_index]["doc_type"]
        elif place_id is None and place_slug in self.slug_place_tracker:
            # same slug seen before — inherit place_id only, not doc_type
            place_id = self.slug_place_tracker[place_slug]

        # 3. Place validation (cache → API)
        place_name = None
        if place_id is not None:
            p_status, p_name = dig._validation.ensure_place(place_id)
            if "Valid" in p_status:
                place_name = p_name or parsed["place_slug"]
            else:
                dig.log(f"    Place {place_id}: {p_status}", "error")
                notes_parts.append(f"Place {place_id}: {p_status}")
                place_name = parsed["place_slug"]

        if place_id is None:
            notes_parts.append("No place ID in filename")
        else:
            self.slug_place_tracker[place_slug] = place_id

        media_ready = not notes_parts

        # 4. Doc tracking / creation
        if doc_index not in self.doc_tracker:  # New Document
            self.doc_seq += 1
            batch_doc_id = (
                f"{batch_folder_id}-{parsed['place_slug']}-{self.doc_seq:04d}-{doc_type}"
            )
            doc_title = (
                f"{place_name or parsed['place_slug']} — "
                f"{DOC_TYPES.get(doc_type, doc_type)}"
            )
            doc_item_id = dig.db.insert_doc(
                place_item_id=place_id,
                item_set_id=item_set_id,
                folder_doc_sequence=self.doc_seq,
                doc_type_code=doc_type,
                doc_title=doc_title,
                batch_doc_id=batch_doc_id,
                date_accepted=props.get("capture_date"),
                ready=media_ready,
            )
            self.doc_tracker[doc_index] = {
                "doc_item_id":  doc_item_id,
                "place_slug":   parsed["place_slug"],
                "doc_type":     doc_type,
                "doc_seq":      self.doc_seq,
                "place_id":     place_id,
                "page_count":   0,
            }
        else:
            doc_item_id = self.doc_tracker[doc_index]["doc_item_id"]
            if parsed["place_slug"] != self.doc_tracker[doc_index]["place_slug"]:
                msg = (
                    f"doc_index {doc_index:04d} conflicts with existing "
                    f"place name — skipped."
                )
                dig.log(f"    ERROR: {msg}", "error")
                dig.db.insert_media(
                    doc_item_id=None,
                    item_set_id=item_set_id,
                    filename=filepath.name,
                    filepath=rel_path,
                    repair_issues=repair_issues,
                    ready=False,
                    notes=msg,
                )
                return

        # 5. Page number
        self.doc_tracker[doc_index]["page_count"] += 1
        page_num = self.doc_tracker[doc_index]["page_count"]
        entry = self.doc_tracker[doc_index]
        batch_media_id = (
            f"{batch_folder_id}-{entry['place_slug']}"
            f"_{entry['doc_seq']:04d}p{page_num:04d}-{entry['doc_type']}"
        )
        dig.db.increment_doc_pages(
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
                    old_rel = rel_path
                    filepath.rename(new_path)
                    filepath = new_path
                    rel_path = str(filepath.relative_to(dig.batch_path))
                    # Re-key the file cache so the next scan hits the cache
                    # under the canonical name (rename preserves mtime/size).
                    dig.db.update_file_cache_path(old_rel, rel_path)
                    dig.log(f"    -> {new_name}", "info")
                except OSError as exc:
                    dig.log(f"    Rename failed: {exc}", "error")
                    notes_parts.append(f"Rename failed: {exc}")

        # 7. Register media
        dig.db.insert_media(
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
