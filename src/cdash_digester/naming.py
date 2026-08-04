"""
CDASH name parsing and slugification.

A leaf module (stdlib only), so db/, services/ and gui/ can all depend on it.

Naming conventions
------------------
  batch folder : CDB<YYMMDD>[a-z]?-<name>
  media folder : [F<index>-]<slug>-OF<item_set_id>
  media stem   : <place_slug>[-_]<doc_index>p<page_index>-<doc_type>[-OP<place_id>]

Each parser returns a dict of components, or None if the name is not in ready
form — the caller treats None as "not yet canonical", not as an error.
"""

import re
from typing import Optional

# Batch folder:  CDB<YYMMDD>[a-z]?-<name>
_BATCH_RE = re.compile(r"^CDB(?P<date>\d{6})(?P<letter>[a-z])?-(?P<name>.+)$")

# Media folder:  [F<index>-]<slug>-OF<item_set_id>
_FOLDER_RE = re.compile(
    r"^(?:F(?P<folder_index>\d+)-)?(?P<slug>.+)-OF(?P<item_set_id>\d+)$"
)

# Ready media stem:
#   <place_slug>_<doc_index>p<page_index>-<doc_type>[-OP<place_id>]
# The delimiter before doc_index may be "-" or "_", and the indices need no
# zero padding (0027p0001 and 27p1 parse identically).
_MEDIA_RE = re.compile(
    r"^(?P<place_slug>.+)[-_](?P<doc_index>\d+)p(?P<page_index>\d+)"
    r"-(?P<doc_type>[A-Z]{2})(?:-OP(?P<place_id>\d+))?$",
    re.IGNORECASE,
)


def slugify(text: str) -> str:
    """Replace spaces with underscores; drop all non-alphanumeric except - and _."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "", text.replace(" - ", "-").replace(" ", "_"))


def parse_batch_name(folder_name: str) -> Optional[dict]:
    """Parse a batch folder name.  Returns a dict or None on mismatch."""
    m = _BATCH_RE.match(folder_name)
    if not m:
        return None
    return {
        "batch_id": f"CDB{m.group('date')}{m.group('letter') or ''}",
        "date":     m.group("date"),
        "letter":   m.group("letter"),
        "name":     m.group("name"),
    }


def parse_folder_name(folder_name: str) -> Optional[dict]:
    """Parse a media folder name.  Returns a dict or None on mismatch."""
    m = _FOLDER_RE.match(folder_name)
    if not m:
        return None
    return {
        "folder_index": int(m.group("folder_index")) if m.group("folder_index") else None,
        "slug":         m.group("slug"),
        "item_set_id":  int(m.group("item_set_id")),
    }


def parse_media_name(stem: str) -> Optional[dict]:
    """Parse a media filename stem (no extension).

    Returns a dict with place_slug, doc_index, page_index, doc_type,
    place_id (int or None), or None if the name is not in ready format.
    """
    m = _MEDIA_RE.match(stem)
    if not m:
        return None
    return {
        "place_slug": m.group("place_slug"),
        "doc_index":  int(m.group("doc_index")),
        "page_index": int(m.group("page_index")),
        "doc_type":   m.group("doc_type").upper(),
        "place_id":   int(m.group("place_id")) if m.group("place_id") else None,
    }
