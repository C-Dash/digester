"""batch_doc_id / batch_media_id are built from the canonical filename.

The scanner renames a media file to its canonical form (place slug resolved
from Omeka) and records identifiers for it. Those identifiers must describe
the name the file ends up with, not the one it arrived with — they are
exported as media.csv's `identifier`/`Relation` and document.csv's
`identifier`, so a batch exported after a single scan must already be correct.
"""

from cdash_digester.digester import Digester
from conftest import FakeValidator, make_tiff, place_props


def _scan(make_batch, filenames, places, folder="F1-Main-OF101"):
    """Scan one folder holding the given media filenames; return the Digester."""
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


def test_identifier_matches_filename_after_one_scan(make_batch):
    """The core fix: identifiers used the pre-rename slug, so a single-scan
    batch exported identifiers that did not match its own filenames."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})

    row = d.db.get_media_for_folder(101)[0]
    assert row.filename == "Main_Place_0001p0001-VE-OP55.tif"
    assert "Main_Place" in row.batch_media_id
    # The stem of the identifier must match the stem of the file.
    assert row.batch_media_id.endswith("Main_Place_0001p0001-VE")
    d.close()


def test_doc_identifier_uses_the_resolved_slug(make_batch):
    """batch_doc_id is exported as document.csv's `identifier`."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})

    doc = d.db.get_docs_for_folder(101)[0]
    assert "Main_Place" in doc.batch_doc_id
    d.close()


def test_identifiers_are_stable_across_a_rescan(make_batch):
    """Scan 1 must already produce what scan 2 produces. Previously scan 1
    used the arrival slug and scan 2 re-parsed the renamed file."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})
    first_media = [m.batch_media_id for m in d.db.get_media_for_folder(101)]
    first_docs = [x.batch_doc_id for x in d.db.get_docs_for_folder(101)]

    d.scan_batch()
    assert [m.batch_media_id for m in d.db.get_media_for_folder(101)] == first_media
    assert [x.batch_doc_id for x in d.db.get_docs_for_folder(101)] == first_docs
    d.close()


def test_multipage_document_is_not_a_false_conflict(make_batch):
    """Regression guard for the trap in this change.

    doc_tracker["place_slug"] is also the doc-index conflict check, comparing
    parsed slug against parsed slug. Repointing it at the resolved slug would
    make page 2 compare its parsed "Main" against a stored "Main_Place" and be
    rejected as a place-name conflict on the first scan of every multi-page
    document. The resolved value lives in a separate id_slug key.
    """
    d = _scan(make_batch,
              ["Main_0001p0001-VE-OP55.tif", "Main_0001p0002-VE-OP55.tif"],
              {55: place_props("Main Place")})

    rows = sorted(d.db.get_media_for_folder(101), key=lambda m: m.filename)
    assert len(rows) == 2
    # Both pages belong to the same document, and neither was skipped.
    assert rows[0].doc_item_id is not None
    assert rows[0].doc_item_id == rows[1].doc_item_id
    for r in rows:
        assert "conflicts with existing place name" not in (r.filename_issues or "")
        assert "Main_Place" in r.batch_media_id
    # Page numbers are distinct within the document.
    assert {r.page_num for r in rows} == {1, 2}
    d.close()


def test_no_place_id_keeps_the_parsed_slug(make_batch):
    """The id_slug fallback: with no place to resolve there is no rename, so
    the identifier must keep the parsed slug — which still matches the file.

    Uses a stem with no -OP<id> token rather than an unresolvable one; see
    the note in test_scan.py about scanning a file whose place ID does not
    exist, which fails earlier for an unrelated reason.
    """
    d = _scan(make_batch, ["Main_0001p0001-VE.tif"], {})

    row = d.db.get_media_for_folder(101)[0]
    assert row.filename == "Main_0001p0001-VE.tif"          # untouched
    assert row.batch_media_id.endswith("Main_0001p0001-VE")
    assert "No place ID in filename" in (row.filename_issues or "")
    d.close()
