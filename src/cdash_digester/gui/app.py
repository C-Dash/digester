"""
Application entry point

Builds the QApplication, forces the light palette the UI's colours assume, and
shows the main window. Kept separate from main_window so the window module is
importable (by tests and tooling) without any application-level side effects.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QLoggingCategory
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


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
