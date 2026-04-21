"""
Console Window

A detachable QDockWidget containing a colour-coded, timestamped log.
Messages are also appended to Catalog/batch.log when a log path is set.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDockWidget, QTextEdit, QWidget

_LEVEL_COLOR = {
    "info":    "#222222",
    "warning": "#cc6600",
    "error":   "#cc0000",
    "success": "#006600",
}


class ConsoleWindow(QDockWidget):
    """Detachable console that displays timestamped, colour-coded log messages."""

    def __init__(self, parent: QWidget = None):
        super().__init__("Console", parent)
        self.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
        )

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QTextEdit.WidgetWidth)
        self._text.setFontFamily("Courier New")
        self.setWidget(self._text)

        self._log_path: Optional[Path] = None

    def set_log_path(self, path: Path):
        self._log_path = path

    @Slot(str, str)
    def append_message(self, message: str, level: str = "info"):
        """Append a colour-coded, timestamped line to the console."""
        ts = datetime.now().strftime("%H:%M:%S")
        color = _LEVEL_COLOR.get(level, _LEVEL_COLOR["info"])
        # Escape any HTML in the message text
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._text.append(
            f'<span style="color:{color}">[{ts}] {safe}</span>'
        )
        self._text.moveCursor(QTextCursor.End)

        if self._log_path:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{ts}] [{level.upper():7s}] {message}\n")
            except OSError:
                pass

    def clear(self):
        self._text.clear()
