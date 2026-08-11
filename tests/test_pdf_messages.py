"""MuPDF's complaints reach the catalog, not the console.

MuPDF writes error text straight to file descriptor 1 by default — not stderr,
and not via any Python mechanism — so a malformed PDF printed
"MuPDF error: format error: …" into the terminal with no indication of which
file caused it, and nothing in the app could intercept it.

pdf_util turns that display off at import and hands the messages back instead,
so they land in the file's format_issues. These tests hold both halves: the
console stays clean, and the information is not lost.
"""

import os
import sys

import fitz
import pytest

from cdash_digester import pdf_util
from cdash_digester.prescreener import screen_file
from conftest import make_pdf


@pytest.fixture
def broken_pdf(tmp_path):
    """A PDF whose page tree points at a non-page object.

    Built by damaging a real PDF rather than by mocking, so the test exercises
    MuPDF's actual message channel — the whole point of the fix.
    """
    good = make_pdf(tmp_path / "good.pdf")
    bad = tmp_path / "Main_Place-0001p0001-RF-OP55.pdf"
    bad.write_bytes(good.read_bytes().replace(b"/Type/Page", b"/Type/Pag0", 1))
    pdf_util.drain_pdf_messages()      # start from a clean buffer
    return bad


def _capture_fd1(fn):
    """Run fn with file descriptor 1 redirected; return what was written to it.

    Redirects the OS-level descriptor, not sys.stdout, because the C library
    writes to the descriptor directly and never touches the Python object.
    """
    import tempfile
    from pathlib import Path

    sink = Path(tempfile.mkdtemp()) / "fd1.txt"
    saved = os.dup(1)
    handle = os.open(str(sink), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.dup2(handle, 1)
    try:
        result = fn()
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(handle)
        os.close(saved)
    return result, sink.read_text(errors="replace")


def test_mupdf_error_display_is_off():
    """The setting the whole fix rests on, asserted directly: importing
    pdf_util must have turned the console channel off."""
    assert fitz.TOOLS.mupdf_display_errors() == 0


def test_screening_a_broken_pdf_prints_nothing_to_the_console(broken_pdf):
    _, written = _capture_fd1(lambda: screen_file(broken_pdf))
    assert "MuPDF" not in written
    assert written.strip() == ""


def test_the_defect_is_recorded_against_the_file(broken_pdf):
    _accepted, props = screen_file(broken_pdf)
    structure = [i for i in props["format_issues"]
                 if i.startswith("PDF structure:")]
    assert structure, f"no structural issue recorded: {props['format_issues']}"
    assert "non-page object in page tree" in " ".join(structure).lower()


def test_a_clean_pdf_records_no_structural_defect(tmp_path):
    pdf = make_pdf(tmp_path / "Main_Place-0001p0001-RF-OP55.pdf")
    pdf_util.drain_pdf_messages()

    _accepted, props = screen_file(pdf)
    assert not [i for i in props["format_issues"]
                if i.startswith("PDF structure:")]


def test_messages_do_not_leak_from_one_file_to_the_next(tmp_path):
    """The MuPDF buffer is global and cumulative. If a file's messages were not
    drained, the *next* file screened would be blamed for them."""
    good = make_pdf(tmp_path / "good.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(good.read_bytes().replace(b"/Type/Page", b"/Type/Pag0", 1))
    clean = make_pdf(tmp_path / "clean.pdf")
    pdf_util.drain_pdf_messages()

    _a, bad_props = screen_file(bad)
    _b, clean_props = screen_file(clean)

    assert [i for i in bad_props["format_issues"] if i.startswith("PDF structure:")]
    assert not [i for i in clean_props["format_issues"]
                if i.startswith("PDF structure:")]


def test_recovery_chatter_is_not_reported_as_a_defect(tmp_path):
    """MuPDF narrates its own repair work ("trying to repair broken xref").
    That is progress, not a problem with the file."""
    good = make_pdf(tmp_path / "good.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(good.read_bytes().replace(b"xref", b"xrEf", 1))
    pdf_util.drain_pdf_messages()

    _accepted, props = screen_file(bad)
    joined = " ".join(props["format_issues"]).lower()
    assert "trying to repair" not in joined
    assert "repairing pdf document" not in joined


def test_screening_rasterises_page_one(tmp_path, monkeypatch):
    """Structural guard for the probe that no synthetic fixture can reach.

    The defect that prompted all this — "No common ancestor in structure tree"
    — is emitted by MuPDF *only* during rasterisation: not at open, not at
    load_page, not even at get_text (measured against the real batch). So
    screening has to actually render a page or it will pass such a file as
    clean. Building a PDF with a broken tagged-structure tree by hand is not
    practical, so this asserts the call is made rather than its effect; without
    it, deleting the get_pixmap line would break nothing visible in this suite.
    """
    calls = []
    real = fitz.Page.get_pixmap

    def spy(self, *a, **kw):
        calls.append(self.number)
        return real(self, *a, **kw)

    monkeypatch.setattr(fitz.Page, "get_pixmap", spy)

    pdf = make_pdf(tmp_path / "doc.pdf", pages=3)
    screen_file(pdf)

    assert calls == [0], f"expected page 1 to be rasterised once, got {calls}"


def test_drain_strips_prefixes_and_collapses_duplicates(monkeypatch):
    monkeypatch.setattr(
        fitz.TOOLS, "mupdf_warnings",
        lambda reset=True: (
            "format error: No common ancestor in structure tree\n"
            "format error: No common ancestor in structure tree\n"
            "trying to repair broken xref\n"
            "\n"
        ),
    )
    assert pdf_util.drain_pdf_messages() == [
        "No common ancestor in structure tree"
    ]
