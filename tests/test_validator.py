"""Tests for CDASHValidator — the Omeka-S REST client.

Network-free: the validator's requests.Session is replaced with a fake that
returns canned responses, so these exercise response handling (status codes,
the Omeka-S-Total-Results header, JSON shape) without touching the API.

This module had no coverage at all before; it is the only network-facing code
in the app, so it is the riskiest thing to refactor blind.
"""

import json

import pytest
import requests

from cdash_digester.validator import CDASHValidator


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, total=None, raises=None):
        self._payload = payload if payload is not None else []
        self.status_code = status_code
        self._raises = raises
        # Omeka-S reports match count in a header, not the body.
        if total is None:
            total = len(self._payload)
        self.headers = {"Omeka-S-Total-Results": str(total)}

    def json(self):
        if self._raises is not None:
            raise self._raises
        return self._payload


class _FakeSession:
    """Stands in for requests.Session, recording calls."""

    def __init__(self, response=None, raise_on_get=None):
        self.response = response
        self.raise_on_get = raise_on_get
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return self.response

    def close(self):
        pass


def _validator(response=None, raise_on_get=None):
    v = CDASHValidator()
    v.session = _FakeSession(response, raise_on_get)
    return v


def _place_item(name="Main Place", **extra):
    item = {
        "cdash:placeItem":   [{"@value": name}],
        "cdash:placeType":   [{"@value": "Building"}],
        "cdash:houseNum":    [{"@value": "10"}],
        "cdash:streetName":  [{"@value": "Main St"}],
        "cdash:streetSort":  [{"@value": "Main St 0010"}],
        "cdash:Neighborhood": [{"@value": "Test"}],
        "o-module-mapping:marker": [
            {"o-module-mapping:lat": "42.5", "o-module-mapping:lng": "-71.25"}
        ],
    }
    item.update(extra)
    return item


# --------------------------------------------------------------- validate_place

def test_validate_place_success_extracts_properties():
    v = _validator(_FakeResponse([_place_item("Mass Ave")]))
    status, props = v.validate_place(43296)

    assert "Valid" in status
    assert props["place_name"] == "Mass Ave"
    assert props["place_type"] == "Building"
    assert props["house_num"] == "10"
    assert props["lat"] == 42.5
    assert props["lon"] == -71.25
    # The place ID and template are sent as query params.
    assert v.session.calls[0]["params"]["id"] == 43296


def test_validate_place_not_found_when_total_is_zero():
    v = _validator(_FakeResponse([], total=0))
    status, props = v.validate_place(999)
    assert "Not found" in status
    assert props == {}


def test_validate_place_not_found_on_http_error():
    v = _validator(_FakeResponse([_place_item()], status_code=500, total=1))
    status, props = v.validate_place(1)
    assert "Not found" in status
    assert props == {}


def test_validate_place_reports_unreachable_api():
    v = _validator(raise_on_get=requests.exceptions.ConnectionError("no route"))
    status, props = v.validate_place(1)
    assert status.startswith("ERROR: API unreachable")
    assert props == {}


def test_validate_place_missing_fields_become_none():
    """A sparse item must not raise — absent properties come back as None."""
    v = _validator(_FakeResponse([{"cdash:placeItem": [{"@value": "Bare"}]}]))
    status, props = v.validate_place(7)
    assert "Valid" in status
    assert props["place_name"] == "Bare"
    assert props["place_type"] is None
    assert props["lat"] is None and props["lon"] is None


def test_validate_place_multi_value_fields_are_joined():
    v = _validator(_FakeResponse([_place_item(**{
        "cdash:Neighborhood": [{"@value": "North"}, {"@value": "South"}],
        "o:item_set": [{"o:id": 101}, {"o:id": 102}],
    })]))
    _, props = v.validate_place(1)
    assert props["neighborhood"] == "North, South"
    assert props["item_set_ids"] == "101, 102"


def test_validate_place_bad_coordinates_become_none():
    v = _validator(_FakeResponse([_place_item(**{
        "o-module-mapping:marker": [
            {"o-module-mapping:lat": "not-a-number",
             "o-module-mapping:lng": "-71.0"}
        ],
    })]))
    _, props = v.validate_place(1)
    assert props["lat"] is None
    assert props["lon"] == -71.0


# -------------------------------------------------------------- validate_folder

def test_validate_folder_success_returns_title():
    v = _validator(_FakeResponse([{"o:title": "Mass Ave Folder"}]))
    status, name = v.validate_folder(43111)
    assert "Valid" in status
    assert name == "Mass Ave Folder"


def test_validate_folder_not_found_when_total_is_zero():
    v = _validator(_FakeResponse([], total=0))
    status, name = v.validate_folder(404)
    assert "Not found" in status
    assert name == ""


def test_validate_folder_reports_unreachable_api():
    v = _validator(raise_on_get=requests.exceptions.Timeout("slow"))
    status, name = v.validate_folder(1)
    assert status.startswith("ERROR: API unreachable")
    assert name == ""


def test_validate_folder_untitled_item_is_undefined():
    v = _validator(_FakeResponse([{"o:id": 5}]))
    status, name = v.validate_folder(5)
    assert "Valid" in status
    assert name == "Undefined"


# ------------------------------------------------------------ validate_resource

def test_validate_resource_rejects_unknown_type():
    v = _validator(_FakeResponse([]))
    status, title = v.validate_resource("sandwich", 1)
    assert "Unknown resource type" in status
    assert title == "Undefined"
    assert v.session.calls == []   # never hits the network


@pytest.mark.parametrize("rtype,payload,expected", [
    ("place",  [{"cdash:placeItem": [{"@value": "P"}]}], "P"),
    ("folder", [{"o:title": "F"}], "F"),
    ("doc",    [{"o:title": "D"}], "D"),
])
def test_validate_resource_extracts_title_per_type(rtype, payload, expected):
    v = _validator(_FakeResponse(payload))
    status, title = v.validate_resource(rtype, 1)
    assert "Valid" in status
    assert title == expected


def test_validate_resource_http_error_is_reported():
    v = _validator(_FakeResponse([], status_code=404, total=1))
    status, title = v.validate_resource("place", 1)
    assert "HTTP 404" in status
    assert title == "Undefined"


def test_validate_resource_handles_invalid_json():
    v = _validator(_FakeResponse(
        [], total=1, raises=json.JSONDecodeError("bad", "doc", 0)))
    status, title = v.validate_resource("place", 1)
    assert status.startswith("ERROR")
    assert title == "Undefined"
