"""Document titles read "<place> - <document type description>".

The separator is a plain hyphen-minus. An em dash had crept into the scanner's
copy of this format string while both AssignmentService paths used "-", so the
same document was titled differently depending on which code path created it —
and the em dash reached document.csv's `title` column. The format now has one
definition (constants.format_doc_title) and these tests hold it.
"""

import pytest

from cdash_digester.constants import DOC_TITLE_SEP, format_doc_title
from cdash_digester.digester import Digester
from conftest import FakeValidator, make_tiff, place_props


def _scan(make_batch, filenames, places, folder="F1-Main-OF101"):
    root = make_batch("CDB260430-Test_batch")
    media = root / "media" / folder
    media.mkdir(parents=True)
    for name in filenames:
        make_tiff(media / name, "RGB", compression="tiff_lzw")
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = FakeValidator(folders={101: "Main St Folder"}, places=places)
    d.scan_batch()
    return d


# ----------------------------------------------------------------- the format

def test_separator_is_a_plain_hyphen():
    assert DOC_TITLE_SEP == " - "
    assert "—" not in DOC_TITLE_SEP


@pytest.mark.parametrize("code,expected", [
    ("VE", "Main Place - Exterior View"),
    ("RF", "Main Place - Research Form"),
    ("ZZ", "Main Place - ZZ"),            # unrecognised code shows itself
    (None, "Main Place - Uncategorized"),  # absent type
])
def test_format_doc_title(code, expected):
    assert format_doc_title("Main Place", code) == expected


# ------------------------------------------------------- the two code paths

def test_scanner_titles_use_the_hyphen(make_batch):
    d = _scan(make_batch, ["Main_Place-0001p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})

    doc = d.db.get_docs_for_folder(101)[0]
    assert doc.doc_title == "Main Place - Exterior View"
    assert "—" not in doc.doc_title
    d.close()


def test_assignment_titles_match_the_scanner(make_batch):
    """Both paths must produce the same title for the same document — the
    drift this guards against was invisible until the two were compared."""
    d = _scan(make_batch, ["Main_Place-0001p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})
    scanned_title = d.db.get_docs_for_folder(101)[0].doc_title

    media_id = d.db.get_media_for_folder(101)[0].media_id
    assert d.assign_media_to_doc([media_id], 55, "VE", is_multi_page=True) is True

    titles = {doc.doc_title for doc in d.db.get_docs_for_folder(101)}
    assert titles == {scanned_title} == {"Main Place - Exterior View"}
    d.close()


def test_single_page_assignment_titles_use_the_hyphen(make_batch):
    """The third call site: "each page is its own document"."""
    d = _scan(make_batch,
              ["Main_Place-0001p0001-VE-OP55.tif",
               "Main_Place-0001p0002-VE-OP55.tif"],
              {55: place_props("Main Place")})

    ids = [m.media_id for m in d.db.get_media_for_folder(101)]
    assert d.assign_media_to_doc(ids, 55, "RF", is_multi_page=False) is True

    new_titles = [doc.doc_title for doc in d.db.get_docs_for_folder(101)
                  if doc.doc_type_code == "RF"]
    assert new_titles == ["Main Place - Research Form"] * 2
    d.close()


def test_untyped_capture_document_has_a_readable_title(make_batch):
    """A case-1 run has no doc type yet, so the title must still be sensible
    rather than saying "None"."""
    d = _scan(make_batch, ["Mass_Ave-1-OP55.tif"], {55: place_props("Main Place")})

    doc = d.db.get_docs_for_folder(101)[0]
    assert doc.doc_title == "Main Place - Uncategorized"
    assert "None" not in doc.doc_title
    d.close()
