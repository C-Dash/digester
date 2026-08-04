"""Characterization tests for RotateService: filtering to JPEG/TIFF files
that aren't flagged Reject, and rotating them end-to-end through a real scan.

Folder/file names get canonicalized during scan (renamed to the resolved
place/folder slug), so tests look up the actual on-disk names from the DB
rather than assuming the names used to build the fixture.
"""

from PIL import Image

from cdash_digester.digester import Digester
from conftest import (
    FakeValidator, place_props, make_tiff, make_multiframe_tiff, make_pdf,
)


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


def test_rotatable_media_ids_excludes_pdf_and_reject(make_batch):
    def build(folder):
        make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
                  compression="tiff_lzw")
        make_multiframe_tiff(folder / "Main_0002p0001-VE-OP55.tif", frames=2)
        make_pdf(folder / "Main_0003p0001-VE-OP55.pdf", pages=1)

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    rows = d.db.get_media_for_folder(101)
    all_ids = [r.media_id for r in rows]
    good_id = next(r.media_id for r in rows
                   if r.filename.endswith(".tif") and r.repair_issues != "Reject")

    assert d.rotatable_media_ids(all_ids) == [good_id]
    d.close()


def test_rotate_media_files_rotates_tiff_and_updates_dimensions(make_batch):
    def build(folder):
        make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB", size=(4, 8),
                  compression="tiff_lzw")

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    row = d.db.get_media_for_folder(101)[0]
    assert (row.pixel_width, row.pixel_height) == (4, 8)

    d.rotate_media_files([row.media_id], "cw")

    # Rescanned after rotation — dimensions should now be swapped.
    updated = d.db.get_media_for_folder(101)[0]
    assert (updated.pixel_width, updated.pixel_height) == (8, 4)

    os_folder_name = d.db.get_folder_by_item_set(101).os_folder_name
    filepath = d.media_path / os_folder_name / updated.filename
    with Image.open(filepath) as im:
        assert im.size == (8, 4)
    assert (filepath.parent / "repaired").exists()
    d.close()


def test_rotate_media_files_skips_reject_flagged(make_batch):
    def build(folder):
        make_multiframe_tiff(folder / "Main_0001p0001-VE-OP55.tif", frames=2)

    d = _scan_folder(make_batch, "F1-Main-OF101", build)
    row = d.db.get_media_for_folder(101)[0]
    os_folder_name = d.db.get_folder_by_item_set(101).os_folder_name
    filepath = d.media_path / os_folder_name / row.filename
    original_bytes = filepath.read_bytes()

    assert d.rotatable_media_ids([row.media_id]) == []
    d.rotate_media_files([row.media_id], "cw")

    assert filepath.read_bytes() == original_bytes
    assert not (filepath.parent / "repaired").exists()
    d.close()
