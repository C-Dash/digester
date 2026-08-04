"""
Session — the per-batch context shared by every service.

Services used to hold a reference to the Digester facade and reach back through
it, including into its private surface (`dig._collect_and_store_counts()`,
`dig._validation`, `dig._validator`). That made Digester unable to change its
internals without breaking services, and made services impossible to construct
in a test without building a whole Digester.

A Session owns only what services legitimately share: the batch paths, the open
database, the log sink, the Omeka validator, and the batch-level counts. Sibling
services are passed explicitly to the services that need them, so each service's
dependencies are visible in its constructor.

`log` and `db` are plain attributes read live on every use, so the GUI worker's
runtime reassignment of the log sink still reaches every service.
"""

import csv
from pathlib import Path
from typing import Callable, Optional

from .repair_media import (
    REPAIR_REJECT_ACTION_REJECTED, REPAIR_REJECT_ACTION_REPAIRED_PREFIX,
)


class Session:
    """Batch state shared across the service layer."""

    def __init__(self, batch_path: Path,
                 log: Callable[[str, str], None] = None,
                 validator=None):
        self.batch_path = Path(batch_path)
        self.log = log or (lambda msg, lvl: None)
        self.db = None
        self.validator = validator

    # ------------------------------------------------------------------ paths

    @property
    def catalog_path(self) -> Path:
        return self.batch_path / "catalog"

    @property
    def media_path(self) -> Path:
        return self.batch_path / "media"

    @property
    def rejects_path(self) -> Path:
        return self.batch_path / "rejects"

    @property
    def db_path(self) -> Path:
        return self.catalog_path / "batch_db.sqlite"

    # --------------------------------------------------------------- counts

    def read_repair_reject_csv_counts(self) -> tuple:
        """Return (rejected_rows, repaired_rows) from catalog/repair_reject.csv.

        repair_reject.csv is the shared log for repair attempts, repair
        refusals, rotations, and reject moves, so neither count is simply the
        row total — each is matched against the Repair_Action string its own
        producer writes ("Rejected" from RejectService, "Repaired: …" from
        repair_media). Rows for refusals and rotations count as neither.
        """
        csv_path = self.catalog_path / "repair_reject.csv"
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            rejected = sum(
                1 for r in rows
                if r.get("Repair_Action", "") == REPAIR_REJECT_ACTION_REJECTED
            )
            repaired = sum(
                1 for r in rows
                if r.get("Repair_Action", "").startswith(
                    REPAIR_REJECT_ACTION_REPAIRED_PREFIX)
            )
            return rejected, repaired
        except FileNotFoundError:
            return 0, 0

    def compute_counts(self) -> dict:
        """Return all six counts. Pure read — nothing is written.

        Split from collect_and_store_counts so a query (get_status_summary)
        can report fresh numbers without the side effect of persisting them.
        """
        stats = self.db.count_batch_stats()
        rejects, repaired = self.read_repair_reject_csv_counts()
        stats["rejects"] = rejects
        stats["repaired"] = repaired
        return stats

    def store_counts(self, counts: dict):
        """Persist the six counts to the batch row."""
        self.db.update_batch_counts(
            folders=counts["folders"], places=counts["places"],
            documents=counts["documents"], media=counts["media"],
            rejects=counts["rejects"], repaired=counts["repaired"],
        )

    def collect_and_store_counts(self) -> dict:
        """Compute all six counts, persist to the batch row, and return them.

        For use by commands (scan, repair, reject, rotate, export). Queries
        should call compute_counts() instead.
        """
        counts = self.compute_counts()
        self.store_counts(counts)
        return counts

    @staticmethod
    def counts_summary(counts: dict) -> str:
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
