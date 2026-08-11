"""ScreeningService — prescreener results with the persistent file cache.

Wraps Digester._screen_file_cached. prescreener.screen_file stays cache-unaware;
all cache logic lives here.
"""

from pathlib import Path
from typing import Tuple

from ..models import split_format_issues, split_repair_issues
from ..prescreener import screen_file


class ScreeningService:
    def __init__(self, session):
        self._session = session

    def screen(self, filepath: Path) -> Tuple[bool, dict]:
        """Return (accepted, props) for filepath, using cdash_file_cache when
        the file's size and mtime are unchanged since it was last screened.

        The cache is keyed by the file's path relative to the batch root. On a
        miss (new file, changed size/mtime, or a failed stat) the prescreener is
        invoked and the result cached.
        """
        session = self._session
        rel_path = str(filepath.relative_to(session.batch_path))
        try:
            st = filepath.stat()
            size_bytes, mtime_ns = st.st_size, st.st_mtime_ns
        except OSError:
            # Let the prescreener surface the error; do not cache a bad stat.
            return screen_file(filepath)

        cached = session.db.get_file_cache(rel_path)
        if (cached and cached["file_size_bytes"] == size_bytes
                and cached["mtime_ns"] == mtime_ns):
            props = {
                "file_size_mb":  cached["file_size_mb"],
                "pixel_width":   cached["pixel_width"],
                "pixel_height":  cached["pixel_height"],
                "format":        cached["format"],
                "capture_date":  cached["capture_date"],
                "date_source":   cached["date_source"],
                "format_issues": split_format_issues(
                    cached["format_issues"]),
                # Split, not parse: a cache hit must reproduce exactly what the
                # prescreener returned on the miss that filled it, or a code
                # changes appearance from one scan to the next.
                "repair_issues": split_repair_issues(cached["repair_issues"]),
                "pdf_pages":     cached["pdf_pages"],
            }
            return cached["accepted"], props

        accepted, props = screen_file(filepath)
        session.db.upsert_file_cache(rel_path, size_bytes, mtime_ns, accepted, props)
        return accepted, props
