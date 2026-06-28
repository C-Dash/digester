"""
Thumbnail Pane

A scrollable grid of image thumbnails, one per media file.
Each thumbnail has a coloured border: green (go), red (no-go), grey (pending).
Selected thumbnails get a thicker border.

Thumbnails are rendered with Pillow for JPEG/TIFF and pymupdf for PDF.
A blank grey rectangle is shown when rendering fails.

Emits ``selection_changed(media_ids)`` when the user clicks a thumbnail.
Provides ``highlight_media_ids()`` to drive selection from the media table.
"""

import io
from pathlib import Path
from typing import Dict

import fitz                        # pymupdf
from PIL import Image

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap, QImage
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGridLayout, QLabel, QScrollArea,
    QVBoxLayout, QWidget,
)

from .status_colors import status_color as _border_color

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEBUG = False     # set True to re-enable thumbnail render diagnostics

_THUMB = 200       # bounding box in px
_BORDER = 3
_SEL_BORDER = 6
_LABEL_H = 32      # height reserved for filename text
_COLS = 4          # thumbnails per row


# ---------------------------------------------------------------------------
# Thumbnail rendering helpers
# ---------------------------------------------------------------------------

def _render_pdf_page(filepath: Path) -> QPixmap:
    if _DEBUG: print(f"DEBUG _render_pdf_page: {filepath}", flush=True)
    try:
        doc = fitz.open(str(filepath))
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
        doc.close()
        fitz.TOOLS.mupdf_warnings(reset=True)
        if _DEBUG: print(f"DEBUG _render_pdf_page: pixmap {pix.width}x{pix.height}", flush=True)
        qi = QImage(pix.samples, pix.width, pix.height,
                    pix.stride, QImage.Format_RGB888)
        pm = QPixmap.fromImage(qi)
        result = pm.scaled(_THUMB, _THUMB, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
        if _DEBUG: print(f"DEBUG _render_pdf_page: done, null={result.isNull()}", flush=True)
        return result
    except Exception as e:
        if _DEBUG: print(f"DEBUG _render_pdf_page: exception {e}", flush=True)
        return _blank()


def _render_image(filepath: Path) -> QPixmap:
    if _DEBUG: print(f"DEBUG _render_image: {filepath}", flush=True)
    try:
        img = Image.open(filepath)
        img.thumbnail((_THUMB, _THUMB), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pm = QPixmap()
        pm.loadFromData(buf.getvalue())
        if pm.isNull():
            if _DEBUG: print("DEBUG _render_image: QPixmap is null, returning blank", flush=True)
            return _blank()
        if _DEBUG: print(f"DEBUG _render_image: done {pm.width()}x{pm.height()}", flush=True)
        return pm
    except Exception as e:
        if _DEBUG: print(f"DEBUG _render_image: exception {e}", flush=True)
        return _blank()


def _blank() -> QPixmap:
    pm = QPixmap(_THUMB, _THUMB)
    pm.fill(QColor("#dddddd"))
    return pm


def _make_thumbnail(filepath: Path) -> QPixmap:
    try:
        if filepath.suffix.lower() == ".pdf":
            return _render_pdf_page(filepath)
        return _render_image(filepath)
    except Exception:
        return _blank()


# ---------------------------------------------------------------------------
# Single thumbnail widget  (QFrame + stylesheet border, no QPainter)
# ---------------------------------------------------------------------------

class _Thumb(QFrame):
    """One thumbnail cell with a stylesheet-based status border."""

    clicked = Signal(int, bool, bool)   # (media_id, ctrl_held, shift_held)

    def __init__(self, media_id: int, pixmap: QPixmap,
                 ready, filename: str = "", page_label: str = "",
                 badge_color: str = "yellow", parent=None):
        super().__init__(parent)
        self.media_id  = media_id
        self._ready    = ready
        self._selected = False

        _w = _THUMB + _SEL_BORDER * 2
        self.setFixedSize(_w, _THUMB + _SEL_BORDER * 2 + _LABEL_H)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setFixedSize(_w, _THUMB + _SEL_BORDER * 2)
        scaled = pixmap.scaled(_THUMB, _THUMB, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        self._label.setPixmap(scaled)
        layout.addWidget(self._label)

        if page_label:
            self._add_page_badge(page_label, scaled, badge_color)

        self._name_label = QLabel(filename)
        self._name_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._name_label.setFixedSize(_w, _LABEL_H)
        self._name_label.setWordWrap(True)
        self._name_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._name_label.setStyleSheet(
            "QLabel { font-size: 8pt; color: #333333; background: transparent; }"
        )
        layout.addWidget(self._name_label)

        self._update_style()

    def _add_page_badge(self, text: str, scaled: QPixmap, color: str = "yellow"):
        """Overlay a page badge on the image's upper-left corner.

        A black drop shadow (zero offset) acts as a halo so the number stays
        legible over light or dark previews, for both the yellow image page
        numbers and the red PDF page counts.
        """
        badge = QLabel(text, self._label)
        badge.setObjectName("pageBadge")
        # Scope the rule to this widget so it doesn't pick up the parent label's
        # status border/background (which would render as a coloured rectangle).
        badge.setStyleSheet(
            f"QLabel#pageBadge {{ color: {color}; font-weight: bold; "
            "font-size: 14pt; background: transparent; border: none; "
            "padding: 0px; }"
        )
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        glow = QGraphicsDropShadowEffect(badge)
        glow.setColor(QColor("black"))
        glow.setBlurRadius(4)
        glow.setOffset(0)
        badge.setGraphicsEffect(glow)
        badge.adjustSize()

        # Anchor to the image's top-left, accounting for the letterbox margin.
        inset = 4
        ox = (self._label.width()  - scaled.width())  // 2 + inset
        oy = (self._label.height() - scaled.height()) // 2 + inset
        badge.move(ox, oy)

    def _update_style(self):
        color = _border_color(self._ready)
        bw = _SEL_BORDER if self._selected else _BORDER
        self._label.setStyleSheet(
            f"QLabel {{ border: {bw}px solid {color}; "
            f"background-color: #f0f0f0; }}"
        )

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self._update_style()

    def set_status(self, ready):
        if self._ready != ready:
            self._ready = ready
            self._update_style()

    def mousePressEvent(self, event):
        mods = event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        self.clicked.emit(self.media_id, ctrl, shift)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# ThumbnailPane
# ---------------------------------------------------------------------------

class ThumbnailPane(QScrollArea):
    """Bottom-right pane: scrollable grid of thumbnails."""

    selection_changed = Signal(list)   # list[int] of media_ids

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._container = QWidget()
        self._layout = QGridLayout(self._container)
        self._layout.setSpacing(8)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self.setWidget(self._container)
        self.setWidgetResizable(True)

        self._widgets: Dict[int, _Thumb] = {}
        self._selected: set = set()
        self._anchor: int = None   # last plain/ctrl click — anchor for shift range
        self._suppress = False

    def load_media(self, rows, batch_root: Path):
        """Render thumbnails for all media rows in the selected folder."""
        if _DEBUG: print(f"DEBUG load_media: {len(rows)} rows, batch_root={batch_root}", flush=True)
        # Remove old widgets from layout and schedule deletion
        for w in self._widgets.values():
            self._layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()
        self._selected.clear()
        self._anchor = None
        if _DEBUG: print("DEBUG load_media: cleared old widgets", flush=True)

        col_count = _COLS
        if _DEBUG: print(f"DEBUG load_media: col_count={col_count}", flush=True)

        for i, row in enumerate(rows):
            try:
                filepath = batch_root / row["filepath"]
                if _DEBUG: print(f"DEBUG load_media [{i}]: filepath={filepath} exists={filepath.exists()}", flush=True)
                pm = _make_thumbnail(filepath) if filepath.exists() else _blank()
                if _DEBUG: print(f"DEBUG load_media [{i}]: pixmap ready, null={pm.isNull()}", flush=True)
                if filepath.suffix.lower() == ".pdf":
                    # PDF badge = page count (always shown, even 1), red.
                    page_label = str(row["num_pages"]) if row["num_pages"] else ""
                    badge_color = "red"
                else:
                    # Image badge = page number within a multi-page doc, yellow.
                    page_label = (str(row["page_num"])
                                  if row["num_pages"] and row["num_pages"] > 1
                                  and row["page_num"] else "")
                    badge_color = "yellow"
                widget = _Thumb(row["media_id"], pm, row["ready"],
                                row["filename"], page_label, badge_color)
                if _DEBUG: print(f"DEBUG load_media [{i}]: _Thumb created", flush=True)
                widget.clicked.connect(self._on_thumb_clicked)
                self._layout.addWidget(widget, i // col_count, i % col_count)
                self._widgets[row["media_id"]] = widget
                if _DEBUG: print(f"DEBUG load_media [{i}]: added to layout", flush=True)
            except Exception as exc:
                print(f"Thumbnail error for {row['filename']}: {exc}", flush=True)

        if _DEBUG: print("DEBUG load_media: setting stretch", flush=True)
        # Push widgets to top-left
        self._layout.setRowStretch(max(1, len(rows) // col_count), 1)
        self._layout.setColumnStretch(col_count, 1)
        if _DEBUG: print("DEBUG load_media: done", flush=True)

    def _on_thumb_clicked(self, media_id: int, ctrl: bool, shift: bool = False):
        if self._suppress:
            return
        order = list(self._widgets.keys())   # insertion order == display order
        if shift and self._anchor in self._widgets:
            i, j = order.index(self._anchor), order.index(media_id)
            lo, hi = sorted((i, j))
            range_ids = set(order[lo:hi + 1])
            # Extend the current selection with Ctrl+Shift; otherwise replace it.
            self._selected = (self._selected | range_ids) if ctrl else range_ids
            # Anchor stays put so the range can be re-dragged from it.
        elif ctrl:
            self._selected.symmetric_difference_update({media_id})
            self._anchor = media_id
        else:
            self._selected = {media_id}
            self._anchor = media_id
        self._apply_selection()
        self.selection_changed.emit(list(self._selected))

    def highlight_media_ids(self, media_ids: list):
        """Drive selection from the media table (no re-emit)."""
        self._suppress = True
        self._selected = set(media_ids)
        self._apply_selection()
        self._suppress = False

    def _apply_selection(self):
        for mid, widget in self._widgets.items():
            widget.set_selected(mid in self._selected)

    def update_status(self, media_id: int, ready):
        if media_id in self._widgets:
            self._widgets[media_id].set_status(ready)
