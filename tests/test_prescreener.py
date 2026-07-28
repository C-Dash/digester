"""Characterization tests for prescreener.screen_file acceptance rules.

Images are synthesised with Pillow; no ExifTool or network needed.
"""

from cdash_digester.prescreener import screen_file
from conftest import make_image, make_tiff, make_multiframe_tiff, make_pdf


# ----------------------------------------------------------------------- JPEG

def test_jpeg_rgb_accepted(tmp_path):
    p = make_image(tmp_path / "a.jpg", "RGB")
    accepted, props = screen_file(p)
    assert accepted is True
    assert props["format"] == "RGB"
    assert props["repair_issues"] == []


def test_jpeg_non_rgb_rejected(tmp_path):
    # Grayscale JPEG is not 24-bit RGB.
    p = make_image(tmp_path / "a.jpg", "L")
    accepted, props = screen_file(p)
    assert accepted is False
    assert "l" in props["repair_issues"]  # mode added by _CLEAN_MODES miss


# ----------------------------------------------------------------------- TIFF

def test_tiff_rgb_lzw_accepted(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGB", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is True
    assert props["repair_issues"] == []


def test_tiff_grayscale_lzw_accepted(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "L", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is True


def test_tiff_bilevel_1bit_accepted(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "1", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is True
    assert props["repair_issues"] == []


def test_tiff_uncompressed_flags_wrong_compression(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    accepted, props = screen_file(p)
    assert accepted is False
    assert "wrong_compression" in props["repair_issues"]


def test_tiff_rgba_flags_repair(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGBA", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert "rgba" in props["repair_issues"]


def test_tiff_16bit_flags_repair(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "I;16", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert "i;16" in props["repair_issues"]


def test_tiff_multiframe_rejected_first_issue(tmp_path):
    p = make_multiframe_tiff(tmp_path / "a.tif", frames=3)
    accepted, props = screen_file(p)
    assert accepted is False
    # multiframe_tiff is always inserted first.
    assert props["repair_issues"][0] == "multiframe_tiff"


# ------------------------------------------------------------------------ PDF

def test_plain_pdf_flags_reject(tmp_path):
    p = make_pdf(tmp_path / "a.pdf", pages=2)
    accepted, props = screen_file(p)
    assert accepted is False
    assert "Reject" in props["repair_issues"]
    assert props["pdf_pages"] == 2


# --------------------------------------------------------------- unsupported

def test_unsupported_suffix_rejected(tmp_path):
    p = tmp_path / "a.png"
    make_image(p, "RGB", fmt="PNG")
    accepted, props = screen_file(p)
    assert accepted is False
    assert "Unsupported file type" in props["qa_note"]
