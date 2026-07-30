"""Characterization tests for BatchDB CRUD, the bool row factory, the three
persistent caches, and clear_working_tables. Pins behavior before Phase 1
splits BatchDB into a Database + repositories."""

import pytest

from cdash_digester.cdash_objects import BatchDB, PLACE_PROP_KEYS
from conftest import place_props


@pytest.fixture
def db(tmp_path):
    d = BatchDB(tmp_path / "batch_db.sqlite")
    d.create_all_tables()
    d.create_all_tables()  # idempotent — must not raise
    yield d
    d.close()


# --------------------------------------------------------------- batch/folder

def test_batch_upsert_is_singleton(db):
    db.upsert_batch("CDB1", "n1", "/p", "2026-01-01")
    db.upsert_batch("CDB1", "n2", "/p", "2026-01-01")
    batch = db.get_batch()
    assert batch["batch_id"] == "CDB1"
    assert batch["name"] == "n2"


def test_folder_upsert_and_index(db):
    db.upsert_folder(item_set_id=101, cdash_folder_name="Main",
                     os_folder_name="F1-Main-OF101", name_ready=True)
    db.assign_folder_index(101, 1)
    row = db.get_folder_by_item_set(101)
    assert row["cdash_folder_name"] == "Main"
    assert row["name_ready"] is True          # bool row factory
    assert row["folder_number"] == 1


# ----------------------------------------------------------------- bool cols

def test_media_ready_roundtrips_as_bool(db):
    # insert_media enforces the cdash_folder foreign key, so the folder first.
    db.upsert_folder(item_set_id=101, cdash_folder_name="Main",
                     os_folder_name="F1-Main-OF101")
    mid = db.insert_media(doc_item_id=None, item_set_id=101,
                          filename="a.tif", filepath="media/a.tif", ready=True)
    assert db.get_media(mid)["ready"] is True
    db.set_media_status(mid, False, "note")
    assert db.get_media(mid)["ready"] is False


# ------------------------------------------------------- clear_working_tables

def test_clear_working_tables_preserves_caches(db):
    db.upsert_batch("CDB1", "n", "/p", "2026-01-01")
    db.upsert_folder(item_set_id=101, cdash_folder_name="Main",
                     os_folder_name="F1-Main-OF101")
    db.insert_media(None, 101, "a.tif", "media/a.tif")
    db.upsert_folder_cache(101, "Main", 1, "valid")
    db.upsert_place_cache(55, place_props("X"), "valid")
    db.upsert_file_cache("media/a.tif", 10, 20, True, {"format_issues": ["OK"]})

    db.clear_working_tables()

    # Working tables emptied...
    assert db.get_folders() == []
    assert db.get_media_for_folder(101) == []
    # ...but batch and all three caches survive.
    assert db.get_batch() is not None
    assert db.get_folder_cache(101)["cdash_folder_name"] == "Main"
    assert db.get_place_cache(55)["place_name"] == "X"
    assert db.get_file_cache("media/a.tif")["accepted"] is True


# --------------------------------------------------------------- clear_caches

def test_clear_caches_empties_caches_only(db):
    db.upsert_batch("CDB1", "n", "/p", "2026-01-01")
    db.upsert_folder(item_set_id=101, cdash_folder_name="Main",
                     os_folder_name="F1-Main-OF101")
    db.upsert_folder_cache(101, "Main", 1, "valid")
    db.upsert_place_cache(55, place_props("X"), "valid")
    db.upsert_file_cache("media/a.tif", 10, 20, True, {"format_issues": ["OK"]})

    removed = db.clear_caches()

    assert removed == 3
    # All three caches emptied...
    assert db.get_folder_cache(101) is None
    assert db.get_place_cache(55) is None
    assert db.get_file_cache("media/a.tif") is None
    # ...but batch and working tables survive.
    assert db.get_batch() is not None
    assert db.get_folder_by_item_set(101) is not None


# ----------------------------------------------------------- folder cache

def test_folder_cache_max_index(db):
    assert db.max_folder_cache_index() == 0
    db.upsert_folder_cache(101, "A", 3, "valid")
    db.upsert_folder_cache(102, "B", 7, "valid")
    assert db.max_folder_cache_index() == 7


def test_folder_cache_upsert_overwrites(db):
    db.upsert_folder_cache(101, "Old", 1, "valid")
    db.upsert_folder_cache(101, "New", 2, "valid")
    row = db.get_folder_cache(101)
    assert row["cdash_folder_name"] == "New"
    assert row["folder_index"] == 2


# ------------------------------------------------------------- place cache

def test_place_cache_roundtrip_all_keys(db):
    db.upsert_place_cache(55, place_props("Foo", lat=1.5, lon=-2.5), "valid")
    row = db.get_place_cache(55)
    for k in PLACE_PROP_KEYS:
        assert k in row
    assert row["place_name"] == "Foo"
    assert row["lat"] == 1.5
    assert row["status"] == "valid"


# -------------------------------------------------------------- file cache

def test_file_cache_roundtrip_and_accepted_bool(db):
    props = {"file_size_mb": 1.0, "pixel_width": 8, "pixel_height": 8,
             "format": "RGB", "capture_date": "2026-01-01", "date_source": "fs",
             "format_issues": ["OK"], "repair_issues": [], "pdf_pages": None}
    db.upsert_file_cache("media/a.tif", 123, 456, True, props)
    row = db.get_file_cache("media/a.tif")
    assert row["file_size_bytes"] == 123
    assert row["mtime_ns"] == 456
    assert row["accepted"] is True
    assert row["format"] == "RGB"
    assert row["format_issues"] == "OK"


def test_file_cache_repair_issues_joined(db):
    props = {"repair_issues": ["wrong_compression", "rgba"]}
    db.upsert_file_cache("media/a.tif", 1, 2, False, props)
    assert db.get_file_cache("media/a.tif")["repair_issues"] == "wrong_compression, rgba"


def test_file_cache_path_rekey(db):
    db.upsert_file_cache("media/old.tif", 1, 2, True, {"format_issues": ["OK"]})
    db.update_file_cache_path("media/old.tif", "media/new.tif")
    assert db.get_file_cache("media/old.tif") is None
    assert db.get_file_cache("media/new.tif")["accepted"] is True


def test_file_cache_rekey_drops_stale_target(db):
    db.upsert_file_cache("media/old.tif", 1, 2, True, {"format_issues": ["OK"]})
    db.upsert_file_cache("media/new.tif", 9, 9, False, {"format_issues": ["stale"]})
    db.update_file_cache_path("media/old.tif", "media/new.tif")
    # The stale target row was replaced by the re-keyed one.
    assert db.get_file_cache("media/new.tif")["accepted"] is True
