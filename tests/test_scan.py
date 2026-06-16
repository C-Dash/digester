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
