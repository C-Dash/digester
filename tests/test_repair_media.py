"""Characterization tests for repair_media transforms.

Mode-conversion repairs (rgba, la, i;16) need no ExifTool. The iphone_vert
rotation falls back to a default 90deg when ExifTool/orientation is absent.
"""

from PIL import Image

from cdash_digester.repair_media import repair_file, parse_repair_issues
from conftest import make_tiff


def _mode(path):
    with Image.open(path) as im:
        return im.mode


def test_parse_repair_issues_normalizes_and_dedupes():
    out = parse_repair_issues("RGBA, multiframe-tiff, rgba")
    assert out == ["rgba", "multiframe_tiff"]


def test_repair_rgba_to_rgb(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGBA")
    ok, msg = repair_file(p, ["rgba"])
    assert ok is True
    assert _mode(p) == "RGB"


def test_repair_la_to_grayscale(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "LA")
    ok, msg = repair_file(p, ["la"])
    assert ok is True
    assert _mode(p) == "L"


def test_repair_16bit_to_grayscale(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "I;16")
    ok, msg = repair_file(p, ["i;16"])
    assert ok is True
    assert _mode(p) == "L"


def test_repair_backs_up_original(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGBA")
    repair_file(p, ["rgba"])
    assert (tmp_path / "rejects" / "a.tif").exists()


def test_repair_multiframe_refused_and_removed(tmp_path):
    from conftest import make_multiframe_tiff
    p = make_multiframe_tiff(tmp_path / "a.tif", frames=2)
    ok, msg = repair_file(p, ["multiframe_tiff"])
    assert ok is False
    assert "multi-frame" in msg.lower()
    # Original is backed up to rejects/ and the working copy removed.
    assert (tmp_path / "rejects" / "a.tif").exists()
    assert not p.exists()


def test_repair_iphone_vert_rotates(tmp_path):
    # Portrait image; default rotation (no EXIF) swaps to landscape.
    p = make_tiff(tmp_path / "a.tif", "RGB", size=(4, 10))
    ok, msg = repair_file(p, ["iphone_vert"])
    assert ok is True
    with Image.open(p) as im:
        assert im.size == (10, 4)
