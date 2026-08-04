"""Tests for Session and the service-layer decoupling it enables.

The point of the Session is that a service no longer needs a whole Digester:
it takes the shared batch context plus whatever sibling services it uses. These
tests exercise services in isolation, which was impossible when every service
held a `dig` back-reference and reached into Digester's private attributes.
"""

import csv

import pytest

from cdash_digester.cdash_objects import BatchDB
from cdash_digester.services.scanning import ScanService
from cdash_digester.services.screening import ScreeningService
from cdash_digester.services.validation import ValidationService
from cdash_digester.session import Session
from conftest import FakeValidator, make_tiff, place_props


def _session(root, validator=None):
    """Session with an open DB — the part Digester.load_or_initialize does."""
    s = Session(root, log=lambda *a, **k: None, validator=validator)
    s.catalog_path.mkdir(parents=True, exist_ok=True)
    s.db = BatchDB(s.db_path)
    s.db.create_all_tables()
    return s


# ------------------------------------------------------------------- paths

def test_session_derives_batch_paths(tmp_path):
    s = Session(tmp_path / "CDB260430-Test_batch")
    assert s.catalog_path.name == "catalog"
    assert s.media_path.name == "media"
    assert s.rejects_path.name == "rejects"
    assert s.db_path == s.catalog_path / "batch_db.sqlite"


def test_session_log_is_swappable_at_runtime():
    """The GUI worker swaps the log sink mid-run; services read it live."""
    s = Session("CDB260430-X")
    seen = []
    s.log("before", "info")          # default sink swallows it
    s.log = lambda m, l: seen.append((m, l))
    s.log("after", "warning")
    assert seen == [("after", "warning")]


# ------------------------------------------------------------------ counts

def test_counts_summary_is_pure(tmp_path):
    """counts_summary formats only — no DB access, so it needs no session."""
    text = Session.counts_summary({
        "folders": 1, "places": 2, "documents": 3,
        "media": 4, "rejects": 5, "repaired": 6,
    })
    assert "Folders:   1" in text
    assert "Repaired:  6" in text


def test_read_counts_returns_zeros_without_a_csv(make_batch):
    s = Session(make_batch("CDB260430-Test_batch"))
    assert s.read_repair_reject_csv_counts() == (0, 0)


# ------------------------------------------- services without a Digester

def test_services_construct_from_a_session_alone(make_batch):
    """Each service takes the Session plus its explicit siblings — no facade."""
    s = _session(make_batch("CDB260430-Test_batch"))
    validation = ValidationService(s)
    screening = ScreeningService(s)
    scan = ScanService(s, validation, screening)
    assert scan._session is s
    s.db.close()


def test_scan_service_runs_standalone(make_batch):
    """A full scan driven by ScanService directly, with no Digester at all."""
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
              compression="tiff_lzw")

    s = _session(root, validator=FakeValidator(
        folders={101: "Main St Folder"}, places={55: place_props("Main Place")}))
    validation = ValidationService(s)
    scan = ScanService(s, validation, ScreeningService(s))

    scan.scan_batch()

    rows = s.db.get_media_for_folder(101)
    assert len(rows) == 1
    assert rows[0].ready is True
    s.db.close()


def test_compute_counts_does_not_write(make_batch, monkeypatch):
    """compute_counts is a pure read.

    Regression: get_status_summary() called collect_and_store_counts(), so
    asking for a status report wrote to the batch row — a query with a side
    effect.
    """
    s = _session(make_batch("CDB260430-Test_batch"))
    s.db.upsert_batch("CDB260430", "Test_batch", "/x", "2026-04-30")

    writes = []
    monkeypatch.setattr(s.db, "update_batch_counts",
                        lambda **kw: writes.append(kw))

    counts = s.compute_counts()
    assert set(counts) >= {"folders", "places", "documents",
                           "media", "rejects", "repaired"}
    assert writes == []          # nothing persisted

    s.collect_and_store_counts()
    assert len(writes) == 1      # the command variant does persist
    s.db.close()


def test_get_status_summary_is_read_only(make_batch, monkeypatch):
    from cdash_digester.digester import Digester
    root = make_batch("CDB260430-Test_batch")
    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()

    writes = []
    monkeypatch.setattr(d.db, "update_batch_counts",
                        lambda **kw: writes.append(kw))

    text = d.get_status_summary()
    assert "Batch Summary" in text
    assert writes == []
    d.close()
