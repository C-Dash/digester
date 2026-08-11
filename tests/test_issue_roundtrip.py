"""A cache hit must reproduce exactly what filled it.

repair_issues had a join helper but no split, so ScreeningService read cached
values back through repair_media.parse_repair_issues — which lower-cases every
code, because it exists to make matching case-insensitive. A file therefore
showed "Reject" on the scan that screened it and "reject" on every scan after,
once the value came from cdash_file_cache. The value written to the cache was
always canonical; only the read path changed it.
"""

import pytest

from cdash_digester.models import (
    join_repair_issues, split_format_issues, split_repair_issues,
)
from cdash_digester.repair_media import parse_repair_issues
from cdash_digester.digester import Digester
from conftest import FakeValidator, make_pdf, make_tiff, place_props


# ------------------------------------------------------------- the helpers

@pytest.mark.parametrize("issues", [
    [],
    ["Reject"],
    ["Flatten"],
    ["Compress LZW", "Check MBs"],
])
def test_split_repair_issues_inverts_join(issues):
    assert split_repair_issues(join_repair_issues(issues)) == issues


def test_split_repair_issues_preserves_case():
    assert split_repair_issues("Reject") == ["Reject"]
    assert split_repair_issues("Compress LZW, Check MBs") == [
        "Compress LZW", "Check MBs"]


def test_parse_repair_issues_still_normalises_for_matching():
    """The other half of the split of duties: parse_ stays lossy on purpose,
    so `"reject" in ...` keeps working however the code was spelled."""
    assert parse_repair_issues("Reject") == ["reject"]
    assert parse_repair_issues("multiframe-tiff") == ["multiframe_tiff"]


def test_parse_repair_issues_folds_spaces_like_hyphens():
    """Spaces normalize to "_" exactly as hyphens do. They did not, so the
    prescreener's "Compress LZW" became "compress lzw" while repair_media
    matched "compress_lzw" — see tests/test_repair_vocabulary.py, which owns
    that invariant now."""
    assert parse_repair_issues("Compress LZW") == ["compress_lzw"]
    assert parse_repair_issues("Check MBs") == ["check_mbs"]


@pytest.mark.parametrize("value", [None, ""])
def test_split_repair_issues_handles_empty(value):
    assert split_repair_issues(value) == []


# --------------------------------------------------------- through a scan

@pytest.fixture
def batch_with_issues(make_batch):
    """A batch holding one file per repair-issue flavour."""
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    # Plain PDF -> Reject. Uncompressed RGBA TIFF -> Flatten + Compress LZW.
    make_pdf(folder / "Main_Place-0001p0001-AM-OP55.pdf")
    make_tiff(folder / "Main_Place-0002p0001-VE-OP55.tif", "RGBA",
              compression=None)

    d = Digester(root, log=lambda *a, **k: None)
    d.load_or_initialize()
    d._validator = FakeValidator(folders={101: "Main St Folder"},
                                 places={55: place_props("Main Place")})
    yield d
    d.close()


def _issue_map(d):
    return {m.filename: m.repair_issues
            for m in d.db.get_media_for_folder(101)}


def test_repair_issues_are_identical_on_a_rescan(batch_with_issues):
    """The regression: scan 2 is served from cdash_file_cache and must agree
    with scan 1 character for character."""
    d = batch_with_issues
    d.scan_batch()
    first = _issue_map(d)

    d.scan_batch()
    assert _issue_map(d) == first


def test_repair_issue_codes_keep_their_canonical_spelling(batch_with_issues):
    d = batch_with_issues
    d.scan_batch()
    d.scan_batch()          # force the cache-hit path

    issues = _issue_map(d)
    pdf = [v for k, v in issues.items() if k.endswith(".pdf")][0]
    assert pdf == "Reject"        # not "reject"


def test_format_issues_are_identical_on_a_rescan(batch_with_issues):
    """format_issues already had a matching split helper, so it never drifted.
    Pinned alongside so the pair cannot regress separately."""
    d = batch_with_issues
    d.scan_batch()
    first = {m.filename: split_format_issues(m.format_issues)
             for m in d.db.get_media_for_folder(101)}

    d.scan_batch()
    assert {m.filename: split_format_issues(m.format_issues)
            for m in d.db.get_media_for_folder(101)} == first
