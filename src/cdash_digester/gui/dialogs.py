"""
Modal dialogs

Self-contained QDialog subclasses with no coupling to MainWindow: they collect
input (or show information) and expose the result as properties.
"""

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QRadioButton, QVBoxLayout,
)

from .. import __build_date__, __version__
from ..cdash_objects import DOC_TYPES


class AssignDialog(QDialog):
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


class AboutDialog(QDialog):
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
