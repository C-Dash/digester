"""Tests for copying the bundled csv_mappings/ folder into a batch's catalog.

CatalogExportService._copy_csv_mappings() only touches the filesystem
(dig.catalog_path) and the log callback, so these tests drive it directly
without a populated database.
"""

from cdash_digester.digester import Digester


def _digester(batch_root):
    dig = Digester(batch_root)
    dig.catalog_path.mkdir(parents=True, exist_ok=True)
    return dig


def test_copy_csv_mappings_copies_tree(make_batch):
    batch = make_batch()
    dig = _digester(batch)

    dig._export._copy_csv_mappings()

    dest = dig.catalog_path / "csv_mappings"
    # A top-level file and a file inside the stage/ subfolder are both present,
    # confirming the subtree is recreated.
    assert (dest / "prod_create_doc_mappings.json").is_file()
    assert (dest / "stage" / "stage_create_place_mappings.json").is_file()


def test_copy_csv_mappings_does_not_overwrite_existing(make_batch):
    batch = make_batch()
    dig = _digester(batch)

    dig._export._copy_csv_mappings()

    edited = dig.catalog_path / "csv_mappings" / "prod_create_doc_mappings.json"
    edited.write_text("LOCAL EDIT", encoding="utf-8")

    # A second export must skip files that already exist.
    dig._export._copy_csv_mappings()

    assert edited.read_text(encoding="utf-8") == "LOCAL EDIT"
