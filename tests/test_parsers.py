"""Characterization tests for the name parsers and slugify in naming.py."""

import pytest

from cdash_digester.naming import (
    natural_key, parse_batch_name, parse_capture_name, parse_folder_name,
    parse_media_name, slugify,
)


# ------------------------------------------------------------------ batch name

def test_parse_batch_name_basic():
    r = parse_batch_name("CDB260320-Test_batch")
    assert r == {
        "batch_id": "CDB260320", "date": "260320",
        "letter": None, "name": "Test_batch",
    }


def test_parse_batch_name_with_letter():
    r = parse_batch_name("CDB260320a-Spring")
    assert r["batch_id"] == "CDB260320a"
    assert r["letter"] == "a"
    assert r["name"] == "Spring"


@pytest.mark.parametrize("bad", ["Test_batch", "CDB26032-x", "CDB260320", "cdb260320-x"])
def test_parse_batch_name_rejects(bad):
    assert parse_batch_name(bad) is None


# ----------------------------------------------------------------- folder name

def test_parse_folder_name_with_index():
    r = parse_folder_name("F6-Mass_Ave-OF43111")
    assert r == {"folder_index": 6, "slug": "Mass_Ave", "item_set_id": 43111}


def test_parse_folder_name_without_index():
    r = parse_folder_name("Mass_Ave-OF43111")
    assert r["folder_index"] is None
    assert r["item_set_id"] == 43111


def test_parse_folder_name_slug_keeps_internal_dashes():
    # The OF<id> suffix anchors the end, so dashes in the slug are preserved.
    r = parse_folder_name("F4-9-45_Brattle_St-OF151128")
    assert r["folder_index"] == 4
    assert r["slug"] == "9-45_Brattle_St"
    assert r["item_set_id"] == 151128


@pytest.mark.parametrize("bad", ["no_suffix", "F1-name-OFabc", "name-OF"])
def test_parse_folder_name_rejects(bad):
    assert parse_folder_name(bad) is None


# ------------------------------------------------------------------ media name

def test_parse_media_name_full():
    r = parse_media_name("Mass_Ave_0027p0001-VE-OP43296")
    assert r == {
        "place_slug": "Mass_Ave", "doc_index": 27, "page_index": 1,
        "doc_type": "VE", "place_id": 43296,
    }


def test_parse_media_name_without_place_id():
    r = parse_media_name("Mass_Ave_0027p0001-VE")
    assert r["place_id"] is None
    assert r["doc_type"] == "VE"


def test_parse_media_name_doc_type_uppercased():
    r = parse_media_name("x_0001p0001-ve-OP5")
    assert r["doc_type"] == "VE"


def test_parse_media_name_accepts_hyphen_delimiter():
    """The delimiter before doc_index may be '-' or '_'."""
    r = parse_media_name("Mass_Ave-0027p0001-VE-OP43296")
    assert r == {
        "place_slug": "Mass_Ave", "doc_index": 27, "page_index": 1,
        "doc_type": "VE", "place_id": 43296,
    }


@pytest.mark.parametrize("stem", [
    "Mass_Ave_27p1-VE",   # underscore delimiter, unpadded
    "Mass_Ave-27p1-VE",   # hyphen delimiter, unpadded
])
def test_parse_media_name_zero_padding_optional(stem):
    """doc_index/page_index need no zero padding, and padded and unpadded
    forms parse to the same integers."""
    r = parse_media_name(stem)
    assert r is not None
    assert (r["doc_index"], r["page_index"]) == (27, 1)
    padded = parse_media_name("Mass_Ave_0027p0001-VE")
    assert (r["doc_index"], r["page_index"]) == (padded["doc_index"],
                                                 padded["page_index"])


def test_parse_media_name_slug_keeps_internal_dashes_and_digits():
    # place_slug is greedy, so the trailing <digits>p<digits>-<TT> anchors the
    # parse even when the slug itself contains dashes and digits.
    r = parse_media_name("9-45_Brattle_St_3p2-VI")
    assert r["place_slug"] == "9-45_Brattle_St"
    assert (r["doc_index"], r["page_index"]) == (3, 2)


@pytest.mark.parametrize("bad", [
    "noindex-VE",              # no <digits>p<digits> group at all
    "Mass_Ave_0027p0001-VEE",  # doc_type must be exactly two letters
])
def test_parse_media_name_rejects(bad):
    assert parse_media_name(bad) is None


def test_parse_media_name_doc_type_is_optional():
    """The intermediate form: indexed, placed, but not yet typed. The scanner
    writes this and must be able to read it back on the next scan."""
    r = parse_media_name("Mass_Ave-0027p0001-OP43296")
    assert r == {
        "place_slug": "Mass_Ave", "doc_index": 27, "page_index": 1,
        "doc_type": None, "place_id": 43296,
    }


def test_op_token_is_not_mistaken_for_a_doc_type():
    """Guard for the ambiguity that making doc_type optional introduces:
    [A-Z]{2} can match the "OP" of "-OP43296", but the trailing digits then
    anchor nothing, so the match must backtrack to doc_type=None rather than
    leave place_id unparsed."""
    r = parse_media_name("Mass_Ave-0027p0001-OP43296")
    assert r["doc_type"] is None
    assert r["place_id"] == 43296


# ---------------------------------------------------------------- capture name

@pytest.mark.parametrize("stem,expected", [
    ("Mass_Ave-3-OP43296",    (3, None, 43296)),
    ("Mass_Ave-3-VE-OP43296", (3, "VE", 43296)),
    ("Mass_Ave-3",            (3, None, None)),
    ("Mass_Ave-3-VE",         (3, "VE", None)),
])
def test_parse_capture_name(stem, expected):
    r = parse_capture_name(stem)
    assert r["place_slug"] == "Mass_Ave"
    assert (r["capture_seq"], r["doc_type"], r["place_id"]) == expected


@pytest.mark.parametrize("bad", ["noseq", "Mass_Ave-", "Mass_Ave"])
def test_parse_capture_name_rejects(bad):
    assert parse_capture_name(bad) is None


def test_indexed_stems_are_not_capture_stems():
    """A canonical stem must never be re-read as a capture name — that would
    mint a second doc index for a file that already carries one."""
    for stem in ("Mass_Ave-0027p0001-VE-OP43296",
                 "Mass_Ave-0027p0001-OP43296",
                 "Mass_Ave-0027p0001"):
        assert parse_media_name(stem) is not None
        assert parse_capture_name(stem) is None


# ----------------------------------------------------------------- natural_key

def test_natural_key_orders_digit_runs_numerically():
    names = ["Slug-10", "Slug-9", "Slug-1", "Slug-2"]
    assert sorted(names, key=natural_key) == ["Slug-1", "Slug-2", "Slug-9", "Slug-10"]


def test_natural_key_agrees_with_plain_sort_on_padded_names():
    """Canonical names are zero-padded, so applying the natural key to a whole
    folder listing cannot reorder anything already canonical."""
    names = ["S-0001p0010-VE", "S-0001p0002-VE", "S-0002p0001-VE"]
    assert sorted(names, key=natural_key) == sorted(names)


# --------------------------------------------------------------------- slugify

@pytest.mark.parametrize("raw,expected", [
    ("Mass Ave", "Mass_Ave"),
    ("9 - 45 Brattle", "9-45_Brattle"),
    ("a/b:c*d", "abcd"),
    ("Keep-dash_and_underscore", "Keep-dash_and_underscore"),
])
def test_slugify(raw, expected):
    assert slugify(raw) == expected
