"""PDF/A screening judges the full profile, not just the conformance letter.

The screener used to read only <pdfaid:conformance>, which is identical for
PDF/A-1b and PDF/A-3b — so a PDF/A-3 file, whose whole distinguishing feature
is that it may embed files of *arbitrary* format, screened as a clean "PDF/A".
Reading pdfaid:part as well is what makes the two distinguishable.

Admitted: 1a 1b 2a 2b 2u 4.  Declined: 3a 3b 3u (arbitrary attachments),
4f (same, for PDF 2.0), 4e (3D/RichMedia).

These build real PDFs with real XMP packets and run screen_file end to end,
rather than asserting against the regexes in isolation.
"""

import pytest

from cdash_digester import prescreener as pres
from cdash_digester.prescreener import PDFA_ADMITTED, screen_file
from conftest import make_pdf


def _screen(tmp_path, **kwargs):
    pdf = make_pdf(tmp_path / "doc.pdf", **kwargs)
    return screen_file(pdf)


def _structural(props):
    """format_issues excluding MuPDF's structural chatter, which is orthogonal."""
    return [i for i in props["format_issues"]
            if not i.startswith("PDF structure:")]


# --------------------------------------------------- every defined profile

# All 11 profiles the standards define, with the verdict each should get.
ALL_PROFILES = [
    ("1", "A", "PDF/A-1A", True),
    ("1", "B", "PDF/A-1B", True),
    ("2", "A", "PDF/A-2A", True),
    ("2", "B", "PDF/A-2B", True),
    ("2", "U", "PDF/A-2U", True),
    ("3", "A", "PDF/A-3A", False),
    ("3", "B", "PDF/A-3B", False),
    ("3", "U", "PDF/A-3U", False),
    ("4", None, "PDF/A-4", True),
    ("4", "E", "PDF/A-4E", False),
    ("4", "F", "PDF/A-4F", False),
]


@pytest.mark.parametrize("part,conf,flavor,admit", ALL_PROFILES)
def test_every_defined_profile(tmp_path, part, conf, flavor, admit):
    accepted, props = _screen(tmp_path, part=part, conformance=conf)

    assert props["format"] == flavor
    assert accepted is admit
    if admit:
        assert props["repair_issues"] == []
        assert _structural(props) == []
    else:
        assert props["repair_issues"] == ["Reject"]
        assert any("not an accepted PDF/A profile" in i
                   for i in _structural(props))


def test_the_table_matches_the_policy_constant():
    """The parametrised table above and PDFA_ADMITTED must not drift apart:
    adding a profile to the set should require exactly one line here."""
    admitted = {flavor.removeprefix("PDF/A-")
                for _p, _c, flavor, ok in ALL_PROFILES if ok}
    assert admitted == set(PDFA_ADMITTED)


def test_pdfa_3_is_declined_for_a_stated_reason():
    """The point of the whole change: 3B and 1B differ only in the part, and
    only one of them is admissible."""
    assert "3B" not in PDFA_ADMITTED
    assert "1B" in PDFA_ADMITTED


def test_pdfa_4f_is_declined_alongside_pdfa_3():
    """4f re-permits arbitrary embedded files, the same reason 3 is declined.
    A higher part number does not make it admissible."""
    assert "4F" not in PDFA_ADMITTED
    assert "4" in PDFA_ADMITTED


# ------------------------------------------------------------- no claim

def test_a_plain_pdf_is_still_rejected(tmp_path):
    accepted, props = _screen(tmp_path)
    assert accepted is False
    assert props["format"] == "PDF"
    assert "Non-archival PDF" in props["format_issues"]
    assert props["repair_issues"] == ["Reject"]


def test_the_namespace_alone_is_not_a_claim(tmp_path):
    """A pdfaid namespace declaration with neither value in it says nothing."""
    accepted, props = _screen(tmp_path, pdfa_ns=True)
    assert accepted is False
    assert props["format"] == "PDF"
    assert "Non-archival PDF" in props["format_issues"]


# --------------------------------------------------- incomplete markers

