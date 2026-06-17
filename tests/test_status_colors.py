"""Tests for the shared GUI status-color helpers (pure functions, no Qt)."""

from cdash_digester.gui.status_colors import (
    status_color, row_background, folder_color,
)


def test_status_color_tristate():
    assert status_color(True) == "#2d8a2d"
    assert status_color(False) == "#cc0000"
    assert status_color(None) == "#888888"


def test_row_background_tristate():
    assert row_background(True) == "#e8f5e9"
    assert row_background(False) == "#ffebee"
    assert row_background(None) == "#f5f5f5"


def test_folder_color_combines_flags():
    assert folder_color(True, True) == "#2d8a2d"     # both ready -> green
    assert folder_color(True, False) == "#cc0000"    # one not ready -> red
    assert folder_color(False, True) == "#cc0000"
    assert folder_color(None, None) == "#888888"     # unknown -> grey
