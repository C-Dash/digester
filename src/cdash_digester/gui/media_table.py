"""
Media Table Pane

A QTableView showing the media rows for the currently selected folder.
The Status column's text is coloured by go/no-go status; row backgrounds are
left to the Windows theme.
Emits ``selection_changed(media_ids)`` when the user changes the selection.
Provides ``highlight_media_ids()`` to drive selection from the thumbnail pane
without re-emitting the signal (avoids feedback loops).
"""

from typing import List

from ..models import MediaWithDoc

from PySide6.QtCore import (
    QAbstractTableModel, QItemSelection, QItemSelectionModel, QModelIndex,
    Qt, Signal,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QMenu, QTableView, QWidget,
)

from .status_colors import status_color, status_text

_COLUMNS  = ["filename", "ready", "repair_issues",  "filename_issues", "doc_type_code", "page_num", "format", "format_issues" ]
_HEADERS  = ["Filename",  "Status",  "Repair Issues",   "Name Issues", "Type",          "Page",    "Format", "Format Notes"]


class _MediaModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: List[MediaWithDoc] = []

    def load(self, rows):
        # A wholesale data replacement is a reset, not a layout change: a reset
        # invalidates the selection model's stored indexes so a previous
        # folder's selection cannot carry over to the new rows.
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

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
            # _COLUMNS names entity fields, so this is a dynamic attribute read.
            val = getattr(row, col)
            if col == "ready":
                return status_text(val)
            return "" if val is None else str(val)
        if role == Qt.ForegroundRole and _COLUMNS[index.column()] == "ready":
            return QBrush(QColor(status_color(row.ready)))
        if role == Qt.UserRole:
            return row.media_id
        return None

    def media_id_at(self, row: int) -> int:
        if 0 <= row < len(self._rows):
            return self._rows[row].media_id
        return -1

    def row_for_media_id(self, media_id: int) -> int:
        for i, r in enumerate(self._rows):
            if r.media_id == media_id:
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
        # Inset cell content so the first character clears the Windows 11
        # selection accent bar on the left edge of selected cells.
        self.setStyleSheet("QTableView::item { padding-left: 5px; padding-right: 4px; }")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._suppress = False
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def load_media(self, rows):
        self._suppress = True
        self._model.load(rows)
        self.clearSelection()   # don't carry a prior folder's selection over
        self._suppress = False
        self.resizeColumnsToContents()

    def _on_selection_changed(self, _selected, _deselected):
        if self._suppress:
            return
        self.selection_changed.emit(self.selected_media_ids())

    def highlight_media_ids(self, media_ids: list):
        """Select rows matching media_ids (called from thumbnail pane)."""
        self._suppress = True
        last_col = self._model.columnCount() - 1
        selection = QItemSelection()
        for mid in media_ids:
            row = self._model.row_for_media_id(mid)
            if row >= 0:
                selection.select(self._model.index(row, 0),
                                 self._model.index(row, last_col))
        self.selectionModel().select(
            selection,
            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
        )
        self._suppress = False

    def selected_media_ids(self) -> List[int]:
        return [
            self._model.media_id_at(idx.row())
            for idx in self.selectionModel().selectedRows()
        ]

    def _show_context_menu(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return
        menu = QMenu(self)
        act_copy = menu.addAction("Copy Cell")
        if menu.exec(self.viewport().mapToGlobal(pos)) is act_copy:
            self._copy_cell(index)

    @staticmethod
    def _copy_cell(index):
        """Copy a cell's displayed text to the system clipboard."""
        QApplication.clipboard().setText(index.data(Qt.DisplayRole) or "")
