"""Field capture names evolve into canonical names across scans.

Batches arrive from the field named <slug>-<capture_seq>[-<DT>][-OP<place_id>],
which carries no document index. The scanner mints one, groups the run, renames
the files, and records what is still missing.

The grouping rule: a capture file that *repeats* -OP<place_id> opens a new
document; one that omits it is the next page of the run already open for that
slug. The capture_seq itself is only an ordering hint — page numbers always
restart at 1.
"""

from cdash_digester.digester import Digester
from conftest import FakeValidator, make_tiff, place_props


def _scan(make_batch, files, places, folder="F1-Main-OF101", root=None):
    """Scan one folder. `files` is a list of names, or (name, width) pairs."""
    if root is None:
        root = make_batch("CDB260430-Test_batch")
        (root / "media" / folder).mkdir(parents=True)
    media = root / "media" / folder
    for entry in files:
        name, width = entry if isinstance(entry, tuple) else (entry, 8)
        make_tiff(media / name, "RGB", size=(width, 8), compression="tiff_lzw")

    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = FakeValidator(folders={101: "Main St Folder"}, places=places)
    d.scan_batch()
    return d


def _rows(d):
    return sorted(d.db.get_media_for_folder(101), key=lambda m: m.filename)


# ------------------------------------------------------- case 1: no doc type

def test_capture_run_without_doc_type_becomes_one_indexed_document(make_batch):
    d = _scan(make_batch,
              ["Mass_Ave-1-OP55.tif", "Mass_Ave-2.tif", "Mass_Ave-3.tif"],
              {55: place_props("Main Place")})

    rows = _rows(d)
    assert [r.filename for r in rows] == [
        "Main_Place-0001p0001-OP55.tif",
        "Main_Place-0001p0002-OP55.tif",
        "Main_Place-0001p0003-OP55.tif",
    ]
    # One document, three pages.
    assert len({r.doc_item_id for r in rows}) == 1
    assert [r.page_num for r in rows] == [1, 2, 3]

    # Registered but not name-ready: the stem still has no -<DT> segment.
    for r in rows:
        assert r.name_ready is False
        assert r.ready is False
        assert "Needs Doc Type" in (r.filename_issues or "")

    doc = d.db.get_docs_for_folder(101)[0]
    assert doc.folder_doc_sequence == 1
    assert doc.doc_type_code is None
    d.close()


def test_case_one_names_are_stable_on_a_second_scan(make_batch):
    """The point of allowing a doc-type-less indexed name: rescanning must read
    the index back rather than mint a second one for the same file."""
    d = _scan(make_batch,
              ["Mass_Ave-1-OP55.tif", "Mass_Ave-2.tif", "Mass_Ave-3.tif"],
              {55: place_props("Main Place")})
    first = [(r.filename, r.batch_media_id, r.page_num) for r in _rows(d)]

    d.scan_batch()
    assert [(r.filename, r.batch_media_id, r.page_num) for r in _rows(d)] == first
    assert len(d.db.get_docs_for_folder(101)) == 1
    d.close()


# ---------------------------------------------------- case 2: with doc type

def test_capture_run_with_doc_type_is_name_ready(make_batch):
    d = _scan(make_batch,
              ["Mass_Ave-1-VE-OP55.tif", "Mass_Ave-2.tif", "Mass_Ave-3.tif"],
              {55: place_props("Main Place")})

    rows = _rows(d)
    assert [r.filename for r in rows] == [
        "Main_Place-0001p0001-VE-OP55.tif",
        "Main_Place-0001p0002-VE-OP55.tif",
        "Main_Place-0001p0003-VE-OP55.tif",
    ]
    # The doc type given on the first file propagates to the whole run.
    for r in rows:
        assert r.name_ready is True
        assert r.batch_media_id.endswith("-VE")
        assert "Needs Doc Type" not in (r.filename_issues or "")

    doc = d.db.get_docs_for_folder(101)[0]
    assert doc.doc_type_code == "VE"
    assert doc.batch_doc_id.endswith("Main_Place-0001-VE")
    d.close()


# ------------------------------------------------------- the grouping rule

