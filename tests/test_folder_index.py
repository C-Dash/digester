"""Folder-index allocation: the on-disk name is authoritative.

The index in a folder's own name (`F3-…`) is what the scanner uses. The
persistent folder cache is only memory — it raises the allocation floor and
covers a folder that lost its prefix, but it no longer decides the number.

This matters beyond folder names: folder_index feeds batch_folder_id, which
feeds batch_doc_id / batch_media_id, which are exported as the `identifier`
and `Relation` columns of document.csv and media.csv. Renumbering silently
rewrites identifiers that may already be in Omeka.
"""

from cdash_digester.digester import Digester
from conftest import FakeValidator, make_tiff, place_props


def _batch(make_batch, folder_names, validator_folders, log=None):
    """Build a batch with the given media folders, one media file in each."""
    root = make_batch("CDB260430-Test_batch")
    for name in folder_names:
        folder = root / "media" / name
        folder.mkdir(parents=True)
        make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
                  compression="tiff_lzw")
    d = Digester(root, log=log or (lambda *a, **k: None))
    d.load_or_initialize()
    d._validator = FakeValidator(folders=validator_folders,
                                 places={55: place_props("Main Place")})
    return d


def _folders_by_item_set(d):
    return {f.item_set_id: f for f in d.db.get_folders()}


def test_purge_caches_does_not_renumber_or_rename(make_batch):
    """The bug: the folder cache was the only persistence of the index, so
    purging it restarted numbering at F1 and the next scan renamed folders on
    disk — changing exported identifiers with them."""
    d = _batch(make_batch, ["F3-Main-OF101"], {101: "Main St Folder"})
    d.scan_batch()

    before = _folders_by_item_set(d)[101]
    assert before.os_folder_name.startswith("F3-")

    d.purge_caches()
    d.scan_batch()

    after = _folders_by_item_set(d)[101]
    assert after.os_folder_name == before.os_folder_name
    assert after.batch_folder_id == before.batch_folder_id
    assert after.folder_number == before.folder_number
    d.close()


def test_media_identifiers_survive_a_purge(make_batch):
    """batch_media_id embeds the folder index, and is exported as media.csv's
    `identifier`. It must not change just because the cache was cleared.

    Scans twice before the purge to reach a steady state: the first scan
    computes batch_media_id from the pre-rename place slug and then renames
    the file, so scan 1 -> scan 2 legitimately differs. That drift is
    pre-existing and unrelated to the cache (it happens without a purge too);
    what this pins is that purging changes nothing.
    """
    d = _batch(make_batch, ["F3-Main-OF101"], {101: "Main St Folder"})
    d.scan_batch()
    d.scan_batch()
    before = [m.batch_media_id for m in d.db.get_media_for_folder(101)]

    d.purge_caches()
    d.scan_batch()
    after = [m.batch_media_id for m in d.db.get_media_for_folder(101)]

    assert before == after
    assert all("F3" in (bid or "") for bid in before)
    d.close()


def test_named_index_wins_over_empty_cache(make_batch):
    """A folder named F7-… keeps 7 on a first scan, with nothing cached."""
    d = _batch(make_batch, ["F7-Main-OF101"], {101: "Main St Folder"})
    d.scan_batch()
    assert _folders_by_item_set(d)[101].folder_number == 7
    assert _folders_by_item_set(d)[101].os_folder_name.startswith("F7-")
    d.close()


def test_new_folder_allocates_above_the_highest_on_disk(make_batch):
    """A folder with no index gets the next free number — which must clear the
    indices already on disk, not restart at 1."""
    d = _batch(make_batch, ["F7-Main-OF101", "Other-OF102"],
               {101: "Main St Folder", 102: "Other Folder"})
    d.scan_batch()

    folders = _folders_by_item_set(d)
    assert folders[101].folder_number == 7
    assert folders[102].folder_number == 8          # not 1, not 2
    assert folders[102].os_folder_name.startswith("F8-")
    d.close()


def test_unindexed_folder_is_still_named_and_renamed(make_batch):
    """Existing behaviour: a folder with no prefix gets one and is renamed."""
    d = _batch(make_batch, ["Main-OF101"], {101: "Main St Folder"})
    d.scan_batch()

    folder = _folders_by_item_set(d)[101]
    assert folder.folder_number == 1
    assert folder.os_folder_name == "F1-Main_St_Folder-OF101"
    d.close()


def test_duplicate_index_reassigns_the_later_folder(make_batch):
    """Two folders claiming F3: the first (sorted) keeps it, the second is
    reassigned and a warning names both.

    folder_number is INTEGER PRIMARY KEY on cdash_folder, so letting a
    duplicate through would raise IntegrityError — and duplicate
    batch_media_id values would collide on Omeka import.
    """
    logged = []
    d = _batch(make_batch, ["F3-Alpha-OF101", "F3-Beta-OF102"],
               {101: "Alpha Folder", 102: "Beta Folder"},
               log=lambda msg, lvl="info": logged.append((lvl, msg)))
    d.scan_batch()

    folders = _folders_by_item_set(d)
    assert folders[101].folder_number == 3          # first claimant keeps it
    assert folders[102].folder_number == 4          # reassigned
    assert folders[101].folder_number != folders[102].folder_number

    warnings = [m for lvl, m in logged if lvl == "warning"]
    assert any("Duplicate folder index F3" in m for m in warnings)
    assert any("F3-Beta-OF102" in m for m in warnings)
    d.close()


def test_cache_learns_the_name_derived_index(make_batch):
    """resolve_folder_name only writes the cache on an API miss, so the
    scanner syncs the index explicitly — otherwise the cache would stay stale
    and could hand the same number to a different folder later."""
    d = _batch(make_batch, ["F5-Main-OF101"], {101: "Main St Folder"})
    d.scan_batch()
    assert d.db.get_folder_cache(101)["folder_index"] == 5

    # A rescan with the cache already populated must keep it correct.
    d.scan_batch()
    assert d.db.get_folder_cache(101)["folder_index"] == 5
    d.close()
