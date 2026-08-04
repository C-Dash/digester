"""Tests for the shared GUI status-color helpers (pure functions, no Qt)."""

from cdash_digester.gui.status_colors import (
    status_color, status_text, folder_color,
)


def test_status_color_tristate():
    assert status_color(True) == "#2d8a2d"
    assert status_color(False) == "#cc0000"
    assert status_color(None) == "#888888"


def test_folder_color_combines_flags():
    assert folder_color(True, True) == "#2d8a2d"     # both ready -> green
    assert folder_color(True, False) == "#cc0000"    # one not ready -> red
    assert folder_color(False, True) == "#cc0000"
    assert folder_color(None, None) == "#888888"     # unknown -> grey


def test_status_text_tristate():
    """One vocabulary for every pane — the media table and folder-info strip
    previously disagreed ("Not Ready" vs "NO")."""
    assert status_text(True) == "Ready"
    assert status_text(False) == "Not Ready"
    assert status_text(None) == "—"
