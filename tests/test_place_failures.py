"""A place ID that will not resolve must not derail the scan.

ensure_place only inserts a cdash_place row on success, so writing the raw
place_id into cdash_doc.place_item_id (a foreign key) raised IntegrityError
and aborted the entire batch over a single typo'd filename. The file should
instead be registered not-ready with a note, and left untouched on disk.
"""

from cdash_digester.digester import Digester
from cdash_digester.services.validation import place_failure_note
from conftest import FakeValidator, make_tiff, place_props


class _UnreachableValidator(FakeValidator):
    """Every place lookup fails the way a network outage does.

    conftest.FakeValidator only ever reports "Not found", which is the other
    failure class — the one that means the filename is wrong.
    """

    def validate_place(self, place_id: int):
        self.place_calls.append(place_id)
        return "ERROR: API unreachable — connection refused", {}


def _scan(make_batch, filenames, places, validator=None):
    root = make_batch("CDB260430-Test_batch")
    media = root / "media" / "F1-Main-OF101"
    media.mkdir(parents=True)
    for name in filenames:
        make_tiff(media / name, "RGB", compression="tiff_lzw")

    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = validator or FakeValidator(
        folders={101: "Main St Folder"}, places=places)
    d.scan_batch()
    return d


def _by_name(d, fragment):
    return next(m for m in d.db.get_media_for_folder(101) if fragment in m.filename)


# --------------------------------------------------------------- the crash

def test_bad_place_does_not_abort_the_scan(make_batch):
    """The bug: one unresolvable place ID stopped the whole batch with
    sqlite3.IntegrityError, so nothing at all was registered."""
    d = _scan(make_batch,
              ["Main_0001p0001-VE-OP999.tif",     # 999 is not in the validator
               "Main_0002p0001-VE-OP55.tif"],     # this one is fine
              {55: place_props("Main Place")})

    rows = d.db.get_media_for_folder(101)
    assert len(rows) == 2, "the good file must still be scanned"
    assert any(r.ready for r in rows), "the valid file should be ready"
    d.close()


# ------------------------------------------------------- the bad file itself

def test_bad_place_is_flagged_not_ready_with_a_note(make_batch):
    d = _scan(make_batch, ["Main_0001p0001-VE-OP999.tif"], {})

    row = _by_name(d, "OP999")
    assert row.ready is False
    assert "Place ID 999 does not exist in Omeka." in (row.filename_issues or "")
    d.close()


def test_bad_place_file_is_not_renamed(make_batch):
    """Nothing is canonicalised on the strength of metadata we could not
    confirm — including the index padding the rename would normalise."""
    d = _scan(make_batch, ["Main_27p1-VE-OP999.tif"], {})

    row = _by_name(d, "OP999")
    assert row.filename == "Main_27p1-VE-OP999.tif"
    assert (d.media_path / "F1-Main_St_Folder-OF101"
            / "Main_27p1-VE-OP999.tif").exists()
    d.close()


def test_bad_place_document_carries_no_place(make_batch):
    """The doc must not reference a place that was never created, and no
    bogus place row may exist to reach place.csv."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP999.tif"], {})

    docs = d.db.get_docs_for_folder(101)
    assert len(docs) == 1
    assert docs[0].place_item_id is None
    assert d.db.get_place(999) is None
    d.close()


# ------------------------------------------------------------ no cascading

def test_bad_place_is_not_inherited_by_a_sibling(make_batch):
    """slug_place_tracker must only remember places that resolved, or one
    typo would contaminate every later file sharing that place slug."""
    d = _scan(make_batch,
              ["Main_0001p0001-VE-OP999.tif",   # bad place
               "Main_0002p0001-VE.tif"],        # same slug, no -OP token
              {})

    sibling = _by_name(d, "0002p0001")
    assert "No place ID in filename" in (sibling.filename_issues or "")
    assert "does not exist in Omeka" not in (sibling.filename_issues or "")
    d.close()


# ---------------------------------------------------- the two failure modes

def test_unreachable_api_reads_differently(make_batch):
    """An outage must not be reported as a nonexistent place — that would
    invite an archivist to 'correct' a filename that was already right."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"], {},
              validator=_UnreachableValidator(folders={101: "Main St Folder"}))

    row = _by_name(d, "OP55")
    assert row.ready is False
    issues = row.filename_issues or ""
    assert "could not be verified (Omeka unreachable)" in issues
    assert "does not exist" not in issues
    d.close()


def test_place_failure_note_wording():
    assert place_failure_note(999, "Not found: place ID 999") == (
        "Place ID 999 does not exist in Omeka.")
    assert place_failure_note(55, "ERROR: API unreachable — boom") == (
        "Place ID 55 could not be verified (Omeka unreachable).")


# ------------------------------------------------------- unchanged behaviour

def test_valid_place_still_renames_and_is_ready(make_batch):
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"],
              {55: place_props("Main Place")})

    row = d.db.get_media_for_folder(101)[0]
    assert row.filename == "Main_Place_0001p0001-VE-OP55.tif"
    assert row.ready is True
    assert not (row.filename_issues or "")
    d.close()
