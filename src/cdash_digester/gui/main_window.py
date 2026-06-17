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

from PySide6.QtCore import QLoggingCategory, QThread, Signal, Slot, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QRadioButton, QSplitter, QVBoxLayout, QWidget,
)

from ..cdash_objects import DOC_TYPES
from ..digester import Digester
from .console_window import ConsoleWindow
from .folder_info_pane import FolderInfoPane
from .folder_pane import FolderPane
from .media_table import MediaTablePane
from .thumbnail_pane import ThumbnailPane


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _Worker(QThread):
    """Runs one digester operation off the main thread."""

    log_message = Signal(str, str)   # (message, level)
    done        = Signal()           # avoid shadowing QThread.finished
    error       = Signal(str)

    def __init__(self, digester: Digester, operation: str, **kwargs):
        super().__init__()
        self.digester  = digester
        self.operation = operation
        self.kwargs    = kwargs

    def run(self):
        # Redirect digester log to a cross-thread signal
        original_log = self.digester.log
        self.digester.log = lambda msg, lvl: self.log_message.emit(msg, lvl)
        try:
            op = self.operation
            if op == "load_or_initialize":
                self.digester.load_or_initialize()
            elif op == "scan_batch":
                self.digester.scan_batch()
            elif op == "validate_folder":
                self.digester.validate_folder(self.kwargs["item_set_id"])
            elif op == "export_csv":
                self.digester.export_csv()
            elif op == "status_summary":
                self.log_message.emit(
                    self.digester.get_status_summary(), "info"
                )
            elif op == "repair_media":
                self.digester.repair_media_files(self.kwargs["media_ids"])
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.digester.log = original_log
            self.done.emit()


# ---------------------------------------------------------------------------
# Metadata assignment dialog
# ---------------------------------------------------------------------------

class _AssignDialog(QDialog):
    """Collect place ID, document type, and page-grouping choice."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assign Metadata")
        self.setMinimumWidth(380)

        layout = QFormLayout(self)

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
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CDASH Presort Digester")
        self.setMinimumSize(1100, 700)

        self.digester:  Optional[Digester] = None
        self.batch_root: Optional[Path]    = None
        self._current_item_set_id: Optional[int] = None
        self._worker: Optional[_Worker] = None

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
        self.folder_pane.folder_selected.connect(self._on_folder_selected)

    def _build_menus(self):
        mb = self.menuBar()

        # --- Batch ---
        batch = mb.addMenu("Batch")

        self._act_choose = QAction("Choose Batch Folder…", self)
        self._act_choose.triggered.connect(self._choose_batch_folder)
        batch.addAction(self._act_choose)

        self._act_init = QAction("Initialize / Validate Batch", self)
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

    # ---------------------------------------------------------------- slots

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
        self.digester.load_or_initialize()
        if not self.digester.is_open:
            return                          # invalid batch — errors already logged
        self._after_load()
        self.console.append_message("Scanning batch…", "info")
        self._run("scan_batch", on_finish=self._reload_folders)

    def _after_load(self):
        if not self.digester or not self.digester.is_open:
            self.console.append_message(
                "Batch load failed — check that the folder contains a "
                "'Media/' subfolder with item set folders named "
                "<Mnemonic>-F<OmekaItemSetID>.",
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
            log_path = self.batch_root / "Catalog" / "batch.log"
            self.console.set_log_path(log_path)
        self._reload_folders()
        for act in (self._act_init, self._act_csv, self._act_status,
                    self._act_val_folder, self._act_assign, self._act_repair):
            act.setEnabled(True)

    @Slot()
    def _initialize_batch(self):
        self.console.append_message("Starting batch scan…", "info")
        self._run("scan_batch", on_finish=self._reload_folders)

    @Slot()
    def _produce_csv(self):
        self.console.append_message("Exporting CSV files…", "info")
        self._run("export_csv")

    @Slot()
    def _write_status(self):
        if self.digester:
            self._run("status_summary")

    @Slot()
    def _validate_selected_folder(self):
        if self._current_item_set_id is None:
            return
        self._run("validate_folder",
                  item_set_id=self._current_item_set_id,
                  on_finish=self._reload_after_folder_scan)

    @Slot(int)
    def _on_folder_selected(self, item_set_id: int):
        self._current_item_set_id = item_set_id
        if not self.digester:
            return
        if not self.digester.is_open:
            self.digester.reopen_db()
        if not self.digester.is_open:
            return
        self.folder_info.show_folder(self.digester.get_folder(item_set_id))
        rows = self.digester.get_media_for_folder(item_set_id)
        self.media_table.load_media(rows)
        self.thumbnail_pane.load_media(rows, self.batch_root)

    @Slot()
    def _assign_metadata(self):
        media_ids = self.media_table.selected_media_ids()
        if not media_ids:
            QMessageBox.information(self, "No Selection",
                                    "Select one or more media files first.")
            return
        dlg = _AssignDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        place_id = dlg.place_id
        if place_id is None and not dlg.place_is_no_change:
            QMessageBox.warning(self, "Invalid Place ID",
                                "Enter a numeric Omeka Place Item ID.")
            return

        if place_id is not None:
            status, place_name = self.digester.validate_place(place_id)
            if "Valid" not in status:
                self.console.append_message(
                    f"Place validation failed: {status}", "error"
                )
                QMessageBox.warning(self, "Invalid Place ID", status)
                return
            self.console.append_message(
                f"Place validated: {place_name} (ID {place_id})", "success"
            )
        ok = self.digester.assign_media_to_doc(
            media_ids, place_id, dlg.doc_type_code, dlg.is_multi_page
        )
        if ok:
            self.console.append_message(
                "Metadata assigned and files renamed.", "success"
            )
            self._reload_after_folder_scan()
        else:
            self.console.append_message(
                "Metadata assignment failed.", "error"
            )

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
        self._run("repair_media", on_finish=self._reload_after_folder_scan,
                  media_ids=repairable_ids)

    # --------------------------------------------------------------- helpers

    def _reload_folders(self):
        if self.digester:
            self.digester.reopen_db()
        if self.digester and self.digester.is_open:
            folders = self.digester.get_folders()
            self.console.append_message(
                f"Loading {len(folders)} folder(s) into pane.", "info"
            )
            self.folder_pane.load_folders(folders)
            if self._current_item_set_id is not None:
                self.folder_pane.select_folder(self._current_item_set_id)

    def _reload_after_folder_scan(self):
        """Refresh folder pane then re-populate media/thumbnail for selected folder."""
        self._reload_folders()
        if self._current_item_set_id is not None:
            self._on_folder_selected(self._current_item_set_id)

    def _run(self, operation: str, on_finish=None, **kwargs):
        """Dispatch a digester operation to a background thread."""
        if self._worker and self._worker.isRunning():
            self.console.append_message(
                "An operation is already running — please wait.", "warning"
            )
            return
        self._worker = _Worker(self.digester, operation, **kwargs)
        self._worker.log_message.connect(self.console.append_message)
        self._worker.error.connect(
            lambda e: self.console.append_message(f"ERROR: {e}", "error")
        )
        if on_finish:
            self._worker.done.connect(on_finish)
        self._worker.start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    QLoggingCategory.setFilterRules("qt.gui.imageio=false")
    app = QApplication(sys.argv)
    app.setApplicationName("CDASH Presort Digester")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
