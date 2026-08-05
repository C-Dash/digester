"""The app assigns names with "-" between place slug and doc index.

The parser still accepts "_" there, so legacy names keep working; "-" is only
the form we now produce. Every fixture below deliberately feeds a legacy
"_" name in and asserts a "-" name out, which exercises both halves of that
transition at once.
"""

from cdash_digester.digester import Digester
from cdash_digester.naming import DOC_INDEX_DELIM, parse_media_name
from conftest import FakeValidator, make_tiff, place_props


def _scan(make_batch, filenames, places=None):
    root = make_batch("CDB260430-Test_batch")
    media = root / "media" / "F1-Main-OF101"
    media.mkdir(parents=True)
    for name in filenames:
        make_tiff(media / name, "RGB", compression="tiff_lzw")

    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = FakeValidator(
        folders={101: "Main St Folder"},
        places=places if places is not None else {55: place_props("Main Place")})
    d.scan_batch()
    return d


def test_legacy_underscore_name_is_renamed_to_the_dash_form(make_batch):
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"])

    row = d.db.get_media_for_folder(101)[0]
    assert row.filename == "Main_Place-0001p0001-VE-OP55.tif"
    # The underscores inside the place slug itself are untouched — only the
    # delimiter before the doc index changes.
    assert row.filename.startswith("Main_Place-")
    d.close()


def test_identifier_uses_the_same_delimiter_as_the_filename(make_batch):
    """Guards the mirroring property: batch_media_id is built to describe the
    canonical filename, so the two must not drift apart on delimiter."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"])

    row = d.db.get_media_for_folder(101)[0]
    stem = row.filename.rsplit(".", 1)[0]
    assert row.batch_media_id.endswith(stem.replace("-OP55", ""))
    assert f"Main_Place{DOC_INDEX_DELIM}0001p0001" in row.batch_media_id
    d.close()


def test_assigned_names_are_stable_on_rescan(make_batch):
    """A name already in the new form must not be renamed again."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"])
    first = d.db.get_media_for_folder(101)[0]

    d.scan_batch()
    second = d.db.get_media_for_folder(101)[0]

    assert second.filename == first.filename
    assert second.batch_media_id == first.batch_media_id
    d.close()


def test_assign_metadata_uses_the_dash_form(make_batch):
    """The second generation path — Assign Metadata renames independently of
    the scanner and must produce the same form."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif"])
    row = d.db.get_media_for_folder(101)[0]

    ok = d.assign_media_to_doc([row.media_id], 55, "VI", is_multi_page=True)
    assert ok

    after = d.db.get_media_for_folder(101)[0]
    assert f"Main_Place{DOC_INDEX_DELIM}" in after.filename
    assert "-VI-OP55" in after.filename
    assert f"Main_Place{DOC_INDEX_DELIM}" in after.batch_media_id
    d.close()


def test_assign_metadata_single_page_uses_the_dash_form(make_batch):
    """The single-page branch builds batch_media_id separately."""
    d = _scan(make_batch, ["Main_0001p0001-VE-OP55.tif",
                           "Main_0002p0001-VE-OP55.tif"])
    ids = [r.media_id for r in d.db.get_media_for_folder(101)]

    ok = d.assign_media_to_doc(ids, 55, "VI", is_multi_page=False)
    assert ok

    for row in d.db.get_media_for_folder(101):
        assert f"Main_Place{DOC_INDEX_DELIM}" in row.batch_media_id
        assert f"Main_Place{DOC_INDEX_DELIM}" in row.filename
    d.close()


def test_parser_accepts_dash_delimited_slug_with_dashes_and_digits():
    """place_slug is greedy, so the last valid split wins — the property that
    keeps "-" unambiguous even when the slug contains dashes and digits."""
    r = parse_media_name("9-45_Brattle_St-0003p0002-VI")
    assert r["place_slug"] == "9-45_Brattle_St"
    assert (r["doc_index"], r["page_index"]) == (3, 2)

    r = parse_media_name("Main-0002-0001p0001-VE")
    assert r["place_slug"] == "Main-0002"
    assert (r["doc_index"], r["page_index"]) == (1, 1)
