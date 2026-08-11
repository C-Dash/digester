"""doc_index is the one document number, and it is never re-used.

The scanner used to keep a second, private document counter (`doc_seq`) that
restarted at 0 on every folder scan, and built batch_doc_id/batch_media_id from
it while renaming the file from the *filename's* doc_index. The two agreed only
when a folder happened to be numbered from 0001, so any other batch exported
identifiers that did not describe their own files.

Now folder_doc_sequence, the filename's <doc_index>, and the number inside both
identifiers are a single value, allocated by BatchDB.next_doc_index().
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


# --------------------------------------------------------------- the core bug

def test_identifiers_use_the_filenames_doc_index(make_batch):
    """A folder numbered from 0027, not 0001.

    The old scan-order counter made this file's identifiers claim index 0001
    while the file on disk carried 0027.
    """
    d = _scan(make_batch, ["Main_Place-0027p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})

    row = d.db.get_media_for_folder(101)[0]
    doc = d.db.get_docs_for_folder(101)[0]

    assert row.filename == "Main_Place-0027p0001-VE-OP55.tif"
    assert row.batch_media_id.endswith("Main_Place-0027p0001-VE")
    assert doc.batch_doc_id.endswith("Main_Place-0027-VE")
    # The number stored on the doc row is the same one, not a scan ordinal.
    assert doc.folder_doc_sequence == 27
    d.close()


def test_sparse_indices_are_each_preserved(make_batch):
    """Three documents numbered 3, 7 and 42 stay 3, 7 and 42."""
    d = _scan(make_batch,
              ["Main_Place-0003p0001-VE-OP55.tif",
               "Main_Place-0007p0001-VI-OP55.tif",
               "Main_Place-0042p0001-RF-OP55.tif"],
              {55: place_props("Main Place")})

    docs = d.db.get_docs_for_folder(101)
    assert [doc.folder_doc_sequence for doc in docs] == [3, 7, 42]
    for doc, idx, dt in zip(docs, (3, 7, 42), ("VE", "VI", "RF")):
        assert doc.batch_doc_id.endswith(f"Main_Place-{idx:04d}-{dt}")
    d.close()


# ------------------------------------------------------------------ non-reuse

def test_next_doc_index_is_max_in_use_plus_one(make_batch):
    """Minting never re-uses a number, and never fills a gap either."""
    d = _scan(make_batch,
              ["Main_Place-0003p0001-VE-OP55.tif",
               "Main_Place-0007p0001-VI-OP55.tif"],
              {55: place_props("Main Place")})

    assert d.db.next_doc_index(101) == 8
    d.close()


def test_next_doc_index_starts_at_one_for_an_empty_folder(make_batch):
    d = _scan(make_batch, [], {})
    assert d.db.next_doc_index(101) == 1
    d.close()


def test_indices_survive_a_full_rescan(make_batch):
    """The filenames are the persistence: a rescan wipes cdash_doc, then
    rebuilds folder_doc_sequence from the names on disk, so the high-water
    mark — and therefore the next mint — is unchanged."""
    d = _scan(make_batch, ["Main_Place-0031p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})
    before = d.db.next_doc_index(101)

    d.scan_batch()
    assert d.db.next_doc_index(101) == before == 32
    d.close()
