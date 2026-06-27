"""Shared pytest fixtures and helpers for the CDASH Digester test suite.

These tests are deterministic and network-free:
  - images/PDFs are synthesised at run time with Pillow / PyMuPDF (no binary
    fixtures committed);
  - the Omeka-S validator is replaced by an in-memory fake so scan tests never
    touch the network.

ExifTool is NOT required: exiftool_util.read_tags swallows failures and
returns {}, so capture dates simply fall back to the filesystem.

GUI tests run headless: QT_QPA_PLATFORM=offscreen is set here, before any Qt
import, so pytest-qt's qtbot never opens a real window.
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import fitz  # PyMuPDF
import pytest
from PIL import Image


# --------------------------------------------------------------------------- #
# Synthetic media factories
# --------------------------------------------------------------------------- #

def make_image(path: Path, mode: str = "RGB", size=(8, 8),
               *, fmt: str | None = None, **save_kwargs) -> Path:
    """Create a tiny image of the given PIL mode. fmt defaults from suffix."""
    img = Image.new(mode, size)
    img.save(str(path), format=fmt, **save_kwargs)
    return path


def make_tiff(path: Path, mode: str = "RGB", size=(8, 8),
              compression: str | None = "tiff_lzw") -> Path:
    """Single-frame TIFF. compression=None writes uncompressed (raw)."""
    img = Image.new(mode, size)
    if compression:
        img.save(str(path), format="TIFF", compression=compression)
    else:
        img.save(str(path), format="TIFF")
    return path


def make_multiframe_tiff(path: Path, frames: int = 2, size=(8, 8)) -> Path:
    base = Image.new("RGB", size)
    extra = [Image.new("RGB", size) for _ in range(frames - 1)]
    base.save(str(path), format="TIFF", save_all=True, append_images=extra,
              compression="tiff_lzw")
    return path


def make_pdf(path: Path, pages: int = 1) -> Path:
    """Plain (non-PDF/A) PDF with the given page count."""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


# --------------------------------------------------------------------------- #
# Fake validator — drop-in for cdash_digester.validator.CDASHValidator
# --------------------------------------------------------------------------- #

class FakeValidator:
    """In-memory stand-in for CDASHValidator.

    folders : {item_set_id: folder_name}     — present == valid
    places  : {place_id: props_dict}         — present == valid
    Records every lookup in .folder_calls / .place_calls so tests can assert
    cache short-circuiting (no repeated lookups).
    """

    def __init__(self, folders: dict | None = None, places: dict | None = None):
        self.folders = folders or {}
        self.places = places or {}
        self.folder_calls: list[int] = []
        self.place_calls: list[int] = []

    def validate_folder(self, item_set_id: int):
        self.folder_calls.append(item_set_id)
        if item_set_id in self.folders:
            return "Valid CDASH folder", self.folders[item_set_id]
        return f"Not found: item set ID {item_set_id}", ""

    def validate_place(self, place_id: int):
        self.place_calls.append(place_id)
        if place_id in self.places:
            return "Valid CDASH place", dict(self.places[place_id])
        return f"Not found: place ID {place_id}", {}

    def close(self):
        pass


def place_props(name: str, **overrides) -> dict:
    """A full place-props dict matching cdash_objects.PLACE_PROP_KEYS."""
    props = {
        "place_name": name, "place_type": "Building", "house_num": "10",
        "street_name": "Main St", "street_sort": "Main St 0010",
        "neighborhood": "Test", "chc_dist": None, "item_set_ids": None,
        "lat": 42.0, "lon": -71.0,
    }
    props.update(overrides)
    return props


# --------------------------------------------------------------------------- #
# Batch-folder fixture
# --------------------------------------------------------------------------- #

@pytest.fixture
def make_batch(tmp_path):
    """Return a builder that creates a CDB batch folder skeleton on disk.

    Usage:
        batch = make_batch("CDB260430-Test")
        folder = batch / "media" / "F1-Main_St-OF101"
        folder.mkdir(parents=True)
        ...add files...
    """
    def _build(name: str = "CDB260430-Test_batch") -> Path:
        root = tmp_path / name
        (root / "media").mkdir(parents=True)
        return root
    return _build
