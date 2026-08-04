"""Characterization tests for the scan pipeline: the doc-tracking / page-grouping
state machine, persistent-cache short-circuiting, and CSV export.

A FakeValidator replaces the Omeka-S API so these run offline and deterministic.
This is the safety net for the planned extraction of _scan_media_in_folder into
a FolderScanner and of the CSV writers into a CatalogExportService.
"""

import csv

import pytest

from cdash_digester.digester import Digester
from conftest import FakeValidator, place_props, make_tiff


def _media(folder, place_slug, doc_index, page, doc_type, place_id):
    name = f"{place_slug}_{doc_index:04d}p{page:04d}-{doc_type}-OP{place_id}.tif"
    make_tiff(folder / name, "RGB", compression="tiff_lzw")


@pytest.fixture
def scanned(make_batch):
    """Build a one-folder batch with three media files across two documents,
    scan it with a fake validator, and return (digester, validator)."""
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    # doc_index 1 has two pages; doc_index 2 has one page.
    _media(folder, "Main", 1, 1, "VE", 55)
    _media(folder, "Main", 1, 2, "VE", 55)
    _media(folder, "Main", 2, 1, "VI", 55)

    validator = FakeValidator(folders={101: "Main St Folder"},
                              places={55: place_props("Main Place")})
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = validator
    d.scan_batch()
    yield d, validator
    d.close()


def test_scan_registers_non_accepted_suffix_file_as_reject(make_batch):
    """A file outside the accepted suffixes used to be silently skipped by
    the scanner; it must now get its own row, Reject-flagged, alongside a
    normal accepted file in the same folder."""
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    _media(folder, "Main", 1, 1, "VE", 55)
    (folder / "notes.docx").write_bytes(b"not an image at all, just bytes")

    validator = FakeValidator(folders={101: "Main St Folder"},
                              places={55: place_props("Main Place")})
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = validator
    d.scan_batch()

    rows = d.db.get_media_for_folder(101)
    assert len(rows) == 2
    bad_row = next(r for r in rows if r["filename"] == "notes.docx")
    assert bad_row["ready"] is False
    assert bad_row["repair_issues"] == "Reject"
    assert bad_row["format_issues"] == "Format not supported"
    d.close()


def test_scan_counts(scanned):
    d, _ = scanned
    stats = d.db.count_batch_stats()
    assert stats == {"folders": 1, "places": 1, "documents": 2, "media": 3}


def test_scan_groups_pages_into_documents(scanned):
    d, _ = scanned
    docs = d.db.get_docs_for_folder(101)
    pages = sorted(doc["num_pages"] for doc in docs)
    assert pages == [1, 2]              # two-page VE doc, one-page VI doc


def test_scan_all_media_ready_and_folder_ready(scanned):
    d, _ = scanned
    folder = d.db.get_folder_by_item_set(101)
    assert folder["name_ready"] is True
    assert folder["media_ready"] is True


def test_scan_format_rejected_media_not_ready_despite_valid_name(make_batch):
    """A format-only rejection (no name/place problem) must still leave the
    media row not-ready. Regression test: media_ready was once computed from
    the name-problem list alone, which stays empty for a purely format-related
    issue — so format problems silently passed. (The fields involved are now
    format_issues and filename_issues.)"""
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGBA", compression="tiff_lzw")

    validator = FakeValidator(folders={101: "Main St Folder"},
                              places={55: place_props("Main Place")})
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = validator
    d.scan_batch()

    rows = d.db.get_media_for_folder(101)
    assert len(rows) == 1
    assert rows[0]["ready"] is False
    assert rows[0]["filename_issues"] == ""   # name/place parsed fine
    assert "Flatten" in rows[0]["repair_issues"]
    d.close()


def test_scan_populates_caches(scanned):
    d, _ = scanned
    assert d.db.get_folder_cache(101)["cdash_folder_name"] == "Main St Folder"
    assert d.db.get_place_cache(55)["place_name"] == "Main Place"


def test_rescan_short_circuits_validator(scanned):
    d, validator = scanned
    folder_calls = len(validator.folder_calls)
    place_calls = len(validator.place_calls)
    d.scan_batch()  # second scan — everything should come from cache
    assert len(validator.folder_calls) == folder_calls
    assert len(validator.place_calls) == place_calls


