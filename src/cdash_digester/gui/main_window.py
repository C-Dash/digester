"""
Main Window

Assembles the splitter layout, menu bar, console dock, and wires all signals
together. Long-running digester operations are dispatched to a Worker thread
(see worker.py) so the UI stays responsive; the application entry point lives
in app.py.
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QUrl, Slot, Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QMainWindow, QMessageBox, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

from ..digester import Digester
from .console_window import ConsoleWindow
from .dialogs import AboutDialog, AssignDialog
from .folder_info_pane import FolderInfoPane
from .folder_pane import FolderPane, folder_key
from .media_table import MediaTablePane
from .thumbnail_pane import ThumbnailPane
from .worker import Worker


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CDASH Presort Digester")
        self.setMinimumSize(1100, 700)

        self.digester:  Optional[Digester] = None
        self.batch_root: Optional[Path]    = None
        self._current_folder = None   # currently selected folder row
        self._worker: Optional[Worker] = None
        # Set by _do_assign on the worker thread, shown by _after_assign on the
        # main thread (dialogs cannot be raised off the main thread).
        self._assign_error: Optional[str] = None
        # media_ids of the loaded folder that Rotate applies to, resolved once
        # per folder load so selection changes don't re-hit the DB.
        self._rotatable_ids: set = set()

        self._build_ui()
        self._build_menus()

    # ------------------------------------------------------------------- UI

    def _build_ui(self):
        outer = QSplitter(Qt.Horizontal)
        self.setCentralWidget(outer)

        self.folder_pane = FolderPane()
        outer.addWidget(self.folder_pane)

        # Right side: vertical splitter with media table, thumbnail pane, and console.
        right = QSplitter(Qt.Vertical)
        outer.addWidget(right)
        # Left pane gets 1 part width, right side gets 3 parts width (1:3 ratio).
        outer.setStretchFactor(0, 1)
        outer.setStretchFactor(1, 3)

        # Top-right slot: a fixed-height folder-info strip above the media table.
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(2)
        self.folder_info = FolderInfoPane()
        self.media_table = MediaTablePane()
        top_layout.addWidget(self.folder_info)
        top_layout.addWidget(self.media_table)
        right.addWidget(top)

        self.thumbnail_pane = ThumbnailPane()
        right.addWidget(self.thumbnail_pane)

        self.console = ConsoleWindow(self)
        right.addWidget(self.console)

        # Right-side panels: media table and thumbnail each get 2/5 height,
        # console gets 1/5.  setStretchFactor governs how *extra* space is shared
        # on resize, but the thumbnail pane is a QScrollArea whose sizeHint is
        # large, so stretch factors alone let it dominate the initial layout.
        # setSizes() pins the initial 2:2:1 proportions explicitly.
        right.setStretchFactor(0, 2)  # media table
        right.setStretchFactor(1, 2)  # thumbnail pane
        right.setStretchFactor(2, 1)  # console
        right.setSizes([280, 280, 140])  # 2 : 2 : 1

        # Bidirectional selection sync
        self.media_table.selection_changed.connect(
            self.thumbnail_pane.highlight_media_ids
        )
        self.thumbnail_pane.selection_changed.connect(
            self.media_table.highlight_media_ids
        )
        # Rotate CW/CCW are grayed out unless the current selection contains
        # at least one rotatable file. highlight_media_ids() (above) doesn't
        # re-emit selection_changed, so exactly one of these two fires per
        # genuine user selection change — both must be connected to catch
        # either source.
        self.media_table.selection_changed.connect(
            lambda _ids: self._sync_actions())
        self.thumbnail_pane.selection_changed.connect(
            lambda _ids: self._sync_actions())
        self.folder_pane.folder_selected.connect(self._on_folder_selected)

    def _build_menus(self):
        mb = self.menuBar()

        # --- Batch ---
        batch = mb.addMenu("Batch")

        self._act_choose = QAction("Choose Batch Folder…", self)
        self._act_choose.triggered.connect(self._choose_batch_folder)
        batch.addAction(self._act_choose)

        self._act_init = QAction("Re-Scan Batch", self)
        self._act_init.triggered.connect(self._initialize_batch)
        self._act_init.setEnabled(False)
        batch.addAction(self._act_init)

        self._act_csv = QAction("Produce CSV Files", self)
        self._act_csv.triggered.connect(self._produce_csv)
        self._act_csv.setEnabled(False)
        batch.addAction(self._act_csv)

        self._act_status = QAction("Write Status to Console", self)
        self._act_status.triggered.connect(self._write_status)
        self._act_status.setEnabled(False)
        batch.addAction(self._act_status)

        self._act_purge_cache = QAction("Purge Validation Caches", self)
        self._act_purge_cache.triggered.connect(self._purge_caches)
        self._act_purge_cache.setEnabled(False)
        batch.addAction(self._act_purge_cache)

        # --- Folder ---
        folder_menu = mb.addMenu("Folder")
        self._act_val_folder = QAction("Rescan Selected Folder", self)
        self._act_val_folder.triggered.connect(self._validate_selected_folder)
        self._act_val_folder.setEnabled(False)
        folder_menu.addAction(self._act_val_folder)

        # --- Media ---
        media_menu = mb.addMenu("Media")
        self._act_assign = QAction("Assign Metadata…", self)
        self._act_assign.triggered.connect(self._assign_metadata)
        self._act_assign.setEnabled(False)
        media_menu.addAction(self._act_assign)

        self._act_repair = QAction("Repair Selected Media", self)
        self._act_repair.triggered.connect(self._repair_selected_media)
        self._act_repair.setEnabled(False)
        media_menu.addAction(self._act_repair)

        self._act_reject = QAction("Reject Selected Media", self)
        self._act_reject.triggered.connect(self._reject_selected_media)
        self._act_reject.setEnabled(False)
        media_menu.addAction(self._act_reject)

        self._act_rotate_cw = QAction("Rotate CW", self)
        self._act_rotate_cw.triggered.connect(
            lambda: self._rotate_selected_media("cw"))
        self._act_rotate_cw.setEnabled(False)
        media_menu.addAction(self._act_rotate_cw)

        self._act_rotate_ccw = QAction("Rotate CCW", self)
        self._act_rotate_ccw.triggered.connect(
            lambda: self._rotate_selected_media("ccw"))
        self._act_rotate_ccw.setEnabled(False)
        media_menu.addAction(self._act_rotate_ccw)

        # --- Digester ---
        digester_menu = mb.addMenu("Digester")

        act_help = QAction("Help", self)
        act_help.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://c-dash.github.io/Documentation/home/")
            )
        )
        digester_menu.addAction(act_help)

        act_github = QAction("GitHub", self)
        act_github.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/C-Dash/digester")
            )
        )
        digester_menu.addAction(act_github)

        act_about = QAction("About…", self)
        act_about.triggered.connect(self._show_about)
        digester_menu.addAction(act_about)

    # ---------------------------------------------------------------- slots

    @Slot()
    def _show_about(self):
        AboutDialog(self).exec()

    @Slot()
    def _choose_batch_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Batch Folder")
        if not folder:
            return
        self.batch_root = Path(folder)
        self.console.clear()
        self.folder_info.clear()
        self.console.append_message(f"Selected: {folder}", "info")
        self.digester = Digester(self.batch_root, self.console.append_message)
        self._run(self._open_and_scan, on_finish=self._after_batch_opened)

    def _open_and_scan(self):
        """Worker-thread half of Choose Batch Folder: open the batch, then scan
        it. Both steps run in one worker so the window stays responsive and
        gated for the whole operation (opening a batch is not cheap)."""
        self.digester.load_or_initialize()
        if self.digester.is_open:
            self.digester.log("Scanning batch…", "info")
            self.digester.scan_batch()

    def _after_batch_opened(self):
        """Main-thread half: wire up the UI for the freshly scanned batch."""
        self._after_load()
        if not self._batch_is_open():
            return              # invalid batch — _after_load logged why
        self._reload_after_folder_scan()

    def _after_load(self):
        if not self._batch_is_open():
            self.console.append_message(
                "Batch load failed — the batch folder must be named "
                "CDB<YYMMDD>-<name> and contain a 'media/' subfolder whose "
                "item-set folders are named [F<index>-]<slug>-OF<ItemSetID>.",
                "error",
            )
            return
        # batch_path may have been renamed during init
        self.batch_root = self.digester.batch_path
        batch = self.digester.get_batch()
        if batch:
            self.setWindowTitle(
                f"CDASH Presort Digester — {batch.batch_id}"
            )
            # Use the Digester's own path property rather than rebuilding it —
            # the on-disk folder is lowercase "catalog", and the hardcoded
            # "Catalog" here only worked because Windows paths are case-
            # insensitive.
            self.console.set_log_path(self.digester.catalog_path / "batch.log")
        # Pane population is left to the caller's _reload_after_folder_scan()
        # so the folder list isn't loaded (and logged) twice on open.
        for act in self._batch_actions():
            act.setEnabled(True)
        self._sync_actions()   # CSV dim until ready; nothing selected yet

    @Slot()
    def _initialize_batch(self):
        self.console.append_message("Starting batch scan…", "info")
        # A full rescan rebuilds every media row, so the media table and
        # thumbnail pane need reloading too — not just the folder pane.
        self._run(self.digester.scan_batch,
                  on_finish=self._reload_after_folder_scan)

    @Slot()
    def _produce_csv(self):
        self.console.append_message("Exporting CSV files…", "info")
        self._run(self.digester.export_csv)

    @Slot()
    def _write_status(self):
        if self.digester:
            self._run(self._log_status_summary)

    def _log_status_summary(self):
        """Worker-thread: push the status summary out through digester.log,
        which Worker has redirected to the console."""
        self.digester.log(self.digester.get_status_summary(), "info")

    @Slot()
    def _purge_caches(self):
        if not self._batch_is_open():
            return
        resp = QMessageBox.question(
            self, "Purge Validation Caches",
            "Clear the folder, place, and file validation caches?\n\n"
            "The next scan will be slower — it re-fetches Omeka folder/place "
            "data and re-screens all files.\n\n",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        self.console.append_message("Purging validation caches…", "info")
        self._run(self.digester.purge_caches)

    @Slot()
    def _validate_selected_folder(self):
        if not self._current_folder:
            return
        item_set_id = self._current_folder.item_set_id
        if item_set_id is None:
            self.console.append_message(
                "Cannot rescan a folder whose name is not in CDASH format.",
                "warning",
            )
            return
        self._run(self.digester.rescan_folder, item_set_id,
                  on_finish=self._reload_after_folder_scan)

    @Slot(object)
    def _on_folder_selected(self, folder):
        self._current_folder = folder
        if not self._batch_is_open():
            return
        self.folder_info.show_folder(folder)
        item_set_id = folder.item_set_id
        rows = (self.digester.get_media_for_folder(item_set_id)
                if item_set_id is not None else [])
        # Resolve rotatability once per folder load; _sync_actions then just
        # intersects the selection against this set.
        self._rotatable_ids = set(
            self.digester.rotatable_media_ids([r.media_id for r in rows])
        ) if rows else set()
        self.media_table.load_media(rows)
        self.thumbnail_pane.load_media(rows, self.batch_root)

    @Slot()
    def _assign_metadata(self):
        media_ids = self.media_table.selected_media_ids()
        if not media_ids:
            QMessageBox.information(self, "No Selection",
                                    "Select one or more media files first.")
            return
        dlg = AssignDialog(len(media_ids), self)
        if dlg.exec() != QDialog.Accepted:
            return

        place_id = dlg.place_id
        if place_id is None and not dlg.place_is_no_change:
            QMessageBox.warning(self, "Invalid Place ID",
                                "Enter a numeric Omeka Place Item ID.")
            return

        # Place validation is a live Omeka HTTP call, so the whole assignment
        # runs on the worker — it used to block the main thread un-gated, and a
        # slow lookup froze the window.
        self._assign_error = None
        self._run(self._do_assign, media_ids, place_id,
                  dlg.doc_type_code, dlg.is_multi_page,
                  on_finish=self._after_assign)

    def _do_assign(self, media_ids, place_id, doc_type_code, is_multi_page):
        """Worker-thread: validate the place, assign metadata, rescan.

        The follow-up rescan happens here rather than as a second _run from
        on_finish — see Worker on why chained _run calls can be refused.
        A validation failure is stashed for _after_assign to show modally,
        since dialogs must be raised on the main thread.
        """
        dig = self.digester
        if place_id is not None:
            status, place_name = dig.validate_place(place_id)
            if "Valid" not in status:
                self._assign_error = status
                dig.log(f"Place validation failed: {status}", "error")
                return
            dig.log(f"Place validated: {place_name} (ID {place_id})", "success")

        if not dig.assign_media_to_doc(media_ids, place_id,
                                       doc_type_code, is_multi_page):
            dig.log("Metadata assignment failed.", "error")
            return

        dig.log("Metadata assigned and files renamed.", "success")
        item_set_id = (self._current_folder.item_set_id
                       if self._current_folder else None)
        if item_set_id is not None:
            # Re-scan the folder so ready/notes reflect the renamed files
            # (assignment itself leaves media status untouched).
            dig.log("Re-scanning folder…", "info")
            dig.rescan_folder(item_set_id)

    def _after_assign(self):
        if self._assign_error:
            QMessageBox.warning(self, "Invalid Place ID", self._assign_error)
            self._assign_error = None
        self._reload_after_folder_scan()

    def _run_media_op(self, eligible_fn, op_fn, *op_args, verb: str,
                      skip_reason: str, detail: str = "",
                      empty_title: str = None, empty_msg: str = None):
        """Shared body for Repair / Reject / Rotate.

        Narrows the current selection to the files the operation applies to,
        reports what was skipped, and dispatches the op to a worker. Passing
        empty_title=None makes the "nothing selected"/"nothing eligible" cases
        silent, for actions whose menu item is already gated on selection.
        """
        media_ids = self.media_table.selected_media_ids()
        if not media_ids:
            if empty_title:
                QMessageBox.information(
                    self, "No Selection",
                    "Select one or more media files first.")
            return

        eligible = eligible_fn(media_ids)
        skipped = len(media_ids) - len(eligible)
        if not eligible:
            if empty_title:
                QMessageBox.information(self, empty_title, empty_msg)
            return

        self.console.append_message(
            f"{verb} {len(eligible)} selected media file(s){detail}...", "info")
        if skipped:
            self.console.append_message(
                f"Skipping {skipped} selected file(s) {skip_reason}.", "info")
        self._run(op_fn, eligible, *op_args,
                  on_finish=self._reload_after_folder_scan)

    @Slot()
    def _repair_selected_media(self):
        if not self._batch_is_open():
            return
        self._run_media_op(
            self.digester.repairable_media_ids,
            self.digester.repair_media_files,
            verb="Repairing",
            skip_reason="with no repair issues",
            empty_title="No Repair Issues",
            empty_msg="None of the selected media files have repair issues.",
        )

    @Slot()
    def _reject_selected_media(self):
        if not self._batch_is_open():
            return
        self._run_media_op(
            self.digester.rejectable_media_ids,
            self.digester.reject_media_files,
            verb="Rejecting",
            skip_reason="not flagged Reject",
            empty_title="No Reject-Flagged Media",
            empty_msg="None of the selected media files are flagged Reject.",
        )

    def _rotate_selected_media(self, direction: str):
        # No dialogs: the Rotate actions are disabled unless the selection
        # holds something rotatable, so both empty cases are unreachable.
        if not self._batch_is_open():
            return
        self._run_media_op(
            self.digester.rotatable_media_ids,
            self.digester.rotate_media_files, direction,
            verb="Rotating",
            detail=f" ({direction})",
            skip_reason="not eligible for rotation",
        )

    # --------------------------------------------------------------- helpers

    def _reload_folders(self):
        if self._batch_is_open():
            folders = self.digester.get_folders()
            self.console.append_message(
                f"Loading {len(folders)} folder(s) into pane.", "info"
            )
            self.folder_pane.load_folders(folders)
            if self._current_folder is not None:
                self.folder_pane.select_folder(folder_key(self._current_folder))

    def _reload_after_folder_scan(self):
        """Refresh folder pane then re-populate media/thumbnail for selected folder."""
        self._reload_folders()
        row = self.folder_pane.current_row()
        if row is not None:
            self._on_folder_selected(row)
        # load_media() clears the table's selection without emitting
        # selection_changed (suppressed during the reset), and batch-ready may
        # have changed, so re-derive both conditional actions explicitly.
        self._sync_actions()

    def _batch_is_open(self) -> bool:
        """True once a batch has been successfully opened."""
        return bool(self.digester and self.digester.is_open)

    def _batch_actions(self):
        """Actions that require an open batch. Excludes Choose Batch Folder
        (needs no batch) and CSV/Rotate (gated on their own conditions)."""
        return (self._act_init, self._act_status, self._act_purge_cache,
                self._act_val_folder, self._act_assign, self._act_repair,
                self._act_reject)

    def _set_busy(self, busy: bool):
        """Enable/disable all DB-touching UI while a worker runs.

        This serializes access to the single shared SQLite connection: while a
        worker is busy the folder pane and the DB-touching menu actions are
        disabled, so the main thread cannot issue a query (folder click) or
        start a synchronous Assign/Repair op that would race the worker.
        See ``Worker`` for the full threading model.
        """
        self.folder_pane.setEnabled(not busy)
        # Choose Batch Folder is the only action that works without an open
        # batch. The rest stay dim until one is open — Choose Batch now routes
        # a failed open through a worker, so lifting the busy gate must not
        # blanket-enable actions that would then run against no batch.
        self._act_choose.setEnabled(not busy)
        batch_open = self._batch_is_open()
        for act in self._batch_actions():
            act.setEnabled(not busy and batch_open)
        # CSV and Rotate are gated on their own conditions rather than
        # blanket-enabled with the rest.
        self._sync_actions(busy)

    def _sync_actions(self, busy: bool = False):
        """Re-derive the conditionally-enabled actions (CSV, Rotate CW/CCW).

        The single place these conditions live, so they cannot drift apart.
        Everything else is blanket-gated by _set_busy; these two additionally
        depend on batch-ready and selection content respectively.
        """
        if busy:
            for act in (self._act_csv,
                        self._act_rotate_cw, self._act_rotate_ccw):
                act.setEnabled(False)
            return

        batch = self.digester.get_batch() if self._batch_is_open() else None
        self._act_csv.setEnabled(bool(batch and batch.ready))

        # Intersect against the rotatable set cached when the folder loaded,
        # rather than re-querying per click: rotatable_media_ids() issues one
        # DB read per id, and this runs on every selection change.
        selected = set(self.media_table.selected_media_ids())
        rotatable = bool(selected & self._rotatable_ids)
        self._act_rotate_cw.setEnabled(rotatable)
        self._act_rotate_ccw.setEnabled(rotatable)

    def _run(self, fn, *args, on_finish=None, **kwargs):
        """Dispatch a digester operation to a background thread.

        ``fn`` is called on the worker thread as ``fn(*args, **kwargs)``. The UI
        is gated busy for the worker's lifetime (see ``Worker``) so no
        main-thread DB access can overlap the worker on the shared connection.
        """
        if self._worker and self._worker.isRunning():
            self.console.append_message(
                "An operation is already running — please wait.", "warning"
            )
            return
        self._worker = Worker(self.digester, fn, *args, **kwargs)
        self._worker.log_message.connect(self.console.append_message)
        self._worker.error.connect(
            lambda e: self.console.append_message(f"ERROR: {e}", "error")
        )
        if on_finish:
            self._worker.done.connect(on_finish)
        # Re-enable the UI when the worker finishes.  Connected after on_finish
        # so the refresh handler (which reads the DB) runs while still "busy",
        # then the gate lifts.
        self._worker.done.connect(lambda: self._set_busy(False))
        self._set_busy(True)
        self._worker.start()
