"""ExifTool is read via a one-shot subprocess with stderr sent to DEVNULL.

The python-exiftool -stay_open child left stderr as an un-drained pipe, which
filled with warnings and deadlocked the windowed frozen build partway through a
folder. These tests pin the handle wiring and the failure contract of the shared
read_tags helper.
"""

import subprocess
from pathlib import Path

from cdash_digester import exiftool_util


def test_read_tags_drains_stderr_and_parses_json(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class _Proc:
            stdout = b'[{"EXIF:Orientation": 6}]'
        return _Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)

    tags = exiftool_util.read_tags(Path("photo.tif"))

    assert tags["EXIF:Orientation"] == 6
    # stderr to DEVNULL is the actual fix; stdin too so it never blocks reading.
    assert captured["kwargs"]["stderr"] is subprocess.DEVNULL
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is subprocess.PIPE
    # A timeout guarantees the scan can never hang forever on a bad file.
    assert captured["kwargs"]["timeout"] > 0
    # Numeric mode passes -n by default.
    assert "-n" in captured["cmd"]


def test_read_tags_non_numeric_omits_n_flag(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class _Proc:
            stdout = b"[]"
        return _Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)

    exiftool_util.read_tags(Path("photo.tif"), numeric=False)
    assert "-n" not in captured["cmd"]


def test_read_tags_returns_empty_on_failure(monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("exiftool not on PATH")

    monkeypatch.setattr(subprocess, "run", boom)

    # Missing/failed ExifTool must degrade to {}, not raise.
    assert exiftool_util.read_tags(Path("photo.tif")) == {}
