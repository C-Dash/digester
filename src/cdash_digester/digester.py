"""
CDASH Digester Module  (digester.py)

The Digester is the session facade and controller. It owns the batch state
(paths, the open BatchDB, the validator, and the log callback) and delegates the
real work to focused services in cdash_digester.services:

  ScanService           — batch/folder/media scan pipeline (+ FolderScanner)
  ValidationService     — folder/place validation with the persistent caches
  ScreeningService      — prescreener results with the persistent file cache
  AssignmentService     — interactive metadata assignment
  RepairService         — media repair
  CatalogExportService  — CSV export

Services do not see this facade. They share a `Session` (session.py) holding the
batch paths, the open database, the log sink, the Omeka validator and the batch
counts, and receive any sibling services they need as explicit constructor
arguments. `log` and `db` are read live from the Session, so the GUI worker's
runtime reassignment of `digester.log` still reaches every service.

The Digester's own `batch_path`/`log`/`db`/paths are properties over the
Session, so existing callers (GUI, tests, CLI) are unaffected.

The GUI runs Digester methods on a QThread worker so the UI stays responsive.

CLI test harness
----------------
  python -m cdash_digester.digester
Copies CDB260430-Test_batch to a temp folder, scans it, and prints the status
summary.  The GUI is not launched.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .cdash_objects import BatchDB
from .naming import parse_batch_name
from .session import Session
from .validator import CDASHValidator
from .services import (
    ScanService, ValidationService, ScreeningService,
    AssignmentService, RepairService, RejectService, RotateService,
    CatalogExportService,
)


class Digester:
    """Session facade: owns batch state and delegates to the service layer."""

    def __init__(self, batch_path: Path,
                 log: Callable[[str, str], None] = None):
        self._session = Session(batch_path, log, validator=CDASHValidator())

        # Services share the Session, not this facade, and receive their
        # sibling services explicitly — so each one's dependencies are
        # visible here rather than discovered via dig._private attributes.
        self._validation = ValidationService(self._session)
        self._screening = ScreeningService(self._session)
        self._scan = ScanService(self._session, self._validation,
                                 self._screening)
        self._assignment = AssignmentService(self._session, self._validation)
        self._repair = RepairService(self._session, self._scan)
        self._reject = RejectService(self._session, self._scan)
        self._rotate = RotateService(self._session, self._scan)
        self._export = CatalogExportService(self._session)

    # ------------------------------------------------- session passthroughs
    # The GUI and tests treat these as Digester state; the Session owns them.

    @property
    def batch_path(self) -> Path:
        return self._session.batch_path

    @property
    def log(self):
        return self._session.log

    @log.setter
    def log(self, fn):
        # The GUI worker swaps this at runtime to redirect output to a signal;
        # services read session.log live, so the swap reaches all of them.
        self._session.log = fn or (lambda msg, lvl: None)

    @property
    def db(self) -> Optional[BatchDB]:
        return self._session.db

    @db.setter
    def db(self, value):
        self._session.db = value

    @property
    def _validator(self):
        return self._session.validator

    @_validator.setter
    def _validator(self, value):
        self._session.validator = value

    # ------------------------------------------------------------------ paths

    @property
    def catalog_path(self) -> Path:
        return self._session.catalog_path

    @property
    def media_path(self) -> Path:
        return self._session.media_path

    @property
    def rejects_path(self) -> Path:
        return self._session.rejects_path

    @property
    def db_path(self) -> Path:
        return self._session.db_path

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

    # ------------------------------------------------- delegating operations

    def scan_batch(self):
        """Full batch scan: rebuild working tables, scan all folders and media."""
        self._scan.scan_batch()

    def purge_caches(self):
        """Empty the three persistent validation/screening caches.

        The next scan/rescan re-fetches Omeka folder/place data and re-screens
        all files instead of short-circuiting from cache.
        """
        if not self.db:
            self.log("No batch open.", "error")
            return
        n = self.db.clear_caches()
        self.log(f"Purged {n} cached validation/screening entr"
                 f"{'y' if n == 1 else 'ies'}.", "info")

    def rescan_folder(self, item_set_id: int):
        """Re-scan a single folder (Folder → Rescan Selected Folder).

        Named for what it does: this does not validate anything. (Omeka
        folder validation is CDASHValidator.validate_folder, a different
        method on a different class.)"""
        self._scan.rescan_folder(item_set_id)

    def validate_place(self, place_id: int) -> Tuple[str, str]:
        """Validate a place ID, register in DB if valid. Returns (status, name)."""
        return self._validation.ensure_place(place_id)

    def assign_media_to_doc(self, media_ids: List[int], place_id: int,
                            doc_type_code: str, is_multi_page: bool) -> bool:
        """Assign selected media files to a new document. Returns True on success."""
        return self._assignment.assign_media_to_doc(
            media_ids, place_id, doc_type_code, is_multi_page)

    def repair_media_files(self, media_ids: List[int]):
        """Repair selected media files that have recorded repair issues."""
        self._repair.repair_media_files(media_ids)

    def repairable_media_ids(self, media_ids: List[int]) -> List[int]:
        """Subset of media_ids that have recorded repair issues."""
        return self._repair.repairable_media_ids(media_ids)

    def reject_media_files(self, media_ids: List[int]):
        """Move selected Reject-flagged media files to the rejects/ archive."""
        self._reject.reject_media_files(media_ids)

    def rejectable_media_ids(self, media_ids: List[int]) -> List[int]:
        """Subset of media_ids flagged Reject."""
        return self._reject.rejectable_media_ids(media_ids)

    def rotate_media_files(self, media_ids: List[int], direction: str):
        """Rotate selected media files 90 degrees ("cw" or "ccw")."""
        self._rotate.rotate_media_files(media_ids, direction)

    def rotatable_media_ids(self, media_ids: List[int]) -> List[int]:
        """Subset of media_ids eligible for rotation (JPEG/TIFF, not Reject)."""
        return self._rotate.rotatable_media_ids(media_ids)

    def export_csv(self):
        """Export batch.csv, folder.csv, place.csv, document.csv, media.csv."""
        self._export.export_csv()

    # ----------------------------------------------------------- queries (GUI)

    @property
    def is_open(self) -> bool:
        """True once a batch DB is open (after load_or_initialize)."""
        return self.db is not None

    def get_batch(self):
        return self.db.get_batch() if self.db else None

    def get_folders(self) -> list:
        return self.db.get_folders() if self.db else []

    def get_folder(self, item_set_id: int):
        return self.db.get_folder_by_item_set(item_set_id) if self.db else None

    def get_media(self, media_id: int):
        return self.db.get_media(media_id) if self.db else None

    def get_media_for_folder(self, item_set_id: int) -> list:
        return self.db.get_media_for_folder(item_set_id) if self.db else []

    # --------------------------------------------------------------- counts

    def _collect_and_store_counts(self) -> dict:
        return self._session.collect_and_store_counts()

    @staticmethod
    def _counts_summary(counts: dict) -> str:
        return Session.counts_summary(counts)

    # ----------------------------------------------------------------- status

    def get_status_summary(self) -> str:
        """Render the batch status report. Read-only.

        This used to call _collect_and_store_counts(), so asking for a status
        summary wrote to the database — a query with a side effect. It now
        computes the counts without persisting them.
        """
        if not self.db:
            return "No batch open."
        batch = self.db.get_batch()
        if not batch:
            return "No batch record in DB."
        folders = self.db.get_folders()
        n_ready = sum(1 for f in folders if f.name_ready and f.media_ready)
        lines = [
            f"Batch:   {batch.batch_id}  ready={batch.ready}",
            f"Folders: {n_ready}/{len(folders)} ready",
            f"Rejected: {batch.rejected_count}",
            "",
        ]
        for f in folders:
            lines.append(
                f"  {f.os_folder_name:<45s}"
                f"  name={'ok' if f.name_ready else 'NO':2s}"
                f"  media={'ok' if f.media_ready else 'NO'}"
            )
        counts = self._session.compute_counts()
        return "\n".join(lines) + "\n" + self._counts_summary(counts)

    # ------------------------------------------------------------------ misc

    def close(self):
        self._validator.close()
        if self.db:
            self.db.close()


# ---------------------------------------------------------------------------
# CLI test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import shutil

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
