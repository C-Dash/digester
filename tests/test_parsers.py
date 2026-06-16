"""Characterization tests for the name parsers and slugify in cdash_objects."""

import pytest

from cdash_digester.cdash_objects import (
    parse_batch_name, parse_folder_name, parse_media_name, slugify,
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


@pytest.mark.parametrize("bad", [
    "noindex-VE", "Mass_Ave_27p1-VE", "Mass_Ave_0027p0001-VEE",
])
def test_parse_media_name_rejects(bad):
    assert parse_media_name(bad) is None


# --------------------------------------------------------------------- slugify

@pytest.mark.parametrize("raw,expected", [
    ("Mass Ave", "Mass_Ave"),
    ("9 - 45 Brattle", "9-45_Brattle"),
    ("a/b:c*d", "abcd"),
    ("Keep-dash_and_underscore", "Keep-dash_and_underscore"),
])
def test_slugify(raw, expected):
    assert slugify(raw) == expected
