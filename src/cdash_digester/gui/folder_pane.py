"""
Folder Pane

A QTreeWidget listing all Item Set folders in the current batch.
Folder names are coloured green (go), red (no-go), or grey (pending).
Emits ``folder_selected(item_set_id)`` when the user clicks a folder.
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

def _folder_color(name_ready: bool, media_ready: bool) -> QColor:
    if name_ready and media_ready:
        return QColor("#2d8a2d")   # green — fully ready
    if name_ready is False or media_ready is False:
        return QColor("#cc0000")   # red — not ready
    return QColor("#888888")       # grey — pending / unseen


class FolderPane(QTreeWidget):
    """Left pane: batch folder list with go/no-go colour coding."""

    folder_selected = Signal(int)   # emits item_set_id

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setHeaderLabel("Folders")
        self.setColumnCount(1)
        self.setMinimumWidth(200)
        self.itemClicked.connect(self._on_clicked)

        # Map Qt item identity → item_set_id (avoids stale row references)
        self._id_map: dict[int, int] = {}   # id(QTreeWidgetItem) → item_set_id

    def load_folders(self, folders):
        """Populate from a sequence of folder dicts."""
        self.clear()
        self._id_map.clear()
        for folder in folders:
            item = QTreeWidgetItem([folder["os_folder_name"]])
            color = _folder_color(folder["name_ready"], folder["media_ready"])
            item.setForeground(0, QBrush(color))
            self.addTopLevelItem(item)
            self._id_map[id(item)] = folder["item_set_id"]

    def update_folder_status(self, item_set_id: int,
                             name_ready: bool, media_ready: bool):
        """Refresh the colour of one folder row after a status change."""
        color = _folder_color(name_ready, media_ready)
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if self._id_map.get(id(item)) == item_set_id:
                item.setForeground(0, QBrush(color))
                break

    def select_folder(self, item_set_id: int):
        """Restore the visual selection to the row matching item_set_id."""
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if self._id_map.get(id(item)) == item_set_id:
                self.setCurrentItem(item)
                return

    def _on_clicked(self, item: QTreeWidgetItem, _column: int):
        item_set_id = self._id_map.get(id(item))
        if item_set_id is not None:
            self.folder_selected.emit(item_set_id)
