"""
Folder Pane

A QTreeWidget listing all Item Set folders in the current batch.
Folder names are coloured green (go), red (no-go), or grey (pending).
Emits ``folder_selected(folder)`` (the folder row) when the user clicks a folder.
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from .status_colors import folder_color


def _folder_color(name_ready: bool, media_ready: bool) -> QColor:
    return QColor(folder_color(name_ready, media_ready))


def folder_key(folder):
    """Stable identity for a folder across rescans.

    Uses item_set_id when present (survives the on-disk rename a scan applies to
    parseable folders); falls back to the on-disk name for folders whose name
    can't be parsed (item_set_id is NULL and they are never renamed).
    """
    isid = folder["item_set_id"]
    return ("id", isid) if isid is not None else ("name", folder["os_folder_name"])


class FolderPane(QTreeWidget):
    """Left pane: batch folder list with go/no-go colour coding."""

    folder_selected = Signal(object)   # emits the folder row (mapping-compatible)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setHeaderLabel("Folders")
        self.setColumnCount(1)
        self.setMinimumWidth(200)
        self.itemClicked.connect(self._on_clicked)

        # Map Qt item identity → folder row (avoids stale row references)
        self._rows_by_item: dict[int, object] = {}   # id(QTreeWidgetItem) → folder

    def load_folders(self, folders):
        """Populate from a sequence of folder rows."""
        self.clear()
        self._rows_by_item.clear()
        for folder in folders:
            item = QTreeWidgetItem([folder["os_folder_name"]])
            color = _folder_color(folder["name_ready"], folder["media_ready"])
            item.setForeground(0, QBrush(color))
            self.addTopLevelItem(item)
            self._rows_by_item[id(item)] = folder

    def select_folder(self, key):
        """Restore the visual selection to the row matching folder_key(...)."""
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            row = self._rows_by_item.get(id(item))
            if row is not None and folder_key(row) == key:
                self.setCurrentItem(item)
                return

    def current_row(self):
        """Folder row of the currently selected item, or None."""
        item = self.currentItem()
        return self._rows_by_item.get(id(item)) if item else None

    def _on_clicked(self, item: QTreeWidgetItem, _column: int):
        row = self._rows_by_item.get(id(item))
        if row is not None:
            self.folder_selected.emit(row)
