"""
Folder Info Pane

A small fixed-height strip shown above the media table that summarises the
currently selected folder: its name, name-ready / media-ready status, and note.
"""

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget,
)

from .folder_pane import _folder_color


def _status_text(ready) -> str:
    if ready is True:
        return "ok"
    if ready is False:
        return "NO"
    return "—"


class FolderInfoPane(QFrame):
    """Fixed-height summary of the selected folder."""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(6)

        self._name = QLabel("—")
        self._name.setStyleSheet("font-weight: bold;")
        self._name_status = QLabel("—")
        self._media_status = QLabel("—")
        self._note = QLabel("")
        self._note.setStyleSheet("color: #555555;")

        row.addWidget(QLabel("Folder:"))
        row.addWidget(self._name)
        row.addSpacing(12)
        row.addWidget(QLabel("Name:"))
        row.addWidget(self._name_status)
        row.addSpacing(12)
        row.addWidget(QLabel("Media:"))
        row.addWidget(self._media_status)
        row.addSpacing(12)
        row.addWidget(QLabel("Note:"))
        row.addWidget(self._note, 1)   # note takes remaining width, single line

        self.clear()

    def clear(self):
        self._name.setText("—")
        self._name.setStyleSheet("font-weight: bold; color: #888888;")
        self._name_status.setText("—")
        self._name_status.setStyleSheet("")
        self._media_status.setText("—")
        self._media_status.setStyleSheet("")
        self._note.setText("")

    def show_folder(self, folder):
        """Populate from a Folder row (mapping-compatible). None clears the pane."""
        if not folder:
            self.clear()
            return

        name = folder["cdash_folder_name"] or folder["os_folder_name"] or "—"
        name_ready = folder["name_ready"]
        media_ready = folder["media_ready"]

        self._name.setText(name)
        self._name.setStyleSheet(
            f"font-weight: bold; color: {_folder_color(name_ready, media_ready).name()};"
        )
        self._set_status(self._name_status, name_ready)
        self._set_status(self._media_status, media_ready)
        self._note.setText(folder["notes"] or "")

    @staticmethod
    def _set_status(label: QLabel, ready):
        label.setText(_status_text(ready))
        if ready is True:
            color = "#2d8a2d"
        elif ready is False:
            color = "#cc0000"
        else:
            color = "#888888"
        label.setStyleSheet(f"color: {color}; font-weight: bold;")
