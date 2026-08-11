"""ScanService and FolderScanner — the batch/folder/media scan pipeline.

Extracted from Digester.scan_batch / _scan_folder / _scan_media_in_folder /
validate_folder. The per-folder media loop (doc tracking, place inheritance,
page numbering, renaming) is lifted into FolderScanner, which holds the
previously-local scan state as instance attributes. Logic and log messages are
preserved verbatim.
"""

from datetime import datetime
from pathlib import Path

from ..constants import DOC_TYPES, FILENAME_ISSUE_SEP, NEEDS_DOC_TYPE_NOTE
from ..naming import (
    DOC_INDEX_DELIM, natural_key,
    parse_batch_name, parse_capture_name, parse_folder_name, parse_media_name,
    slugify,
)
from ..models import join_format_issues, join_repair_issues
from .validation import PLACE_FOLDER_MISMATCH_NOTE, place_failure_note


class ScanService:
    def __init__(self, session, validation, screening):
        self._session = session
        self._validation = validation
        self._screening = screening

    # ------------------------------------------------------------ full scan

    def scan_batch(self):
        """Full batch scan: rebuild working tables, scan all folders and media."""
        session = self._session
        if session.db is None:
            session.log("No open batch — call load_or_initialize first.", "error")
            return

        parsed = parse_batch_name(session.batch_path.name)
        if not parsed:
            return

        # Rebuild the working tables in place, preserving the DB file and any
        # persistent cache tables (cdash_batch is kept and updated below).
        session.db.create_all_tables()
        session.db.clear_working_tables()
        session.db.upsert_batch(
            batch_id=parsed["batch_id"],
            name=parsed["name"],
            batch_folder_path=str(session.batch_path),
            initialized_date=datetime.now().date().isoformat(),
        )
        session.log(f"Scanning batch {parsed['batch_id']} …", "info")

        folder_dirs = sorted(
            p for p in session.media_path.iterdir() if p.is_dir()
        )
        for folder_dir, folder_index in self._assign_folder_indices(folder_dirs):
            self._scan_folder(folder_dir, folder_index=folder_index,
                              batch_id=parsed["batch_id"])

        session.db.recalculate_batch_ready()
        counts = session.collect_and_store_counts()
        batch = session.db.get_batch()
        session.log(
            f"Scan complete — ready: {batch.ready}  "
            + session.counts_summary(counts),
            "info",
        )

    def _assign_folder_indices(self, folder_dirs):
        """Resolve each folder's F<index>, yielding (folder_dir, folder_index).

        The folder's own on-disk name is authoritative: a folder already named
        F3-… keeps index 3 no matter what the cache says. That is what makes
        the numbering survive Purge Validation Caches — and with it the
        batch_folder_id / batch_doc_id / batch_media_id values built from it,
        which are exported as CSV identifiers.

        Precedence per folder:
          1. an index in the name, if no earlier folder claimed it;
          2. otherwise the cached index, if free (covers a folder that lost
             its prefix);
          3. otherwise the next free number, which triggers a rename.

        The cache is no longer the authority, only memory: it still raises the
        allocation floor so a number is not reused for a different folder.
        Unparseable names yield index None and are handled by _scan_folder.
        """
        session = self._session
        parsed_dirs = [(d, parse_folder_name(d.name)) for d in folder_dirs]

        # Never hand out a number that is already on disk or remembered in the
        # cache, even if the folder that owns it is absent from this scan.
        on_disk = [f["folder_index"] for _, f in parsed_dirs
                   if f and f["folder_index"] is not None]
        next_index = max([session.db.max_folder_cache_index()] + on_disk) + 1

        claimed = {}          # folder_index -> folder name that took it
        results = []
        for folder_dir, fparsed in parsed_dirs:
            if not fparsed:
                results.append((folder_dir, None))
                continue

            named = fparsed["folder_index"]
            if named is not None and named not in claimed:
                folder_index = named
            else:
                if named is not None:
                    session.log(
                        f"  Duplicate folder index F{named}: '{folder_dir.name}' "
                        f"collides with '{claimed[named]}' — reassigning to "
                        f"F{next_index}.",
                        "warning",
                    )
                    folder_index = next_index
                    next_index += 1
                else:
                    cached = session.db.get_folder_cache(fparsed["item_set_id"])
                    cached_index = cached["folder_index"] if cached else None
                    if cached_index is not None and cached_index not in claimed:
                        folder_index = cached_index
                    else:
                        folder_index = next_index
                        next_index += 1

            claimed[folder_index] = folder_dir.name
            results.append((folder_dir, folder_index))
        return results

    # ------------------------------------------------------- single folder

    def rescan_folder(self, item_set_id: int):
        """Re-scan a single folder (Folder → Rescan Selected Folder)."""
        session = self._session
        if session.db is None:
            return
        folder_row = session.db.get_folder_by_item_set(item_set_id)
        if not folder_row:
            session.log(f"Folder {item_set_id} not found in DB.", "error")
            return
        folder_dir = session.media_path / folder_row.os_folder_name
        if not folder_dir.is_dir():
            session.log(f"Folder not found on disk: {folder_dir}", "error")
            return

        self._delete_folder_records(item_set_id)
        self._scan_media_in_folder(
            folder_dir, item_set_id,
            batch_folder_id=folder_row.batch_folder_id or "",
        )
        session.db.recalculate_batch_ready()
        session.log(f"Re-scanned: {folder_row.os_folder_name}", "info")

    def _delete_folder_records(self, item_set_id: int):
        """Remove media and doc records for one folder before re-scanning."""
        self._session.db.delete_folder_records(item_set_id)

    # -------------------------------------------------- folder scan internals

    def _scan_folder(self, folder_dir: Path, folder_index: int, batch_id: str):
        session = self._session
        session.log(f"Folder: {folder_dir.name}", "info")

        parsed = parse_folder_name(folder_dir.name)
        if not parsed:
            session.log(
                f"  Cannot parse folder name — not ready: {folder_dir.name}",
                "warning",
            )
            # Register the folder as not name-ready so the problem surfaces in
            # the batch. There is no item_set_id to key on (NULL) and no media
            # is scanned for it.
            session.db.upsert_folder(
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
        name, name_ready = self._validation.resolve_folder_name(
            item_set_id, folder_index)
        cdash_folder_name = name if name is not None else parsed["slug"]

        # Rename folder to validated, identified form if necessary
        canonical = f"F{folder_index}-{slugify(cdash_folder_name)}-OF{item_set_id}"
        if folder_dir.name != canonical:
            new_path = folder_dir.parent / canonical
            try:
                folder_dir.rename(new_path)
                session.log(f"  Renamed -> {canonical}", "info")
                folder_dir = new_path
            except OSError as exc:
                session.log(f"  Rename failed: {exc}", "error")

        session.db.upsert_folder(
            item_set_id=item_set_id,
            cdash_folder_name=cdash_folder_name,
            os_folder_name=folder_dir.name,
            batch_folder_id=batch_folder_id,
            name_ready=name_ready,
        )
        session.db.assign_folder_index(item_set_id, folder_index)

        # Teach the cache the index we actually used. resolve_folder_name only
        # writes the cache on an API miss, so a name-derived index would never
        # reach it otherwise — leaving max_folder_cache_index() stale and able
        # to hand the same number to a different folder later. Skipped when it
        # already agrees, to avoid a pointless write per folder per scan.
        cached = session.db.get_folder_cache(item_set_id)
        if cached and cached["folder_index"] != folder_index:
            session.db.set_folder_cache_index(item_set_id, folder_index)

        self._scan_media_in_folder(folder_dir, item_set_id, batch_folder_id)

    def _scan_media_in_folder(self, folder_dir: Path,
                              item_set_id: int, batch_folder_id: str):
        FolderScanner(self._session, self._validation, self._screening,
                      folder_dir, item_set_id, batch_folder_id).run()


class FolderScanner:
    """Screens and registers every media file in one folder.

    Holds the per-folder scan state (document tracker, slug→place map, document
    sequence counter) that was previously local to _scan_media_in_folder.

    Every file in the folder is registered, regardless of suffix — a file
    outside the accepted formats still gets a row (format identified via PIL
    when possible, Reject-flagged always) so it shows up with a thumbnail
    attempt and can be moved out via the Reject action. Format-rejected files
    stay in place; their issues are recorded in cdash_media.format_issues and
    they continue through name parsing and place validation so all problems
    are captured in a single scan.
    """

    def __init__(self, session, validation, screening, folder_dir: Path,
                 item_set_id: int, batch_folder_id: str):
        self._session = session
        self._validation = validation
        self._screening = screening
        self.folder_dir = folder_dir
        self.item_set_id = item_set_id
        self.batch_folder_id = batch_folder_id
        # doc_index → {doc_item_id, place_slug, id_slug, doc_type, place_id,
        #              page_count}. Keyed by the document's index, which is the
        #              same number the filename carries and the same value
        #              stored as cdash_doc.folder_doc_sequence.
        self.doc_tracker: dict = {}
        self.slug_place_tracker: dict = {}   # place_slug → place_id
        # place_slug → doc_index of the capture run currently open for that
        # slug. A capture file that repeats -OP<id> closes the run and starts a
        # new document; one that omits it joins the open run as the next page.
        self.capture_runs: dict = {}

    def run(self):
        session = self._session
        # Every file is scanned, regardless of suffix — f.is_file() alone
        # already excludes subdirectories like the repaired/ backup folder.
        # Sorted naturally, not lexically: capture runs are grouped in file
        # order and the rename that follows is not reversible, so "Slug-10"
        # must not overtake "Slug-9".
        media_files = sorted(
            (f for f in self.folder_dir.iterdir() if f.is_file()),
            key=lambda f: natural_key(f.name),
        )
        for filepath in media_files:
            self._process_file(filepath)
        session.db.recalculate_folder_status(self.item_set_id)

    def _process_file(self, filepath: Path):
        session = self._session
        item_set_id = self.item_set_id
        batch_folder_id = self.batch_folder_id

        # session.log(f"  {filepath.name}", "info")
        notes_parts: list = []
        repair_issues = ""

        # 1. Format screening (cache → prescreener)
        accepted, props = self._screening.screen(filepath)
        repair_issues = join_repair_issues(props.get("repair_issues"))
        format_issues = join_format_issues(props.get("format_issues"))
        if not accepted:
            session.log(f"    Format issue: {format_issues}", "warning")

        # 2. Name parsing.
        # A stem is read first as an indexed (canonical or part-canonical) name,
        # and only then as a field capture name — an indexed stem is never a
        # capture stem, and trying them the other way round would re-mint an
        # index for a file that already has one.
        parsed = parse_media_name(filepath.stem)
        capture = parse_capture_name(filepath.stem) if not parsed else None
        rel_path = str(filepath.relative_to(session.batch_path))

        if not parsed and not capture:
            notes_parts.append("Name not in ready format")
            session.db.insert_media(
                doc_item_id=None,
                item_set_id=item_set_id,
                filename=filepath.name,
                filepath=rel_path,
                capture_date=props.get("capture_date"),
                file_size_mb=props.get("file_size_mb"),
                pixel_width=props.get("pixel_width"),
                pixel_height=props.get("pixel_height"),
                format=props.get("format"),
                format_issues=format_issues,
                repair_issues=repair_issues,
                ready=False,
                filename_issues=FILENAME_ISSUE_SEP.join(notes_parts),
            )
            session.log("    Not-ready name.", "info")
            return

        if parsed:
            place_slug = parsed["place_slug"]
            doc_index = parsed["doc_index"]
            doc_type = parsed["doc_type"]
            place_id = parsed["place_id"]

            if doc_index in self.doc_tracker and (place_slug == self.doc_tracker[doc_index]["place_slug"] and place_id == None):
                # same document no place_id
                place_id = self.doc_tracker[doc_index]["place_id"]
                # Only fill a doc type in, never overwrite one the name states.
                doc_type = doc_type or self.doc_tracker[doc_index]["doc_type"]
            elif place_id is None and place_slug in self.slug_place_tracker:
                # same slug seen before — inherit place_id only, not doc_type
                place_id = self.slug_place_tracker[place_slug]
        else:
            # Capture name: an index has to be minted, or inherited from the
            # run this file continues. doc_index stays None until step 3b,
            # because a new document is only opened once its place is known.
            place_slug = capture["place_slug"]
            doc_type = capture["doc_type"]
            place_id = capture["place_id"]
            doc_index = None

            open_run = self.capture_runs.get(place_slug)
            if place_id is None and open_run is not None:
                # Continues the open run: the absence of -OP<id> is what marks
                # this file as another page rather than a new document.
                doc_index = open_run
                entry = self.doc_tracker[doc_index]
                place_id = entry["place_id"]
                doc_type = doc_type or entry["doc_type"]
            elif place_id is None:
                # Nothing to attach it to — a capture name with no place ID and
                # no run in progress carries no document identity at all.
                notes_parts.append("Name not in ready format")
                session.db.insert_media(
                    doc_item_id=None,
                    item_set_id=item_set_id,
                    filename=filepath.name,
                    filepath=rel_path,
                    capture_date=props.get("capture_date"),
                    file_size_mb=props.get("file_size_mb"),
                    pixel_width=props.get("pixel_width"),
                    pixel_height=props.get("pixel_height"),
                    format=props.get("format"),
                    format_issues=format_issues,
                    repair_issues=repair_issues,
                    ready=False,
                    filename_issues=FILENAME_ISSUE_SEP.join(notes_parts),
                )
                session.log("    Not-ready name.", "info")
                return

        # 3. Place validation (cache → API)
        # place_ok is the single answer to "did this place resolve?". It used
        # to be inferred from place_name, which was set to the parsed slug on
        # failure — truthy, so an unverified file was renamed as if canonical.
        place_name = None
        place_ok = False
        if place_id is not None:
            p_status, p_name = self._validation.ensure_place(place_id)
            if "Valid" in p_status:
                place_ok = True
                place_name = p_name or place_slug
                if not self._validation.place_associated_with_folder(
                        place_id, item_set_id):
                    notes_parts.insert(0, PLACE_FOLDER_MISMATCH_NOTE)
                    session.log(f"    {PLACE_FOLDER_MISMATCH_NOTE}", "warning")
            else:
                note = place_failure_note(place_id, p_status)
                session.log(f"    {note} ({p_status})", "error")
                notes_parts.append(note)

        if place_id is None:
            notes_parts.append("No place ID in filename")
        elif place_ok:
            # Only remember a place that actually resolved — an unverified ID
            # must not be inherited by a sibling file with the same slug.
            self.slug_place_tracker[place_slug] = place_id

        # The slug this file will actually be named with. Identifiers (step 4/5)
        # and the rename (step 6) are both built from this one value, so they
        # cannot disagree — they used to, leaving batch_media_id describing the
        # pre-rename name until the next scan corrected it.
        # When the place is missing or unresolved the rename is skipped, so the
        # parsed slug is the right fallback: it still matches the filename.
        id_slug = slugify(place_name) if place_ok else place_slug

        # A document with no type yet is registered and named, but is never
        # name-ready: the canonical stem is still missing its -<DT> segment.
        if doc_type is None:
            notes_parts.append(NEEDS_DOC_TYPE_NOTE)

        name_ready = not notes_parts
        media_ready = accepted and name_ready

        # 3b. Mint the document index for a capture name that opens a new
        # document. Deferred to here so the index is drawn after place
        # validation, keeping the numbering contiguous for the common case.
        if doc_index is None:
            doc_index = session.db.next_doc_index(item_set_id)

        # 4. Doc tracking / creation.
        # doc_index is the document's number everywhere: it is what the
        # filename carries, what is stored as folder_doc_sequence, and what
        # both identifiers below are built from. There is no separate
        # scan-order counter — one existed, and it made identifiers disagree
        # with the filenames whenever a folder was not numbered from 0001.
        type_suffix = f"-{doc_type}" if doc_type else ""
        if doc_index not in self.doc_tracker:  # New Document
            batch_doc_id = (
                f"{batch_folder_id}-{id_slug}-{doc_index:04d}{type_suffix}"
            )
            # An unrecognised 2-letter code still shows itself, as before; only
            # a genuinely absent type falls back to "Uncategorized".
            doc_title = (
                f"{place_name or place_slug} — "
                f"{DOC_TYPES.get(doc_type, doc_type or 'Uncategorized')}"
            )
            doc_item_id = session.db.insert_doc(
                # Never write an unverified place: cdash_doc.place_item_id is a
                # foreign key into cdash_place, and ensure_place only inserts a
                # place row on success. Writing the raw id raised IntegrityError
                # and aborted the whole scan over one typo'd filename.
                place_item_id=place_id if place_ok else None,
                item_set_id=item_set_id,
                folder_doc_sequence=doc_index,
                doc_type_code=doc_type,
                doc_title=doc_title,
                batch_doc_id=batch_doc_id,
                date_accepted=props.get("capture_date"),
                ready=media_ready,
            )
            self.doc_tracker[doc_index] = {
                "doc_item_id":  doc_item_id,
                # place_slug stays "as parsed from the filename" — the
                # doc-index conflict check below compares it against the next
                # file's parsed slug, so it must not hold the resolved value.
                "place_slug":   place_slug,
                # id_slug is the canonical slug used for names and identifiers.
                "id_slug":      id_slug,
                "doc_type":     doc_type,
                # None when unresolved, so later pages of this document don't
                # inherit an id that will not validate.
                "place_id":     place_id if place_ok else None,
                "page_count":   0,
            }
            if capture:
                # This capture file opened a document; later files with the
                # same slug and no -OP<id> become its pages. A slug can only
                # have one run open, so this also closes any earlier one.
                self.capture_runs[place_slug] = doc_index
        else:
            doc_item_id = self.doc_tracker[doc_index]["doc_item_id"]
            if place_slug != self.doc_tracker[doc_index]["place_slug"]:
                msg = (
                    f"doc_index {doc_index:04d} conflicts with existing "
                    f"place name — skipped."
                )
                session.log(f"    ERROR: {msg}", "error")
                session.db.insert_media(
                    doc_item_id=None,
                    item_set_id=item_set_id,
                    filename=filepath.name,
                    filepath=rel_path,
                    repair_issues=repair_issues,
                    ready=False,
                    filename_issues=msg,
                )
                return

        # 5. Page number
        self.doc_tracker[doc_index]["page_count"] += 1
        page_num = self.doc_tracker[doc_index]["page_count"]
        entry = self.doc_tracker[doc_index]
        entry_suffix = f"-{entry['doc_type']}" if entry["doc_type"] else ""
        batch_media_id = (
            f"{batch_folder_id}-{entry['id_slug']}"
            f"{DOC_INDEX_DELIM}{doc_index:04d}p{page_num:04d}"
            f"{entry_suffix}"
        )
        session.db.increment_doc_pages(
            doc_item_id,
            props.get("capture_date"),
            count=props.get("pdf_pages") or 1,
        )

        # 6. Rename only when the place actually resolved. An unverifiable
        # place leaves the file exactly as it arrived, so nothing is
        # canonicalised on the strength of metadata we could not confirm; the
        # next scan renames it once the place ID is corrected.
        # Uses the document's id_slug — the same value batch_media_id above was
        # built from — so the filename and the identifier always agree, and
        # every page of a document is named for that document's place.
        if place_ok:
            # The -<DT> segment is omitted while the document type is unknown;
            # the name is still fully re-parseable, so the next scan reads the
            # index back rather than minting a new one, and Assign Metadata
            # completes the stem once a type is chosen.
            new_stem = (
                f"{entry['id_slug']}{DOC_INDEX_DELIM}"
                f"{doc_index:04d}p{page_num:04d}"
                f"{entry_suffix}-OP{place_id}"
            )
            new_name = f"{new_stem}{filepath.suffix.lower()}"
            if new_name != filepath.name:
                new_path = filepath.parent / new_name
                try:
                    old_rel = rel_path
                    filepath.rename(new_path)
                    filepath = new_path
                    rel_path = str(filepath.relative_to(session.batch_path))
                    # Re-key the file cache so the next scan hits the cache
                    # under the canonical name (rename preserves mtime/size).
                    session.db.update_file_cache_path(old_rel, rel_path)
                    session.log(f"    -> {new_name}", "info")
                except OSError as exc:
                    session.log(f"    Rename failed: {exc}", "error")
                    notes_parts.append(f"Rename failed: {exc}")

        # 7. Register media
        session.db.insert_media(
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
            format=props.get("format"),
            format_issues=format_issues,
            repair_issues=repair_issues,
            ready=media_ready,
            filename_issues=FILENAME_ISSUE_SEP.join(notes_parts),
            name_ready=name_ready,
        )
