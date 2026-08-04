"""AssignmentService — interactive metadata assignment.

Extracted from Digester.assign_media_to_doc and its helpers (_rename_media,
_current_doc_type, _current_place_info). Logic preserved verbatim.
"""

from typing import List, Optional, Tuple

from ..constants import DOC_TYPES
from ..naming import slugify
from .validation import PLACE_FOLDER_MISMATCH_NOTE


class AssignmentService:
    def __init__(self, session, validation):
        self._session = session
        self._validation = validation

    def _current_doc_type(self, media_id: int) -> Optional[str]:
        """Return the doc_type_code from the existing doc for this media file."""
        db = self._session.db
        row = db.get_media(media_id)
        if not row or not row.doc_item_id:
            return None
        doc = db.get_doc(row.doc_item_id)
        return doc.doc_type_code if doc else None

    def _current_place_info(self, media_id: int) -> Tuple[Optional[int], Optional[str]]:
        """Return (place_id, place_name) from the existing doc for this media file."""
        db = self._session.db
        row = db.get_media(media_id)
        if not row or not row.doc_item_id:
            return None, None
        doc = db.get_doc(row.doc_item_id)
        if not doc or not doc.place_item_id:
            return None, None
        place_row = db.get_place(doc.place_item_id)
        place_name = place_row.place_name if place_row else None
        return doc.place_item_id, place_name

    def assign_media_to_doc(self, media_ids: List[int], place_id: int,
                            doc_type_code: str, is_multi_page: bool) -> bool:
        """Assign selected media files to a new document.

        is_multi_page=True  → all files become pages of one document.
        is_multi_page=False → each file becomes a separate single-page document.
        Returns True on success.
        """
        session = self._session
        if not session.db:
            return False

        place_no_change = place_id is None
        type_no_change  = doc_type_code is None

        # Validate place when a new place_id was provided.
        if not place_no_change:
            p_status, place_name = self._validation.ensure_place(place_id)
            if "Valid" not in p_status:
                session.log(f"Place validation failed: {p_status}", "error")
                return False
            place_slug = slugify(place_name)
        else:
            place_name = None   # resolved per-file (or from first file for multi-page)
            place_slug = None

        first = session.db.get_media(media_ids[0])
        if not first:
            return False
        item_set_id = first.item_set_id

        # Reject an explicitly entered place that isn't associated with this
        # folder in CDASH (No Change inherits a previously validated place).
        if not place_no_change and not self._validation.place_associated_with_folder(
                place_id, item_set_id):
            session.log(PLACE_FOLDER_MISMATCH_NOTE, "error")
            return False

        folder_row = session.db.get_folder_by_item_set(item_set_id)
        batch_folder_id = (folder_row.batch_folder_id or "") if folder_row else ""

        docs = session.db.get_docs_for_folder(item_set_id)
        next_seq = max((d.folder_doc_sequence for d in docs), default=0) + 1

        # For multi-page No Change doc_type: use first file's type.
        if type_no_change:
            first_type = self._current_doc_type(media_ids[0])
            if first_type is None:
                session.log(
                    "Cannot determine doc type — first file has no existing document.",
                    "error",
                )
                return False
            doc_type_code = first_type   # overridden per-file in single-page branch

        # For No Change place: resolve from first file; for multi-page set globals now.
        if place_no_change:
            first_place_id, first_place_name = self._current_place_info(media_ids[0])
            if first_place_id is None:
                session.log(
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
                doc_id = session.db.insert_doc(
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
                    session.db.assign_media_to_doc(media_id, doc_id, page_num,
                                               batch_media_id)
                    self._rename_media(media_id, place_id, place_slug,
                                       next_seq, page_num, doc_type_code)
                    # Assignment updates metadata only; the file's ready status
                    # and notes (e.g. format/repair issues) are left as-is.
                session.db.renumber_doc_pages(doc_id)
            else:
                for page_num, media_id in enumerate(sorted(media_ids), start=1):
                    # Resolve per-file place when No Change.
                    if place_no_change:
                        eff_place_id, eff_place_name = self._current_place_info(media_id)
                        if eff_place_id is None:
                            session.log(
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
                        session.log(
                            f"Skipping media {media_id}: no existing doc type.", "warning"
                        )
                        next_seq += 1
                        continue

                    eff_title    = f"{eff_place_name} — {DOC_TYPES.get(effective_type, effective_type)}"
                    batch_doc_id = f"{batch_folder_id}-{eff_slug}-{next_seq:04d}-{effective_type}"
                    doc_id = session.db.insert_doc(
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
                    session.db.assign_media_to_doc(media_id, doc_id, 1, batch_media_id)
                    self._rename_media(media_id, eff_place_id, eff_slug,
                                       next_seq, 1, effective_type)
                    # Assignment updates metadata only; ready status and notes
                    # are left as-is.
                    session.db.renumber_doc_pages(doc_id)
                    next_seq += 1

            session.db.recalculate_folder_status(item_set_id)
            session.db.recalculate_batch_ready()
            return True

        except Exception as exc:
            session.log(f"assign_media_to_doc failed: {exc}", "error")
            return False

    def _rename_media(self, media_id: int, place_id: int, place_slug: str,
                      doc_seq: int, page_num: int, doc_type: str):
        session = self._session
        row = session.db.get_media(media_id)
        if not row:
            return
        old_path = session.batch_path / row.filepath
        new_name = (
            f"{place_slug}_{doc_seq:04d}p{page_num:04d}"
            f"-{doc_type}-OP{place_id}{old_path.suffix.lower()}"
        )
        new_path = old_path.parent / new_name
        try:
            if old_path != new_path:
                old_path.rename(new_path)
            session.db.update_media_filename(
                media_id, new_name,
                str(new_path.relative_to(session.batch_path)),
            )
        except OSError as exc:
            session.log(f"  Rename failed for {row.filename}: {exc}", "error")
