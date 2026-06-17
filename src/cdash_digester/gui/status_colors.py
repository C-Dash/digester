"""
Shared status-color helpers for the GUI panes.

Two palettes, both keyed by a tri-state ready value (True / False / None):
  - status_color  : strong foreground/border colors (folder tree, thumbnail
                    borders, folder-info status).
  - row_background : pale row tints (media table).

folder_color combines the folder's two booleans (name_ready, media_ready) into a
single tri-state, then maps it through status_color.
"""

# True = ready/green, False = not-ready/red, None = pending/grey
_STRONG = {True: "#2d8a2d", False: "#cc0000", None: "#888888"}
_PALE   = {True: "#e8f5e9", False: "#ffebee", None: "#f5f5f5"}


def status_color(ready) -> str:
    """Strong hex color for a single tri-state ready value."""
    return _STRONG.get(ready, _STRONG[None])


def row_background(ready) -> str:
    """Pale hex background tint for a single tri-state ready value."""
    return _PALE.get(ready, _PALE[None])


def _combine(name_ready, media_ready):
    """Collapse a folder's two booleans into one tri-state ready value."""
    if name_ready and media_ready:
        return True
    if name_ready is False or media_ready is False:
        return False
    return None


def folder_color(name_ready, media_ready) -> str:
    """Strong hex color for a folder given its name/media ready flags."""
    return status_color(_combine(name_ready, media_ready))
