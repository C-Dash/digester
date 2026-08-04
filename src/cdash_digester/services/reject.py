"""RejectService — move Reject-flagged media files to a top-level archive.

Extracted from the Repair pipeline pattern (services/repair.py). Unlike repair,
which rewrites a file in place, reject moves the file out of Media/<folder>/
into rejects/<folder>/ at the batch root, then relies on the existing rescan
machinery to drop the stale DB row.
"""

import shutil
from typing import List

from ..repair_media import (
    REPAIR_REJECT_ACTION_REJECTED, parse_repair_issues,
    _append_repair_reject_csv,
)


class RejectService:
    def __init__(self, session, scan):
        self._session = session
        self._scan = scan

    def rejectable_media_ids(self, media_ids: List[int]) -> List[int]:
        """Return the subset of media_ids flagged Reject."""
        db = self._session.db
        if not db:
            return []
        out = []
        for media_id in media_ids:
            row = db.get_media(media_id)
            if row and "reject" in parse_repair_issues(row.repair_issues):
                out.append(media_id)
        return out

    def reject_media_files(self, media_ids: List[int]):
        """Move selected Reject-flagged media files to rejects/<folder>/.

        If a source media folder ends up empty, it is removed and an alert
        is logged. Each affected folder is then rescanned so the DB drops
        the moved file's row — or, if any folder was removed outright, a
        full batch scan runs instead so the folder pane no longer lists it.
        """
        session = self._session
        if not session.db:
            session.log("No open batch.", "error")
            return

        rejected = 0
        failed = 0
        skipped = 0
        affected_folders: set = set()
        source_dirs: dict = {}   # item_set_id -> Path

        for media_id in media_ids:
            row = session.db.get_media(media_id)
            if not row:
                skipped += 1
                session.log(f"Media ID {media_id} not found.", "warning")
                continue

            issues = parse_repair_issues(row.repair_issues)
            if "reject" not in issues:
                skipped += 1
                session.log(f"Skipping {row.filename}: not flagged Reject.", "info")
                continue

            filepath = session.batch_path / row.filepath
            if not filepath.exists():
                failed += 1
                session.log(f"Cannot reject {row.filename}: file not found.", "error")
                continue

            folder_row = session.db.get_folder_by_item_set(row.item_set_id)
            folder_name = (folder_row.os_folder_name if folder_row
                           else filepath.parent.name)
            dest_dir = session.rejects_path / folder_name
            dest_dir.mkdir(parents=True, exist_ok=True)

            try:
                shutil.move(str(filepath), str(dest_dir / filepath.name))
            except OSError as exc:
                failed += 1
                session.log(f"Cannot move {row.filename}: {exc}", "error")
                continue

            # Log the parsed repair issues, matching what repair_file() writes
            # into the shared Repair_Issues column. (This used to pass the PIL
            # format string, so reject rows read e.g. "RGB 24-bit" in a column
            # of repair codes.)
            _append_repair_reject_csv(
                session.catalog_path, filepath,
                issues, REPAIR_REJECT_ACTION_REJECTED,
            )

            rejected += 1
            affected_folders.add(row.item_set_id)
            source_dirs[row.item_set_id] = filepath.parent
            session.log(f"  Rejected {row.filename} -> rejects/{folder_name}/",
                     "success")

        session.log(
            f"Reject complete: {rejected} moved, {failed} failed, {skipped} skipped.",
            "info",
        )
        session.collect_and_store_counts()

        # Remove any now-empty source folders BEFORE rescanning, so a folder
        # that disappears is caught by the rescan rather than left as a
        # stale DB row (which would keep it listed in the folder pane).
        removed_item_set_ids: set = set()
        for item_set_id, folder_dir in source_dirs.items():
            if folder_dir.is_dir() and not any(folder_dir.iterdir()):
                try:
                    folder_dir.rmdir()
                    removed_item_set_ids.add(item_set_id)
                    session.log(
                        f"ALERT: media folder '{folder_dir.name}' is now "
                        f"empty and was removed.",
                        "warning",
                    )
                except OSError as exc:
                    session.log(
                        f"Could not remove empty folder {folder_dir.name}: {exc}",
                        "error",
                    )

        if removed_item_set_ids:
            # A folder vanished — rescan_folder() only
            # updates media/doc rows for a folder still on disk, it doesn't
            # prune the cdash_folder row itself. A full batch scan rebuilds
            # cdash_folder from what's actually on disk, so it also covers
            # every other affected folder in one pass.
            self._scan.scan_batch()
        else:
            for item_set_id in affected_folders:
                self._scan.rescan_folder(item_set_id)
