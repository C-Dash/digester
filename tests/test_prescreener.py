"""Characterization tests for prescreener.screen_file acceptance rules.

Images are synthesised with Pillow; no ExifTool or network needed.
"""

import pytest
import cdash_digester.prescreener as pres
from cdash_digester.prescreener import screen_file
from conftest import make_image, make_tiff, make_multiframe_tiff, make_pdf


# ----------------------------------------------------------------------- JPEG

def test_jpeg_rgb_accepted(tmp_path):
    p = make_image(tmp_path / "a.jpg", "RGB")
    accepted, props = screen_file(p)
    assert accepted is True
    assert props["format"] == "RGB 24-bit"
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
    monkeypatch.setattr(pres, "MAX_FILE_MB", 0.0)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression=None)
    accepted, props = screen_file(p)
    assert accepted is False
    assert "Compress LZW" in props["repair_issues"]
    assert "Check MBs" in props["repair_issues"]
    assert "Reject" not in props["repair_issues"]


def test_tiff_compressed_oversized_rejected(tmp_path, monkeypatch):
    import cdash_digester.prescreener as pres
    monkeypatch.setattr(pres, "MAX_FILE_MB", 0.0)
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

def test_unsupported_suffix_but_pil_readable_still_flags_not_supported(tmp_path):
    # Foreign suffix — even though PIL can open it (e.g. a PNG), format is
    # still the PIL mode but format_issues always says "Format not supported".
    p = tmp_path / "a.png"
    make_image(p, "RGB", fmt="PNG")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["format"] == "RGB 24-bit"
    assert props["format_issues"] == ["Format not supported"]
    assert props["repair_issues"] == ["Reject"]


def test_unsupported_suffix_and_pil_unreadable_flags_not_supported(tmp_path):
    p = tmp_path / "a.docx"
    p.write_bytes(b"not an image at all, just some bytes")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["format"] is None
    assert props["format_issues"] == ["Format not supported"]
    assert props["repair_issues"] == ["Reject"]


def test_unsupported_suffix_falls_back_to_filetype_sniff(tmp_path):
    # PIL can't open a zip, but the lightweight filetype sniff can identify
    # it from its magic bytes and give format a short description.
    p = tmp_path / "a.docx"
    p.write_bytes(b"PK\x03\x04" + b"0" * 40)
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["format"] == "ZIP"
    assert props["format_issues"] == ["Format not supported"]
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
    monkeypatch.setattr(pres, "MAX_MEGAPIXELS", 10)
    p = make_tiff(tmp_path / "a.tif", "RGB", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]


def test_32bit_float_tiff_flags_reject(tmp_path):
    p = make_tiff(tmp_path / "a.tif", "F", compression="tiff_lzw")
    accepted, props = screen_file(p)
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]


# ------------------------------------------------------- PDF/A conformance regex

@pytest.mark.parametrize("xmp", [
    "<pdfaid:conformance>B</pdfaid:conformance>",
    "<x:conformance>b</x:conformance>",          # any namespace prefix
    "<pdfaid:conformance> A </pdfaid:conformance>",
    'pdfaid:conformance="U"',
    'conformance="f"',
    "<pdfaid:conformance>E</pdfaid:conformance>",
])
def test_pdfa_conformance_regex_accepts_known_forms(xmp):
    """Element and attribute forms, any prefix, any level A/B/U/E/F, any case.

    Replaced 16 hand-written literal markers; this pins the equivalence.
    """
    assert pres._PDFA_CONFORMANCE_RE.search(xmp)


@pytest.mark.parametrize("xmp", [
    "",
    "<pdfaid:part>1</pdfaid:part>",              # part is not conformance
    "<pdfaid:conformance>Z</pdfaid:conformance>",  # not a real level
    'conformance="Z"',
])
def test_pdfa_conformance_regex_rejects_other_text(xmp):
    assert pres._PDFA_CONFORMANCE_RE.search(xmp) is None


@pytest.mark.parametrize("xmp,expected", [
    ("<pdfaid:part>1</pdfaid:part>", "1"),
    ("<x:part> 2 </x:part>", "2"),               # any prefix, any whitespace
    ('pdfaid:part="3"', "3"),                    # attribute form
    ('part="4"', "4"),
])
def test_pdfa_part_regex_accepts_known_forms(xmp, expected):
    """Mirrors the conformance regex: element and attribute form, any prefix.
    Reading the part is what tells PDF/A-1b from PDF/A-3b."""
    m = pres._PDFA_PART_RE.search(xmp)
    assert m is not None
    assert (m.group(1) or m.group(2)) == expected


@pytest.mark.parametrize("xmp", [
    "",
    "<pdfaid:conformance>B</pdfaid:conformance>",   # conformance is not part
    # Extension-schema boilerplate: real files declare "part" as a property
    # *name*. Requiring a digit is what stops it matching.
    "<pdfaProperty:name>part</pdfaProperty:name>",
    "<pdfaSchema:prefix>pdfaid</pdfaSchema:prefix>",
])
def test_pdfa_part_regex_rejects_other_text(xmp):
    assert pres._PDFA_PART_RE.search(xmp) is None
