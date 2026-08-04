"""Characterization tests for repair_media transforms.

Mode-conversion repairs (all under the "flatten" issue code) and the
"compress_lzw" re-save need no ExifTool. Rotate CW/CCW (rotate_file) is
exercised at the bottom of this file — JPEG needs a real ExifTool on PATH
(present in this dev environment) to read/write the Orientation tag.
"""

from PIL import Image
from PIL.ExifTags import Base

from cdash_digester.repair_media import (
    repair_file, rotate_file, parse_repair_issues,
    _ORIENTATION_ROTATE_CW, _ORIENTATION_ROTATE_CCW,
)
from cdash_digester.exiftool_util import read_tags
from conftest import make_tiff, make_image


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
    monkeypatch.setattr(pres, "MAX_FILE_MB", 1000.0)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    ok, msg = repair_file(p, ["compress_lzw", "check_mbs"])
    assert ok is True
    assert "within" in msg.lower()
    assert (tmp_path / "repaired" / "a.tif").exists()


def test_repair_check_mbs_reverts_when_still_over_limit(tmp_path, monkeypatch):
    from cdash_digester import prescreener as pres
    monkeypatch.setattr(pres, "MAX_FILE_MB", 0.0)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    original_bytes = p.read_bytes()
    ok, msg = repair_file(p, ["compress_lzw", "check_mbs"])
    assert ok is False
    assert "still" in msg.lower()
    # Reverted — original left untouched, no backup created.
    assert p.read_bytes() == original_bytes
    assert not (tmp_path / "repaired").exists()


# ------------------------------------------------------------------ rotation

def test_orientation_rotate_tables_are_internally_consistent():
    # 4x either direction returns to the start; CW then CCW cancels; for
    # every starting orientation, including the four mirrored ones (2/4/5/7).
    for start in range(1, 9):
        x = start
        for _ in range(4):
            x = _ORIENTATION_ROTATE_CW[x]
        assert x == start
        x = start
        for _ in range(4):
            x = _ORIENTATION_ROTATE_CCW[x]
        assert x == start
        assert _ORIENTATION_ROTATE_CCW[_ORIENTATION_ROTATE_CW[start]] == start
        assert _ORIENTATION_ROTATE_CW[_ORIENTATION_ROTATE_CCW[start]] == start


def _orientation_tag(path):
    tags = read_tags(path)
    return tags.get("EXIF:Orientation", tags.get("Orientation"))


def test_rotate_jpeg_cw_from_no_tag_writes_orientation_6(tmp_path):
    p = make_image(tmp_path / "a.jpg", "RGB", fmt="JPEG")
    original_bytes = p.read_bytes()
    ok, msg = rotate_file(p, "cw")
    assert ok is True
    assert _orientation_tag(p) == _ORIENTATION_ROTATE_CW[1] == 6
    # Metadata-only: no pixel re-encode, no backup/commit ceremony.
    assert not (tmp_path / "repaired").exists()
    assert p.read_bytes() != original_bytes   # the tag write did change bytes


def test_rotate_jpeg_ccw_from_existing_orientation(tmp_path):
    exif = Image.Exif()
    exif[Base.Orientation] = 6
    p = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(p, format="JPEG", exif=exif.tobytes())
    assert _orientation_tag(p) == 6

    ok, msg = rotate_file(p, "ccw")
    assert ok is True
    assert _orientation_tag(p) == _ORIENTATION_ROTATE_CCW[6] == 1


def test_rotate_tiff_cw_bakes_pixels_and_resets_orientation(tmp_path):
    # No pre-existing orientation tag: this app never writes one to a TIFF
    # except via this rotate feature, which always resets it to 1 afterward
    # — so a stray tag isn't a realistic starting state to test against.
    # (Also: Pillow's TIFF decoder auto-applies Orientation on open(), unlike
    # JPEG, so a pre-set tag would confuse the dimension math below.)
    p = make_tiff(tmp_path / "a.tif", "RGB", size=(4, 8))
    assert _orientation_tag(p) is None

    ok, msg = rotate_file(p, "cw")
    assert ok is True
    with Image.open(p) as im:
        assert im.size == (8, 4)   # width/height swapped via expand=True
    # TIFF orientation is never trusted — explicitly written to Normal.
    assert _orientation_tag(p) == 1
    assert (tmp_path / "repaired" / "a.tif").exists()


def test_rotate_tiff_combined_with_pending_flatten(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGBA", compression="tiff_lzw")
    ok, msg = rotate_file(p, "cw", issues=["flatten"])
    assert ok is True
    with Image.open(p) as im:
        assert im.mode == "RGB"          # flatten applied
        assert im.size == (8, 8)         # square source, but rotation ran too
    assert "rotated" in msg.lower()
    assert (tmp_path / "repaired" / "a.tif").exists()


def test_rotate_reject_flagged_file_untouched(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGB")
    original_bytes = p.read_bytes()
    ok, msg = rotate_file(p, "cw", issues=["reject"])
    assert ok is False
    assert "reject action" in msg.lower()
    assert p.read_bytes() == original_bytes
    assert not (tmp_path / "repaired").exists()


def test_rotate_pdf_refused(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4 not a real pdf")
    ok, msg = rotate_file(p, "cw")
    assert ok is False
    assert "pdf" in msg.lower()


def test_repair_flatten_preserves_tiff_metadata(tmp_path):
    """Regression test for the general EXIF-carryover practice, independent
    of rotation: a descriptive TIFF tag survives a plain Flatten repair."""
    exif = Image.Exif()
    exif[Base.Make] = "TestScanner"
    p = tmp_path / "a.tif"
    Image.new("RGBA", (8, 8)).save(p, format="TIFF", exif=exif.tobytes())

    ok, msg = repair_file(p, ["flatten"])
    assert ok is True
    tags = read_tags(p)
    assert tags.get("EXIF:Make", tags.get("Make")) == "TestScanner"