def test_unparseable_folder_registered_not_ready(make_batch):
    """A media subfolder whose name can't be parsed is registered as a
    not-name-ready folder row (item_set_id NULL) rather than skipped."""
    root = make_batch("CDB260430-Test_batch")
    (root / "media" / "Nonsense").mkdir(parents=True)
    good = root / "media" / "F1-Main-OF101"
    good.mkdir(parents=True)
    _media(good, "Main", 1, 1, "VE", 55)

    validator = FakeValidator(folders={101: "Main St Folder"},
                              places={55: place_props("Main Place")})
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = validator
    d.scan_batch()

    folders = d.db.get_folders()
    bad = [f for f in folders if f["os_folder_name"] == "Nonsense"]
    assert len(bad) == 1
    assert bad[0]["name_ready"] is False
    assert bad[0]["item_set_id"] is None
    # An unparseable folder keeps the whole batch from being ready.
    assert d.db.get_batch()["ready"] is False
    d.close()


# ----------------------------------------- place ↔ folder association check

from cdash_digester.services.validation import PLACE_FOLDER_MISMATCH_NOTE


def test_place_associated_with_folder_helper(scanned):
    d, _ = scanned
    d.db.upsert_place(place_item_id=70, place_name="P", item_set_ids="101, 202")
    assoc = d._validation.place_associated_with_folder
    assert assoc(70, 101) is True
    assert assoc(70, 202) is True
    assert assoc(70, 303) is False
    # Empty / None item_set_ids → skip the check (treated as associated).
    d.db.upsert_place(place_item_id=71, place_name="Q", item_set_ids=None)
    d.db.upsert_place(place_item_id=72, place_name="R", item_set_ids="")
    assert assoc(71, 999) is True
    assert assoc(72, 999) is True


def _scan_one_file_with_item_sets(make_batch, item_set_ids):
    """Scan a folder OF101 holding one media file whose place (55) has the
    given item_set_ids. Returns the registered media row."""
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    _media(folder, "Main", 1, 1, "VE", 55)
    validator = FakeValidator(
        folders={101: "Main St Folder"},
        places={55: place_props("Main Place", item_set_ids=item_set_ids)},
    )
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = validator
    d.scan_batch()
    rows = d.db.get_media_for_folder(101)
    d.close()
    return rows


def test_scan_flags_place_not_associated_with_folder(make_batch):
    rows = _scan_one_file_with_item_sets(make_batch, item_set_ids="999")
    assert len(rows) == 1
    assert rows[0]["ready"] is False
    assert rows[0]["filename_issues"].startswith(PLACE_FOLDER_MISMATCH_NOTE)


def test_scan_accepts_place_associated_with_folder(make_batch):
    rows = _scan_one_file_with_item_sets(make_batch, item_set_ids="101")
    assert len(rows) == 1
    assert rows[0]["ready"] is True
    assert PLACE_FOLDER_MISMATCH_NOTE not in (rows[0]["filename_issues"] or "")


def test_assign_rejects_place_not_associated(scanned):
    d, _ = scanned
    media_ids = [m["media_id"] for m in d.db.get_media_for_folder(101)]
    # Place 60 exists in CDASH but is associated with a different folder.
    d._validator.places[60] = place_props("Other", item_set_ids="999")
    ok = d.assign_media_to_doc(media_ids, 60, "VE", True)
    assert ok is False


def test_assign_accepts_place_associated(scanned):
    d, _ = scanned
    media_ids = [m["media_id"] for m in d.db.get_media_for_folder(101)]
    d._validator.places[61] = place_props("Here", item_set_ids="101")
    ok = d.assign_media_to_doc(media_ids, 61, "VE", True)
    assert ok is True


def test_assign_preserves_media_status_and_notes(scanned):
    d, _ = scanned
    mid = d.db.get_media_for_folder(101)[0]["media_id"]
    # Simulate a format-rejected file: not ready, with a format note.
    d.db.set_media_status(mid, False, "Format rejected: something")
    d._validator.places[61] = place_props("Here", item_set_ids="101")

    assert d.assign_media_to_doc([mid], 61, "VE", True) is True

    row = d.db.get_media(mid)
    assert row["ready"] is False                          # status untouched
    assert row["filename_issues"] == "Format rejected: something"   # untouched
    assert row["doc_item_id"] is not None                 # but metadata applied


def test_export_csv_writes_catalog(scanned):
    d, _ = scanned
    d.export_csv()
    catalog = d.catalog_path
    for name in ("batch.csv", "folder.csv", "place.csv",
                 "document.csv", "media.csv"):
        assert (catalog / name).exists(), name
    with open(catalog / "media.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
