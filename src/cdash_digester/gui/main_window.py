"""
Main Window

Entry point for the CDASH Presort Digester GUI.  Assembles the splitter
layout, menu bar, console dock, and wires all signals together.

Long-running digester operations are dispatched to a QThread so the UI
stays responsive.  The worker redirects the digester's log callback to a
Qt signal, which Qt routes safely back to the main thread.
"""

import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QLoggingCategory, QThread, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QRadioButton, QSplitter, QVBoxLayout, QWidget,
)

from .. import __build_date__, __version__
from ..cdash_objects import DOC_TYPES
from ..digester import Digester
from .console_window import ConsoleWindow
from .folder_info_pane import FolderInfoPane
from .folder_pane import FolderPane, folder_key
from .media_table import MediaTablePane
from .thumbnail_pane import ThumbnailPane


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _Worker(QThread):
    """Runs one digester operation off the main thread.

    The operation is a bound callable plus its arguments, so there is no op-name
    string to typo and no dispatch table to keep in sync. A multi-step operation
    should be a single callable that performs every step (see
    ``MainWindow._open_and_scan``) rather than two chained ``_run`` calls: the
    ``done`` signal fires from inside ``run()``, so a second ``_run`` invoked
    from an ``on_finish`` handler can still see ``isRunning()`` and be refused.

    Threading model
    ---------------
    The Digester holds a single SQLite connection shared between this worker
    thread and the GUI main thread (the connection is opened with
    ``check_same_thread=False``).  Safety depends on that connection never
    being touched concurrently from both threads.  This is enforced by the
    main window, not by a lock in the persistence layer:

      * Long-running operations run here, on the worker thread.
      * Main-thread DB reads (folder clicks) and the synchronous Assign/Repair
        handlers run only when the window is idle.
      * ``MainWindow._run`` gates the UI busy (``_set_busy``) for the lifetime
        of a worker, so the main thread cannot issue a query or start another
        operation until ``done`` fires.  The two therefore never overlap.
    """

    log_message = Signal(str, str)   # (message, level)
    done        = Signal()           # avoid shadowing QThread.finished
    error       = Signal(str)

    def __init__(self, digester: Digester, fn, *args, **kwargs):
        super().__init__()
        self.digester = digester
        self._fn      = fn
        self._args    = args
        self._kwargs  = kwargs

    def run(self):
        # Redirect digester log to a cross-thread signal. Anything the callable
        # logs via digester.log — including nested service calls — reaches the
        # console this way, which is why worker-side work should log rather
        # than return values.
        original_log = self.digester.log
        self.digester.log = lambda msg, lvl: self.log_message.emit(msg, lvl)
        try:
            self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.digester.log = original_log
            self.done.emit()


# ---------------------------------------------------------------------------
# Metadata assignment dialog
# ---------------------------------------------------------------------------

class _AssignDialog(QDialog):
    """Collect place ID, document type, and page-grouping choice."""

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assign Metadata")
        self.setMinimumWidth(380)

        layout = QFormLayout(self)

        header = QLabel(f"{count} media file{'s' if count != 1 else ''} selected")
        header.setStyleSheet("font-weight: bold;")
        layout.addRow(header)

        self._place_edit = QLineEdit()
        self._place_edit.setPlaceholderText("No Change")
        layout.addRow("Place ID:", self._place_edit)

        self._type_combo = QComboBox()
        self._type_combo.addItem("— No Change —", None)
        for code, desc in DOC_TYPES.items():
            self._type_combo.addItem(f"{code} — {desc}", code)
        layout.addRow("Document Type:", self._type_combo)

        layout.addRow(QLabel("Grouping:"))
        self._multi  = QRadioButton("All files are pages of one document")
        self._single = QRadioButton("Each file is a separate single-page document")
        self._multi.setChecked(True)
        layout.addRow("", self._multi)
        layout.addRow("", self._single)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @property
    def place_is_no_change(self) -> bool:
        return self._place_edit.text().strip() == ""

    @property
    def place_id(self) -> Optional[int]:
        try:
            return int(self._place_edit.text().strip())
        except ValueError:
            return None

    @property
    def doc_type_code(self) -> str:
        return self._type_combo.currentData()

    @property
    def is_multi_page(self) -> bool:
        return self._multi.isChecked()


# ---------------------------------------------------------------------------
# About dialog
# ---------------------------------------------------------------------------

