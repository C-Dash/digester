"""Tests for the Digester facade query methods and repairable_media_ids."""

import pytest

from cdash_digester.digester import Digester
from cdash_digester.models import Batch, Folder, Media


def test_query_methods_safe_when_not_open(tmp_path):
    d = Digester(tmp_path / "CDB260430-Test", log=lambda *a, **k: None)
    # No batch opened yet.
    assert d.is_open is False
    assert d.get_batch() is None
    assert d.get_folders() == []
    assert d.get_folder(1) is None
    assert d.get_media(1) is None
    assert d.get_media_for_folder(1) == []
    assert d.repairable_media_ids([1, 2]) == []


@pytest.fixture
def opened(make_batch):
    root = make_batch("CDB260430-Test_batch")
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d.db.upsert_folder(item_set_id=101, cdash_folder_name="Main",
                       os_folder_name="F1-Main-OF101", name_ready=True)
    yield d
    d.close()


def test_query_methods_return_entities(opened):
    d = opened
    assert d.is_open is True
    assert isinstance(d.get_batch(), Batch)
    assert isinstance(d.get_folder(101), Folder)
    assert all(isinstance(f, Folder) for f in d.get_folders())


def test_repairable_media_ids_filters_by_issues(opened):
    d = opened
    with_issue = d.db.insert_media(None, 101, "a.tif", "media/a.tif",
                                   repair_issues="rgba, wrong_compression")
    no_issue = d.db.insert_media(None, 101, "b.tif", "media/b.tif",
                                 repair_issues="")
    result = d.repairable_media_ids([with_issue, no_issue])
    assert result == [with_issue]
    assert isinstance(d.get_media(with_issue), Media)


def test_purge_caches_empties_the_caches(opened):
    d = opened
    d.db.upsert_folder_cache(101, "Main", 1, "valid")
    d.db.upsert_file_cache("media/a.tif", 10, 20, True, {"qa_note": "OK"})
    d.purge_caches()
    assert d.db.get_folder_cache(101) is None
    assert d.db.get_file_cache("media/a.tif") is None
