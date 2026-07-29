"""Characterization tests for RejectService: moving Reject-flagged media files
to the top-level rejects/ archive, rescanning the affected folder, and
cleaning up a media folder that becomes empty as a result.

Folder/file names get canonicalized during scan (renamed to the resolved
place/folder slug), so tests look up the actual on-disk names from the DB
rather than assuming the names used to build the fixture.
"""

import csv

from cdash_digester.digester import Digester
from conftest import FakeValidator, place_props, make_tiff, make_multiframe_tiff


def _scan_folder(make_batch, folder_name, build_files):
    """Build one folder under media/, populate it via build_files(folder),
    scan the batch, and return the open Digester."""
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / folder_name
    folder.mkdir(parents=True)
    build_files(folder)

    validator = FakeValidator(folders={101: "Main St Folder"},
                              places={55: place_props("Main Place")})
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = validator
    d.scan_batch()
    return d


def test_rejectable_media_ids_filters_to_reject_flag(make_batch):
    def build(folder):
        make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
                  compression="tiff_lzw")
        make_multiframe_tiff(folder / "Main_0002p0001-VE-OP55.tif", frames=2)

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    rows = d.db.get_media_for_folder(101)
    all_ids = [r["media_id"] for r in rows]
    bad_ids = [r["media_id"] for r in rows if r["repair_issues"] == "Reject"]

    assert d.rejectable_media_ids(all_ids) == bad_ids
    d.close()


def test_reject_moves_file_and_keeps_folder_with_siblings(make_batch):
    def build(folder):
        make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
                  compression="tiff_lzw")
        make_multiframe_tiff(folder / "Main_0002p0001-VE-OP55.tif", frames=2)

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    rows = d.db.get_media_for_folder(101)
    good_row = next(r for r in rows if r["repair_issues"] != "Reject")
    bad_row = next(r for r in rows if r["repair_issues"] == "Reject")
    os_folder_name = d.db.get_folder_by_item_set(101)["os_folder_name"]

    d.reject_media_files([bad_row["media_id"]])

    dest = d.rejects_path / os_folder_name / bad_row["filename"]
    assert dest.exists()
    src_folder = d.media_path / os_folder_name
    assert src_folder.is_dir()   # sibling file still there — not deleted
    assert (src_folder / good_row["filename"]).exists()
    assert not (src_folder / bad_row["filename"]).exists()

    remaining = d.db.get_media_for_folder(101)
    assert [r["filename"] for r in remaining] == [good_row["filename"]]
    d.close()


def test_reject_removes_now_empty_source_folder(make_batch):
    def build(folder):
        make_multiframe_tiff(folder / "Main_0001p0001-VE-OP55.tif", frames=2)

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    bad_row = d.db.get_media_for_folder(101)[0]
    os_folder_name = d.db.get_folder_by_item_set(101)["os_folder_name"]

    d.reject_media_files([bad_row["media_id"]])

    dest = d.rejects_path / os_folder_name / bad_row["filename"]
    assert dest.exists()
    assert not (d.media_path / os_folder_name).exists()
    assert d.db.get_media_for_folder(101) == []
    d.close()


def test_reject_appends_rejects_csv_row(make_batch):
    def build(folder):
        make_multiframe_tiff(folder / "Main_0001p0001-VE-OP55.tif", frames=2)

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    bad_row = d.db.get_media_for_folder(101)[0]

    d.reject_media_files([bad_row["media_id"]])

    csv_path = d.catalog_path / "rejects.csv"
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["Filename"] == bad_row["filename"]
    assert rows[0]["Repair_Action"] == "Rejected"
    assert rows[0]["Repair_Issues"] == bad_row["format_note"]
    d.close()


def test_reject_skips_non_reject_media(make_batch):
    def build(folder):
        make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
                  compression="tiff_lzw")

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    good_row = d.db.get_media_for_folder(101)[0]
    os_folder_name = d.db.get_folder_by_item_set(101)["os_folder_name"]

    assert d.rejectable_media_ids([good_row["media_id"]]) == []

    d.reject_media_files([good_row["media_id"]])
    src = d.media_path / os_folder_name / good_row["filename"]
    assert src.exists()   # untouched — not flagged Reject
    assert not d.rejects_path.exists()
    d.close()
