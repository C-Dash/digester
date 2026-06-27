"""Minimal ExifTool access via a one-shot subprocess.

We invoke the ``exiftool`` executable directly rather than through the
python-exiftool (-stay_open) wrapper. That wrapper kept a child process whose
stderr was an un-drained pipe; in the windowed (no-console) PyInstaller build
the small Windows pipe buffer filled with ExifTool warnings and deadlocked the
scan partway through a folder.

Each call here uses fully controlled handles: stdin and stderr go to DEVNULL,
stdout is captured and fully drained by ``subprocess.run``, CREATE_NO_WINDOW
suppresses a console flash on Windows, and a timeout guards against a
pathological file. ExifTool being absent or failing degrades to an empty dict —
callers treat missing tags as "fall back to other sources".
"""

import json
import os
import subprocess
from pathlib import Path

_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def read_tags(filepath: Path, numeric: bool = True, timeout: int = 30) -> dict:
    """Return ExifTool tags for ``filepath`` as a dict, or {} on any failure.

    numeric=True passes ``-n`` so values like EXIF:Orientation come back as
    integers rather than human-readable strings.
    """
    args = ["exiftool", "-json"]
    if numeric:
        args.append("-n")
    args.append(str(filepath))
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
            timeout=timeout,
        )
        data = json.loads(proc.stdout or b"[]")
        return data[0] if data else {}
    except Exception:
        return {}
