"""Tests for the domain model dataclasses, and that repositories return
entities (not plain dicts) for entity reads.

Entities are plain frozen dataclasses: attribute access only. They used to
carry a mapping shim (``row["field"]``, ``.get()``, ``keys()``/``items()``,
``dict(row)``) so callers could migrate incrementally; every caller now uses
attribute access and the shim is gone.
"""

import dataclasses

import pytest

from cdash_digester.models import Batch, Folder, Media, MediaWithDoc
from cdash_digester.cdash_objects import BatchDB


# ------------------------------------------------------------------ entities

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


def test_entities_are_attribute_accessed():
    m = Media.from_row({"media_id": 7, "filename": "a.tif", "ready": True})
    assert m.filename == "a.tif"
    assert m.media_id == 7
    assert m.ready is True
    # Unset fields fall back to their declared defaults, not KeyError.
    assert m.format is None


def test_entities_are_frozen():
    """Rows are read models; mutating one would not reach the database."""
    f = Folder.from_row({"item_set_id": 5, "cdash_folder_name": "X"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.cdash_folder_name = "Y"


def test_unknown_attribute_raises():
    b = Batch.from_row({"batch_id": "CDB1"})
    with pytest.raises(AttributeError):
        _ = b.does_not_exist


def test_mapping_shim_is_gone():
    """The dict-compatibility layer was a migration aid and has been removed.

    Keeping it would let dict-style access silently persist, which is what
    coupled every caller — including the whole GUI — to column-name strings
    rather than to the type.
    """
    m = Media.from_row({"media_id": 1})
    for attr in ("get", "keys", "values", "items"):
        assert not hasattr(m, attr), f"Row.{attr} should be gone"
    with pytest.raises(TypeError):
        _ = m["media_id"]
    with pytest.raises(TypeError):
        dict(m)


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

    # The joined read returns a typed entity too, so both accessors for the
    # same rows are substitutable. It used to return a plain dict, so
    # get_media() and get_media_for_folder() disagreed on shape.
    rows = db.get_media_for_folder(101)
    assert isinstance(rows[0], MediaWithDoc)
    assert isinstance(rows[0], Media)          # and still a Media
    assert rows[0].filename == "a.tif"
    # plus the joined columns Media alone does not carry
    assert hasattr(rows[0], "doc_type_code")
    assert hasattr(rows[0], "num_pages")
