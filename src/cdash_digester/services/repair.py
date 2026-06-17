"""RepairService — apply repairs to media files with recorded repair issues.

Extracted from Digester.repair_media_files. Logic preserved verbatim.
"""

from typing import List

from ..repair_media import parse_repair_issues, repair_file


class RepairService:
    def __init__(self, dig):
        self._dig = dig

    def repairable_media_ids(self, media_ids: List[int]) -> List[int]:
        """Return the subset of media_ids that have recorded repair issues,
        using the canonical parse_repair_issues check."""
        db = self._dig.db
        if not db:
            return []
        out = []
        for media_id in media_ids:
            row = db.get_media(media_id)
            if row and parse_repair_issues(row.get("repair_issues")):
                out.append(media_id)
        return out

    def repair_media_files(self, media_ids: List[int]):
        """Repair selected media files that have recorded repair issues.

        Each repaired file is copied back over its original, then every
        affected folder is rescanned so the DB reflects the corrected files.
        """
        dig = self._dig
        if not dig.db:
            dig.log("No open batch.", "error")
            return

        repaired = 0
        failed = 0
        skipped = 0
        affected_folders: set = set()

        for media_id in media_ids:
            row = dig.db.get_media(media_id)
            if not row:
                skipped += 1
                dig.log(f"Media ID {media_id} not found.", "warning")
                continue

            issues = parse_repair_issues(row.get("repair_issues"))
            if not issues:
                skipped += 1
                dig.log(f"Skipping {row['filename']}: no repair issues.", "info")
                continue

            filepath = dig.batch_path / row["filepath"]
            if not filepath.exists():
                failed += 1
                dig.log(f"Cannot repair {row['filename']}: file not found.", "error")
                continue

            dig.log(f"Repairing {row['filename']} ({', '.join(issues)})…", "info")
            success, message = repair_file(filepath, issues, catalog_path=dig.catalog_path)
            if success:
                repaired += 1
                affected_folders.add(row["item_set_id"])
                dig.log(f"  {message}", "success")
            else:
                failed += 1
                dig.log(f"  {message}", "error")

        dig.log(
            f"Repair complete: {repaired} repaired, {failed} failed, {skipped} skipped.",
            "info",
        )
        counts = dig._collect_and_store_counts()
        dig.log(dig._counts_summary(counts), "info")

        for item_set_id in affected_folders:
            dig.validate_folder(item_set_id)
