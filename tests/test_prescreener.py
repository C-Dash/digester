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
    # Grayscale JPEG is not 24-bit RGB — not admissible, no repair path.
    p = make_image(tmp_path / "a.jpg", "L")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]


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


def test_tiff_uncompressed_flags_compress_lzw(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    accepted, props = screen_file(p)
    assert accepted is False
    assert "Compress LZW" in props["repair_issues"]


def test_tiff_rgba_flags_flatten(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "RGBA", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert "Flatten" in props["repair_issues"]


def test_tiff_16bit_flags_flatten(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "I;16", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert "Flatten" in props["repair_issues"]


def test_tiff_uncompressed_oversized_flags_check_mbs_not_rejected(tmp_path, monkeypatch):
    import cdash_digester.prescreener as pres
    monkeypatch.setattr(pres, "_MAX_FILE_MB", 0.0)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    accepted, props = screen_file(p)
    assert accepted is False
    assert "Compress LZW" in props["repair_issues"]
    assert "Check MBs" in props["repair_issues"]
    assert "Reject" not in props["repair_issues"]


def test_tiff_compressed_oversized_rejected(tmp_path, monkeypatch):
    import cdash_digester.prescreener as pres
    monkeypatch.setattr(pres, "_MAX_FILE_MB", 0.0)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]


def test_tiff_multiframe_rejected_first_issue(tmp_path):
    p = make_multiframe_tiff(tmp_path / "a.tif", frames=3)
    accepted, props = screen_file(p)
    assert accepted is False
    # multiframe TIFFs are non-repairable; repair_issues is Reject-only.
    assert props["repair_issues"] == ["Reject"]


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
    assert any("Unsupported file type" in msg for msg in props["format_issues"])
    assert props["repair_issues"] == ["Reject"]


# ----------------------------------------------------------- unreadable/dead-ends

def test_corrupt_tiff_flags_reject_and_unreadable(tmp_path):
    p = tmp_path / "a.tif"
    p.write_bytes(b"not a real tiff")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["format"] == "Unreadable"
    assert props["repair_issues"] == ["Reject"]


def test_corrupt_jpeg_flags_reject_and_unreadable(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"not a real jpeg")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["format"] == "Unreadable"
    assert props["repair_issues"] == ["Reject"]


def test_megapixel_exceeded_flags_reject(tmp_path, monkeypatch):
    import cdash_digester.prescreener as pres
    monkeypatch.setattr(pres, "_MAX_MEGAPIXELS", 10)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]


def test_32bit_float_tiff_flags_reject(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "F", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]