class _AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About CDASH Presort Digester")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        title = QLabel("<b>CDASH Presort Digester</b>")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"Version: {__version__}"))
        layout.addWidget(QLabel(f"Build date: {__build_date__}"))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


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
        self._worker: Optional[_Worker] = None
        # Set by _do_assign on the worker thread, shown by _after_assign on the
        # main thread (dialogs cannot be raised off the main thread).
        self._assign_error: Optional[str] = None

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
        self.media_table.selection_changed.connect(self._sync_rotate_enabled)
        self.thumbnail_pane.selection_changed.connect(self._sync_rotate_enabled)
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
        _AboutDialog(self).exec()

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
        if not self.digester or not self.digester.is_open:
            return              # invalid batch — _after_load logged why
        self._reload_after_folder_scan()

    def _after_load(self):
        if not self.digester or not self.digester.is_open:
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
                f"CDASH Presort Digester — {batch['batch_id']}"
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
        self._sync_csv_enabled()   # CSV stays dim until the batch is ready
        self._sync_rotate_enabled([])   # no selection yet on a fresh load

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
        which _Worker has redirected to the console."""
        self.digester.log(self.digester.get_status_summary(), "info")

    @Slot()
    def _purge_caches(self):
        if not self.digester or not self.digester.is_open:
            return
        resp = QMessageBox.question(
            self, "Purge Validation Caches",
            "Clear the folder, place, and file validation caches?\n\n"
            "The next scan will be slower — it re-fetches Omeka folder/place "
            "data and re-screens all files.\n\n"
            "WARNING: the folder cache also allocates folder index numbers. "
            "Purging it restarts numbering from F1, so the next scan may "
            "renumber and RENAME your media folders on disk.",
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
        item_set_id = self._current_folder["item_set_id"]
        if item_set_id is None:
            self.console.append_message(
                "Cannot rescan a folder whose name is not in CDASH format.",
                "warning",
            )
            return
        self._run(self.digester.validate_folder, item_set_id,
                  on_finish=self._reload_after_folder_scan)

    @Slot(object)
    def _on_folder_selected(self, folder):
        self._current_folder = folder
        if not self.digester or not self.digester.is_open:
            return
        self.folder_info.show_folder(folder)
        item_set_id = folder["item_set_id"]
        rows = (self.digester.get_media_for_folder(item_set_id)
                if item_set_id is not None else [])
        self.media_table.load_media(rows)
        self.thumbnail_pane.load_media(rows, self.batch_root)

    @Slot()
    def _assign_metadata(self):
        media_ids = self.media_table.selected_media_ids()
        if not media_ids:
            QMessageBox.information(self, "No Selection",
                                    "Select one or more media files first.")
            return
        dlg = _AssignDialog(len(media_ids), self)
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
        on_finish — see _Worker on why chained _run calls can be refused.
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
        item_set_id = (self._current_folder["item_set_id"]
                       if self._current_folder else None)
        if item_set_id is not None:
            # Re-scan the folder so ready/notes reflect the renamed files
            # (assignment itself leaves media status untouched).
            dig.log("Re-scanning folder…", "info")
            dig.validate_folder(item_set_id)

    def _after_assign(self):
        if self._assign_error:
            QMessageBox.warning(self, "Invalid Place ID", self._assign_error)
            self._assign_error = None
        self._reload_after_folder_scan()

    @Slot()
    def _repair_selected_media(self):
        if not self.digester or not self.digester.is_open:
            return

        media_ids = self.media_table.selected_media_ids()
        if not media_ids:
            QMessageBox.information(
                self,
                "No Selection",
                "Select one or more media files first.",
            )
            return

        repairable_ids = self.digester.repairable_media_ids(media_ids)
        skipped = len(media_ids) - len(repairable_ids)

        if not repairable_ids:
            QMessageBox.information(
                self,
                "No Repair Issues",
                "None of the selected media files have repair issues.",
            )
            return

        self.console.append_message(
            f"Repairing {len(repairable_ids)} selected media file(s)...",
            "info",
        )
        if skipped:
            self.console.append_message(
                f"Skipping {skipped} selected file(s) with no repair issues.",
                "info",
            )
        self._run(self.digester.repair_media_files, repairable_ids,
                  on_finish=self._reload_after_folder_scan)

    @Slot()
    def _reject_selected_media(self):
        if not self.digester or not self.digester.is_open:
            return

        media_ids = self.media_table.selected_media_ids()
        if not media_ids:
            QMessageBox.information(
                self,
                "No Selection",
                "Select one or more media files first.",
            )
            return

        rejectable_ids = self.digester.rejectable_media_ids(media_ids)
        skipped = len(media_ids) - len(rejectable_ids)

        if not rejectable_ids:
            QMessageBox.information(
                self,
                "No Reject-Flagged Media",
                "None of the selected media files are flagged Reject.",
            )
            return

        self.console.append_message(
            f"Rejecting {len(rejectable_ids)} selected media file(s)...",
            "info",
        )
        if skipped:
            self.console.append_message(
                f"Skipping {skipped} selected file(s) not flagged Reject.",
                "info",
            )
        self._run(self.digester.reject_media_files, rejectable_ids,
                  on_finish=self._reload_after_folder_scan)

    def _rotate_selected_media(self, direction: str):
        if not self.digester or not self.digester.is_open:
            return

        media_ids = self.media_table.selected_media_ids()
        if not media_ids:
            return   # menu action is disabled in this case; defensive no-op

        rotatable_ids = self.digester.rotatable_media_ids(media_ids)
        skipped = len(media_ids) - len(rotatable_ids)

        if not rotatable_ids:
            return   # same — shouldn't be reachable while the action is enabled

        self.console.append_message(
            f"Rotating {len(rotatable_ids)} selected media file(s) "
            f"({direction})...",
            "info",
        )
        if skipped:
            self.console.append_message(
                f"Skipping {skipped} selected file(s) not eligible for rotation.",
                "info",
            )
        self._run(self.digester.rotate_media_files, rotatable_ids, direction,
                  on_finish=self._reload_after_folder_scan)

    # --------------------------------------------------------------- helpers

    def _reload_folders(self):
        if self.digester and self.digester.is_open:
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
        # Assign Metadata runs synchronously (no worker → no _set_busy), so
        # re-derive the CSV action here in case batch ready changed.
        self._sync_csv_enabled()
        # load_media() clears the table's selection without emitting
        # selection_changed (suppressed during the reset), so Rotate's
        # enabled state needs an explicit re-derive here too.
        self._sync_rotate_enabled([])

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
        See ``_Worker`` for the full threading model.
        """
        self.folder_pane.setEnabled(not busy)
        # Choose Batch Folder is the only action that works without an open
        # batch. The rest stay dim until one is open — Choose Batch now routes
        # a failed open through a worker, so lifting the busy gate must not
        # blanket-enable actions that would then run against no batch.
        self._act_choose.setEnabled(not busy)
        batch_open = bool(self.digester and self.digester.is_open)
        for act in self._batch_actions():
            act.setEnabled(not busy and batch_open)
        # CSV and Rotate are special: disabled while busy, and otherwise only
        # re-enabled based on their own condition (batch ready / selection
        # content) rather than blanket-enabled with the rest.
        if busy:
            self._act_csv.setEnabled(False)
            self._act_rotate_cw.setEnabled(False)
            self._act_rotate_ccw.setEnabled(False)
        else:
            self._sync_csv_enabled()
            self._sync_rotate_enabled(self.media_table.selected_media_ids())

    def _sync_csv_enabled(self):
        """Enable 'Produce CSV Files' only when the batch ready status is True."""
        batch = (self.digester.get_batch()
                 if self.digester and self.digester.is_open else None)
        self._act_csv.setEnabled(bool(batch and batch["ready"]))

    def _sync_rotate_enabled(self, media_ids: List[int]):
        """Enable Rotate CW/CCW only when the selection has at least one
        rotatable file (JPEG/TIFF, not flagged Reject)."""
        rotatable = bool(
            self.digester and self.digester.is_open
            and media_ids and self.digester.rotatable_media_ids(media_ids)
        )
        self._act_rotate_cw.setEnabled(rotatable)
        self._act_rotate_ccw.setEnabled(rotatable)

    def _run(self, fn, *args, on_finish=None, **kwargs):
        """Dispatch a digester operation to a background thread.

        ``fn`` is called on the worker thread as ``fn(*args, **kwargs)``. The UI
        is gated busy for the worker's lifetime (see ``_Worker``) so no
        main-thread DB access can overlap the worker on the shared connection.
        """
        if self._worker and self._worker.isRunning():
            self.console.append_message(
                "An operation is already running — please wait.", "warning"
            )
            return
        self._worker = _Worker(self.digester, fn, *args, **kwargs)
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _apply_light_theme(app):
    """Force a light palette regardless of the OS theme.

    The app's colors are all chosen for a light background. Fusion honours the
    custom palette; the native Windows style follows system dark mode and would
    ignore it (which is what made the console/thumbnail text unreadable).
    """
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor("#f0f0f0"))
    pal.setColor(QPalette.WindowText,      QColor("#000000"))
    pal.setColor(QPalette.Base,            QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase,   QColor("#f5f5f5"))
    pal.setColor(QPalette.Text,            QColor("#000000"))
    pal.setColor(QPalette.Button,          QColor("#f0f0f0"))
    pal.setColor(QPalette.ButtonText,      QColor("#000000"))
    pal.setColor(QPalette.ToolTipBase,     QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipText,     QColor("#000000"))
    pal.setColor(QPalette.Highlight,       QColor("#3399ff"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.PlaceholderText, QColor("#777777"))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#a0a0a0"))
    app.setPalette(pal)


def main():
    # When running as a PyInstaller bundle, prepend the bundle directory so
    # ExifTool (bundled alongside the executable) is found on PATH.
    if getattr(sys, "frozen", False):
        import os
        # sys._MEIPASS is the _internal/ folder where bundled binaries land.
        _bundle_dir = getattr(sys, "_MEIPASS", str(Path(sys.executable).parent))
        os.environ["PATH"] = _bundle_dir + os.pathsep + os.environ.get("PATH", "")

    QLoggingCategory.setFilterRules("qt.gui.imageio=false")
    app = QApplication(sys.argv)
    _apply_light_theme(app)
    app.setApplicationName("CDASH Presort Digester")
    _icon_path = Path(__file__).parent / "assets" / "icon.ico"
    if _icon_path.exists():
        app.setWindowIcon(QIcon(str(_icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
