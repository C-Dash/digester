"""
Media Table Pane

A QTableView showing the media rows for the currently selected folder.
Row background is coloured by go/no-go status.
Emits ``selection_changed(media_ids)`` when the user changes the selection.
Provides ``highlight_media_ids()`` to drive selection from the thumbnail pane
without re-emitting the signal (avoids feedback loops).
"""

from typing import List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QTableView, QWidget

_COLUMNS  = ["filename", "doc_type_code", "page_num", "repair_issues", "ready", "notes"]
_HEADERS  = ["Filename",  "Type",          "Page",    "Repair Issues", "Status", "Notes"]

_READY_DISPLAY = {True: "Ready", False: "Not Ready", None: "—"}

def _row_color(ready) -> QColor:
    if ready is True:
        return QColor("#e8f5e9")
    if ready is False:
        return QColor("#ffebee")
    return QColor("#f5f5f5")


class _MediaModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: List[dict] = []

    def load(self, rows):
        self.layoutAboutToBeChanged.emit()
        self._rows = [dict(r) for r in rows]
        self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            col = _COLUMNS[index.column()]
            val = row[col]
            if col == "ready":
                return _READY_DISPLAY.get(val, "—")
            return "" if val is None else str(val)
        if role == Qt.BackgroundRole:
            return QBrush(_row_color(row["ready"]))
        if role == Qt.UserRole:
            return row["media_id"]
        return None

    def media_id_at(self, row: int) -> int:
        if 0 <= row < len(self._rows):
            return self._rows[row]["media_id"]
        return -1

    def row_for_media_id(self, media_id: int) -> int:
        for i, r in enumerate(self._rows):
            if r["media_id"] == media_id:
                return i
        return -1


class MediaTablePane(QTableView):
    """Top-right pane: tabular view of media files in the selected folder."""

    selection_changed = Signal(list)   # list[int] of media_ids

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._model = _MediaModel()
        self.setModel(self._model)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.horizontalHeader().setStretchLastSection(True)
        self.setAlternatingRowColors(False)
        self._suppress = False
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def load_media(self, rows):
        self._suppress = True
        self._model.load(rows)
        self._suppress = False
        self.resizeColumnsToContents()

    def _on_selection_changed(self, _selected, _deselected):
        if self._suppress:
            return
        ids = [
            self._model.media_id_at(idx.row())
            for idx in self.selectionModel().selectedRows()
        ]
        self.selection_changed.emit(ids)

    def highlight_media_ids(self, media_ids: list):
        """Select rows matching media_ids (called from thumbnail pane)."""
        self._suppress = True
        self.clearSelection()
        for mid in media_ids:
            row = self._model.row_for_media_id(mid)
            if row >= 0:
                self.selectRow(row)
        self._suppress = False

    def selected_media_ids(self) -> List[int]:
        return [
            self._model.media_id_at(idx.row())
            for idx in self.selectionModel().selectedRows()
        ]
