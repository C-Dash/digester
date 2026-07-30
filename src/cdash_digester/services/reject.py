"""RejectService — move Reject-flagged media files to a top-level archive.

Extracted from the Repair pipeline pattern (services/repair.py). Unlike repair,
which rewrites a file in place, reject moves the file out of Media/<folder>/
into rejects/<folder>/ at the batch root, then relies on the existing rescan
machinery to drop the stale DB row.
"""

import shutil
from typing import List

from ..repair_media import parse_repair_issues, _append_repair_reject_csv


class RejectService:
    def __init__(self, dig):
        self._dig = dig

    def rejectable_media_ids(self, media_ids: List[int]) -> List[int]:
        """Return the subset of media_ids flagged Reject."""
        db = self._dig.db
        if not db:
            return []
        out = []
        for media_id in media_ids:
            row = db.get_media(media_id)
            if row and "reject" in parse_repair_issues(row.get("repair_issues")):
                out.append(media_id)
        return out

    def reject_media_files(self, media_ids: List[int]):
        """Move selected Reject-flagged media files to rejects/<folder>/.

        Each affected folder is then rescanned so the DB drops the moved
        file's row. If a source media folder ends up empty, it is removed
        and an alert is logged.
        """
        dig = self._dig
        if not dig.db:
            dig.log("No open batch.", "error")
            return

        rejected = 0
        failed = 0
        skipped = 0
        affected_folders: set = set()
        source_dirs: dict = {}   # item_set_id -> Path

        for media_id in media_ids:
            row = dig.db.get_media(media_id)
            if not row:
                skipped += 1
                dig.log(f"Media ID {media_id} not found.", "warning")
                continue

            issues = parse_repair_issues(row.get("repair_issues"))
            if "reject" not in issues:
                skipped += 1
                dig.log(f"Skipping {row['filename']}: not flagged Reject.", "info")
                continue

            filepath = dig.batch_path / row["filepath"]
            if not filepath.exists():
                failed += 1
                dig.log(f"Cannot reject {row['filename']}: file not found.", "error")
                continue

            folder_row = dig.db.get_folder_by_item_set(row["item_set_id"])
            folder_name = (folder_row["os_folder_name"] if folder_row
                           else filepath.parent.name)
            dest_dir = dig.rejects_path / folder_name
            dest_dir.mkdir(parents=True, exist_ok=True)

            try:
                shutil.move(str(filepath), str(dest_dir / filepath.name))
            except OSError as exc:
                failed += 1
                dig.log(f"Cannot move {row['filename']}: {exc}", "error")
                continue

            _append_repair_reject_csv(
                dig.catalog_path, filepath,
                [row.get("format") or ""], "Rejected",
            )

            rejected += 1
            affected_folders.add(row["item_set_id"])
            source_dirs[row["item_set_id"]] = filepath.parent
            dig.log(f"  Rejected {row['filename']} -> rejects/{folder_name}/",
                     "success")

        dig.log(
            f"Reject complete: {rejected} moved, {failed} failed, {skipped} skipped.",
            "info",
        )
        dig._collect_and_store_counts()

        for item_set_id in affected_folders:
            dig.validate_folder(item_set_id)

        for folder_dir in source_dirs.values():
            if folder_dir.is_dir() and not any(folder_dir.iterdir()):
                try:
                    folder_dir.rmdir()
                    dig.log(
                        f"ALERT: media folder '{folder_dir.name}' is now "
                        f"empty and was removed.",
                        "warning",
                    )
                except OSError as exc:
                    dig.log(
                        f"Could not remove empty folder {folder_dir.name}: {exc}",
                        "error",
                    )
