"""Characterization tests for repair_media transforms.

Mode-conversion repairs (all under the "flatten" issue code) and the
"compress_lzw" re-save need no ExifTool. iphone_vert rotation is retired
from the repair_issues vocabulary; the rotation helpers stay in the module
for the planned Rotate CW/CCW menu actions but aren't exercised here.
"""

from PIL import Image

from cdash_digester.repair_media import repair_file, parse_repair_issues
from conftest import make_tiff


def _mode(path):
    with Image.open(path) as im:
        return im.mode


def test_parse_repair_issues_normalizes_and_dedupes():
    out = parse_repair_issues("Flatten, multiframe-tiff, flatten")
    assert out == ["flatten", "multiframe_tiff"]


def test_repair_flatten_rgba_to_rgb(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGBA")
    ok, msg = repair_file(p, ["flatten"])
    assert ok is True
    assert _mode(p) == "RGB"


def test_repair_flatten_la_to_grayscale(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "LA")
    ok, msg = repair_file(p, ["flatten"])
    assert ok is True
    assert _mode(p) == "L"


def test_repair_flatten_16bit_to_grayscale(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "I;16")
    ok, msg = repair_file(p, ["flatten"])
    assert ok is True
    assert _mode(p) == "L"


def test_repair_compress_lzw_noted(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    ok, msg = repair_file(p, ["compress_lzw"])
    assert ok is True
    assert "lzw" in msg.lower()


def test_repair_backs_up_original(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGBA")
    repair_file(p, ["flatten"])
    assert (tmp_path / "repaired" / "a.tif").exists()


def test_repair_reject_flagged_file_untouched(tmp_path):
    from conftest import make_multiframe_tiff
    p = make_multiframe_tiff(tmp_path / "a.tif", frames=2)
    original_bytes = p.read_bytes()
    ok, msg = repair_file(p, ["multiframe_tiff"])
    assert ok is False
    assert "reject action" in msg.lower()
    # File is left completely untouched — no backup, no delete.
    assert p.exists()
    assert p.read_bytes() == original_bytes
    assert not (tmp_path / "repaired").exists()


def test_repair_check_mbs_kept_when_now_within_limit(tmp_path, monkeypatch):
    from cdash_digester import prescreener as pres
    monkeypatch.setattr(pres, "_MAX_FILE_MB", 1000.0)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    ok, msg = repair_file(p, ["compress_lzw", "check_mbs"])
    assert ok is True
    assert "within" in msg.lower()
    assert (tmp_path / "repaired" / "a.tif").exists()


def test_repair_check_mbs_reverts_when_still_over_limit(tmp_path, monkeypatch):
    from cdash_digester import prescreener as pres
    monkeypatch.setattr(pres, "_MAX_FILE_MB", 0.0)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    original_bytes = p.read_bytes()
    ok, msg = repair_file(p, ["compress_lzw", "check_mbs"])
    assert ok is False
    assert "still" in msg.lower()
    # Reverted — original left untouched, no backup created.
    assert p.read_bytes() == original_bytes
    assert not (tmp_path / "repaired").exists()