def test_repeating_the_place_id_starts_a_new_document(make_batch):
    d = _scan(make_batch,
              ["Mass_Ave-1-VE-OP55.tif", "Mass_Ave-2.tif",
               "Mass_Ave-3-VE-OP55.tif", "Mass_Ave-4.tif"],
              {55: place_props("Main Place")})

    rows = _rows(d)
    assert [r.filename for r in rows] == [
        "Main_Place-0001p0001-VE-OP55.tif",
        "Main_Place-0001p0002-VE-OP55.tif",
        "Main_Place-0002p0001-VE-OP55.tif",
        "Main_Place-0002p0002-VE-OP55.tif",
    ]
    docs = d.db.get_docs_for_folder(101)
    assert [doc.folder_doc_sequence for doc in docs] == [1, 2]
    d.close()


def test_two_places_run_independently(make_batch):
    d = _scan(make_batch,
              ["Alpha-1-VE-OP55.tif", "Alpha-2.tif",
               "Beta-1-VE-OP66.tif", "Beta-2.tif"],
              {55: place_props("Alpha Place"), 66: place_props("Beta Place")})

    rows = _rows(d)
    assert [r.filename for r in rows] == [
        "Alpha_Place-0001p0001-VE-OP55.tif",
        "Alpha_Place-0001p0002-VE-OP55.tif",
        "Beta_Place-0002p0001-VE-OP66.tif",
        "Beta_Place-0002p0002-VE-OP66.tif",
    ]
    d.close()


def test_a_bare_capture_name_with_no_open_run_is_not_ready(make_batch):
    """No place ID and nothing to continue: there is no document identity to
    attach, so the file is registered untouched as not-ready."""
    d = _scan(make_batch, ["Orphan-1.tif"], {})

    row = _rows(d)[0]
    assert row.filename == "Orphan-1.tif"          # untouched
    assert row.doc_item_id is None
    assert row.name_ready is False
    assert "Name not in ready format" in (row.filename_issues or "")
    d.close()


# ----------------------------------------------- handoff to Assign Metadata

def test_assign_metadata_completes_a_case_one_run(make_batch):
    """The workflow the doc-type-less form exists to support: scan leaves the
    run indexed and flagged, the archivist supplies a type, and the names and
    the name_ready flag are completed without a rescan."""
    d = _scan(make_batch,
              ["Mass_Ave-1-OP55.tif", "Mass_Ave-2.tif"],
              {55: place_props("Main Place")})

    rows = _rows(d)
    assert all(r.name_ready is False for r in rows)

    assert d.assign_media_to_doc([r.media_id for r in rows], 55, "RF",
                                 is_multi_page=True) is True

    rows = _rows(d)
    assert [r.filename for r in rows] == [
        "Main_Place-0002p0001-RF-OP55.tif",
        "Main_Place-0002p0002-RF-OP55.tif",
    ]
    for r in rows:
        assert r.name_ready is True
        assert "Needs Doc Type" not in (r.filename_issues or "")
        assert r.batch_media_id.endswith("-RF")
    d.close()


def test_assignment_retracts_only_the_note_it_resolves(make_batch):
    """Assignment supplies a doc type, so it may retract "Needs Doc Type" — and
    nothing else. Other name-side notes rest on things it never re-examines."""
    d = _scan(make_batch, ["Mass_Ave-1-OP55.tif"], {55: place_props("Main Place")})

    row = _rows(d)[0]
    d.db.set_media_status(row.media_id, False,
                          "Needs Doc Type, Some other problem")

    assert d.assign_media_to_doc([row.media_id], 55, "RF",
                                 is_multi_page=True) is True

    row = d.db.get_media(row.media_id)
    assert row.filename_issues == "Some other problem"
    assert row.name_ready is True
    d.close()


# --------------------------------------------------------------- file order

def test_capture_runs_are_paged_in_numeric_order(make_batch):
    """Plain lexical sort puts Mass_Ave-10 before Mass_Ave-9, which would page
    the document wrongly — and the rename that follows is not reversible.

    Each file is made <capture_seq> pixels wide, so the page it ends up as can
    be checked after the capture_seq is gone from the name.
    """
    files = [("Mass_Ave-1-VE-OP55.tif", 1)]
    files += [(f"Mass_Ave-{n}.tif", n) for n in range(2, 13)]
    d = _scan(make_batch, files, {55: place_props("Main Place")})

    rows = _rows(d)
    assert len(rows) == 12
    for r in rows:
        assert r.page_num == r.pixel_width
    d.close()