def test_part_without_conformance_is_recorded_and_judged_on_the_part(tmp_path):
    accepted, props = _screen(tmp_path, part="2")
    assert props["format"] == "PDF/A-2?"
    assert accepted is True                      # part 2 is admissible
    assert props["repair_issues"] == []
    assert any("marker incomplete" in i for i in _structural(props))


def test_an_incomplete_marker_on_a_declined_part_is_still_declined(tmp_path):
    accepted, props = _screen(tmp_path, part="3")
    assert props["format"] == "PDF/A-3?"
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]


def test_conformance_without_part_is_recorded_and_admitted(tmp_path):
    """It could be a 3B in disguise, but equally the 1B that every real file
    in the batch is. Flagged rather than assumed either way."""
    accepted, props = _screen(tmp_path, conformance="B")
    assert props["format"] == "PDF/A-?B"
    assert accepted is True
    assert any("marker incomplete" in i for i in _structural(props))


def test_part_4_without_conformance_is_complete_not_partial(tmp_path):
    """PDF/A-4's base profile genuinely has no conformance letter, so this is
    a whole claim — it must not be reported as an incomplete marker."""
    accepted, props = _screen(tmp_path, part="4")
    assert props["format"] == "PDF/A-4"
    assert accepted is True
    assert _structural(props) == []


# ------------------------------------------------------- malformed claims

def test_a_nonexistent_level_for_the_part_is_rejected(tmp_path):
    """PDF/A-1 defines only a and b, so 1U is not a profile. It must not be
    waved through on the strength of its part being 1."""
    accepted, props = _screen(tmp_path, part="1", conformance="U")
    assert props["format"] == "PDF/A-1U"
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]
    assert any("not a recognised PDF/A profile" in i
               for i in _structural(props))


def test_an_unknown_part_is_rejected(tmp_path):
    """Admit-list membership is the single test, so a future part is declined
    until it is explicitly considered."""
    accepted, props = _screen(tmp_path, part="5", conformance="B")
    assert props["format"] == "PDF/A-5B"
    assert accepted is False
    assert props["repair_issues"] == ["Reject"]


# --------------------------------------------------- real-world XMP shapes

def test_case_insensitive_conformance_is_normalised(tmp_path):
    accepted, props = _screen(tmp_path, part="2", conformance="b")
    assert props["format"] == "PDF/A-2B"
    assert accepted is True


def test_extension_schema_boilerplate_does_not_fake_a_part(tmp_path):
    """Two of the real batch files embed a PDF/A extension-schema description,
    which contains the literal words "part" and "conformance" as property
    *names*. Requiring a digit is what stops those matching."""
    boilerplate = (
        "   <pdfaSchema:prefix>pdfaid</pdfaSchema:prefix>\n"
        "   <pdfaProperty:name>part</pdfaProperty:name>\n"
        "   <pdfaProperty:name>conformance</pdfaProperty:name>"
    )
    accepted, props = _screen(tmp_path, part="2", conformance="B",
                              extra_xmp=boilerplate)
    assert props["format"] == "PDF/A-2B"
    assert accepted is True

    # And with no real values present, the boilerplate alone claims nothing.
    accepted, props = _screen(tmp_path, pdfa_ns=True, extra_xmp=boilerplate)
    assert props["format"] == "PDF"


def test_attribute_form_is_accepted(tmp_path):
    """The compact rdf:Description form puts both values in attributes."""
    pdf = tmp_path / "attr.pdf"
    make_pdf(pdf, pdfa_ns=True,
             extra_xmp='   <rdf:X pdfaid:part="2" pdfaid:conformance="U"/>')
    accepted, props = screen_file(pdf)
    assert props["format"] == "PDF/A-2U"
    assert accepted is True


# ------------------------------------------------------ resolver directly

@pytest.mark.parametrize("xmp,expected", [
    ("", None),
    ("<pdfaid:part>2</pdfaid:part>", None),          # no namespace, no claim
])
def test_resolver_requires_the_namespace(xmp, expected):
    profile, _note = pres._pdfa_profile(xmp)
    assert profile is expected
