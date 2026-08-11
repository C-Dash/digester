"""The prescreener's codes must be the codes repair_media acts on.

The two ends of the repair vocabulary were bare string literals and they
disagreed: the prescreener raised "Compress LZW" / "Check MBs", repair_media
tested for "compress_lzw" / "check_mbs", and normalization folded hyphens but
not spaces — so neither branch ever ran on a real file.

LZW still got applied (the TIFF save path applies it unconditionally), which is
why nothing looked broken. The "Check MBs" size re-check did not: its whole job
is to REFUSE a file that is still over the limit after compression, and instead
every such file was committed and reported as repaired.

The existing repair tests all pass the underscore spelling directly, so they
could not catch it. These drive repair_file with whatever screen_file actually
produced, which is the only arrangement that pins the two ends together.
"""

import pytest

from cdash_digester import prescreener, repair_media
from cdash_digester.constants import (
    CHECK_MBS_TOKEN, COMPRESS_LZW_TOKEN, FLATTEN_TOKEN,
    MULTIFRAME_TIFF_TOKEN, REJECT_TOKEN,
    REPAIR_CHECK_MBS, REPAIR_COMPRESS_LZW, REPAIR_FLATTEN, REPAIR_REJECT,
    REPAIR_MULTIFRAME_TIFF, normalize_repair_issue,
)
from cdash_digester.repair_media import parse_repair_issues, repair_file
from conftest import make_tiff


# --------------------------------------------------- vocabulary agreement

@pytest.mark.parametrize("code,token", [
    (REPAIR_REJECT, REJECT_TOKEN),
    (REPAIR_FLATTEN, FLATTEN_TOKEN),
    (REPAIR_COMPRESS_LZW, COMPRESS_LZW_TOKEN),
    (REPAIR_CHECK_MBS, CHECK_MBS_TOKEN),
    (REPAIR_MULTIFRAME_TIFF, MULTIFRAME_TIFF_TOKEN),
])
def test_each_code_normalises_to_the_token_used_to_match_it(code, token):
    """The invariant the bug violated. Every code raised by the prescreener
    must reduce to the token repair_media tests for."""
    assert normalize_repair_issue(code) == token
    assert parse_repair_issues(code) == [token]


@pytest.mark.parametrize("spelling", [
    "Compress LZW", "compress lzw", "compress-lzw", "compress_lzw",
    "  COMPRESS   LZW  ",
])
def test_separators_and_case_all_fold_together(spelling):
    """Spaces fold to "_" as hyphens always did — that asymmetry was the bug."""
    assert normalize_repair_issue(spelling) == COMPRESS_LZW_TOKEN


def test_tokens_use_underscores_not_spaces():
    """Guard against fixing this by changing the match tokens to spaces
    instead, which would break stored hyphen/underscore spellings."""
    for token in (COMPRESS_LZW_TOKEN, CHECK_MBS_TOKEN, MULTIFRAME_TIFF_TOKEN):
        assert " " not in token
        assert "-" not in token


# ------------------------------------- prescreener output drives the repair

def test_compress_lzw_from_the_prescreener_is_reported_as_applied(tmp_path):
    """Not just "did the file end up LZW" — it always did, because the TIFF
    save path compresses unconditionally. What was missing is the branch that
    records the action, so the log said "Repaired: " with nothing after it."""
    p = make_tiff(tmp_path / "plain.tif", "RGB", compression=None)
    accepted, props = prescreener.screen_file(p)
    assert props["repair_issues"] == [REPAIR_COMPRESS_LZW]

    ok, msg = repair_file(p, props["repair_issues"])
    assert ok is True
    assert "lzw compression applied" in msg


def test_flatten_from_the_prescreener_still_works(tmp_path):
    p = make_tiff(tmp_path / "rgba.tif", "RGBA", compression="tiff_lzw")
    accepted, props = prescreener.screen_file(p)
    assert REPAIR_FLATTEN in props["repair_issues"]

    ok, msg = repair_file(p, props["repair_issues"])
    assert ok is True
    assert "rgba->rgb" in msg


def test_check_mbs_refuses_a_file_that_is_still_oversized(tmp_path, monkeypatch):
    """The bug that mattered. The refusal path was unreachable, so a file that
    is still over the limit after compression was committed and reported as
    repaired. CLAUDE.md promises the original is left completely untouched."""
    p = make_tiff(tmp_path / "big.tif", "RGB", size=(600, 600), compression=None)
    # Force the limit under the compressed size so the guard must trigger.
    monkeypatch.setattr(prescreener, "MAX_FILE_MB", 0.0001)

    accepted, props = prescreener.screen_file(p)
    assert props["repair_issues"] == [REPAIR_COMPRESS_LZW, REPAIR_CHECK_MBS]

    before = p.read_bytes()
    ok, msg = repair_file(p, props["repair_issues"])

    assert ok is False
    assert "after compression" in msg
    assert p.read_bytes() == before             # original untouched
    assert not (p.parent / "repaired").exists()  # and never backed up


def test_check_mbs_commits_when_compression_brings_it_under(tmp_path,
                                                            monkeypatch):
    """The other side of the same branch: when it does fit, commit and say so."""
    p = make_tiff(tmp_path / "big.tif", "RGB", size=(600, 600), compression=None)
    monkeypatch.setattr(prescreener, "MAX_FILE_MB", 0.5)

    accepted, props = prescreener.screen_file(p)
    assert REPAIR_CHECK_MBS in props["repair_issues"]

    ok, msg = repair_file(p, props["repair_issues"])
    assert ok is True
    assert "within" in msg and "limit" in msg
    assert (p.parent / "repaired" / p.name).exists()   # original backed up


def test_reject_from_the_prescreener_is_refused(tmp_path):
    p = tmp_path / "notmedia.xyz"
    p.write_bytes(b"not an image")
    accepted, props = prescreener.screen_file(p)
    assert props["repair_issues"] == [REPAIR_REJECT]

    ok, msg = repair_file(p, props["repair_issues"])
    assert ok is False
    assert "Reject" in msg
