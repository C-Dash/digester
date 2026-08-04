"""Tests for the domain model dataclasses and their mapping-compatibility,
and that repositories return entities (not plain dicts) for single-table reads.
"""

import pytest

from cdash_digester.models import Batch, Folder, Media
from cdash_digester.cdash_objects import BatchDB


# ----------------------------------------------------------------- Row mixin

def test_from_row_ignores_extra_columns():
    m = Media.from_row({"media_id": 1, "filename": "a.tif", "bogus": "x"})
    assert m.media_id == 1
    assert m.filename == "a.tif"


def test_from_row_none_passthrough():
    assert Media.from_row(None) is None


def test_media_has_no_date_source():
    """date_source is a prescreener/file-cache concern, not a media property.

    It was previously declared on Media and in the cdash_media schema but
    never passed to insert_media, so the column was permanently NULL.
    Dropped rather than wired up. It must stay tolerated as an input, since
    a cached props dict still carries it.
    """
    assert "date_source" not in Media._field_names()
    assert not hasattr(Media.from_row({"media_id": 1}), "date_source")
    # extra key must not raise — from_row takes only its own fields
    assert Media.from_row({"media_id": 1, "date_source": "exif"}).media_id == 1


def test_dual_access_attribute_and_mapping():
    m = Media.from_row({"media_id": 7, "filename": "a.tif", "ready": True})
    assert m.filename == "a.tif"       # attribute
    assert m["filename"] == "a.tif"    # legacy item access
    assert m.get("missing") is None    # legacy .get with default
    assert m.get("missing", "d") == "d"
    assert "ready" in m                # __contains__ over field names
    assert m["media_id"] == 7


def test_mapping_unpacking_and_dict():
    f = Folder.from_row({"item_set_id": 5, "cdash_folder_name": "X",
                         "name_ready": True})
    as_dict = dict(f)
    assert as_dict["item_set_id"] == 5
    assert as_dict["cdash_folder_name"] == "X"
    # ** unpacking works because keys()/__getitem__ are provided
    assert {**f}["name_ready"] is True


def test_missing_key_raises_keyerror():
    b = Batch.from_row({"batch_id": "CDB1"})
    with pytest.raises(KeyError):
        _ = b["does_not_exist"]


# -------------------------------------------------- repositories return entities

@pytest.fixture
def db(tmp_path):
    d = BatchDB(tmp_path / "batch_db.sqlite")
    d.create_all_tables()
    yield d
    d.close()


def test_repo_reads_return_dataclasses(db):
    db.upsert_batch("CDB1", "n", "/p", "2026-01-01")
    db.upsert_folder(item_set_id=101, cdash_folder_name="Main",
                     os_folder_name="F1-Main-OF101", name_ready=True)
    mid = db.insert_media(None, 101, "a.tif", "media/a.tif", ready=True)

    assert isinstance(db.get_batch(), Batch)
    assert isinstance(db.get_folder_by_item_set(101), Folder)
    assert all(isinstance(f, Folder) for f in db.get_folders())
    assert isinstance(db.get_media(mid), Media)
    # Joined read stays a plain dict (carries doc_type_code from the join).
    rows = db.get_media_for_folder(101)
    assert isinstance(rows[0], dict)
    assert "doc_type_code" in rows[0]
