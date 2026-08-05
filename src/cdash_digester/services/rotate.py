"""RotateService — rotate selected JPEG/TIFF media files 90 degrees.

Extracted following the same pattern as RepairService/RejectService. Unlike
Repair, rotation is user-initiated (not driven by recorded repair_issues) and
excludes PDFs and Reject-flagged files entirely — see repair_media.rotate_file
for the JPEG (metadata-only) vs TIFF (pixel-rotate) split.
"""

from pathlib import Path
from typing import List

from ..repair_media import parse_repair_issues, rotate_file

_ROTATABLE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff"}


class RotateService:
    def __init__(self, session, scan):
        self._session = session
        self._scan = scan

    def rotatable_media_ids(self, media_ids: List[int]) -> List[int]:
        """Return the subset of media_ids eligible for rotation: JPEG/TIFF
        (not PDF, not some other format) and not flagged Reject."""
        db = self._session.db
        if not db:
            return []
        out = []
        for media_id in media_ids:
            row = db.get_media(media_id)
            if not row:
                continue
            if Path(row.filename).suffix.lower() not in _ROTATABLE_SUFFIXES:
                continue
            if "reject" in parse_repair_issues(row.repair_issues):
                continue
            out.append(media_id)
        return out

    def rotate_media_files(self, media_ids: List[int], direction: str):
        """Rotate selected media files 90 degrees ("cw" or "ccw").

        Each affected folder is then rescanned so the DB (and the GUI's
        thumbnail/media panes, reloaded from disk by the caller) reflect the
        rotated files.
        """
        session = self._session
        if not session.db:
            session.log("No open batch.", "error")
            return

        rotated = 0
        failed = 0
        skipped = 0
        affected_folders: set = set()

        for media_id in media_ids:
            row = session.db.get_media(media_id)
            if not row:
                skipped += 1
                session.log(f"Media ID {media_id} not found.", "warning")
                continue

            filepath = session.batch_path / row.filepath
            if not filepath.exists():
                failed += 1
                session.log(f"Cannot rotate {row.filename}: file not found.", "error")
                continue

            session.log(f"Rotating {row.filename} ({direction})…", "info")
            success, message = rotate_file(
                filepath, direction, row.repair_issues,
                catalog_path=session.catalog_path,
                format_issues=row.format_issues or "",
            )
            if success:
                rotated += 1
                affected_folders.add(row.item_set_id)
                session.log(f"  {message}", "success")
            else:
                failed += 1
                session.log(f"  {message}", "error")

        session.log(
            f"Rotate complete: {rotated} rotated, {failed} failed, {skipped} skipped.",
            "info",
        )
        session.collect_and_store_counts()

        for item_set_id in affected_folders:
            self._scan.rescan_folder(item_set_id)
