"""
Shared status-color helpers for the GUI panes.

One palette, keyed by a tri-state ready value (True / False / None):
  - status_color : strong foreground/border colors (folder tree, thumbnail
                   borders, folder-info status, media-table status column).

folder_color combines the folder's two booleans (name_ready, media_ready) into a
single tri-state, then maps it through status_color.

There is deliberately no row-background helper: row tinting is delegated to the
Windows theme, so panes colour only foregrounds and borders. A pale-tint
palette used to live here unused — don't reintroduce one.
"""

# True = ready/green, False = not-ready/red, None = pending/grey
_STRONG = {True: "#2d8a2d", False: "#cc0000", None: "#888888"}
_TEXT   = {True: "Ready", False: "Not Ready", None: "—"}


def status_color(ready) -> str:
    """Strong hex color for a single tri-state ready value."""
    return _STRONG.get(ready, _STRONG[None])


def status_text(ready) -> str:
    """Display label for a single tri-state ready value.

    Shared so every pane says the same thing; the media table and the
    folder-info strip used to have separate vocabularies ("Not Ready" vs "NO").
    """
    return _TEXT.get(ready, _TEXT[None])


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
