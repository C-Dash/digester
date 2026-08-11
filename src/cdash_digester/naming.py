"""
CDASH name parsing and slugification.

A leaf module (stdlib only), so db/, services/ and gui/ can all depend on it.

Naming conventions
------------------
  batch folder : CDB<YYMMDD>[a-z]?-<name>
  media folder : [F<index>-]<slug>-OF<item_set_id>
  media stem   : <place_slug>[-_]<doc_index>p<page_index>[-<doc_type>][-OP<place_id>]
  capture stem : <place_slug>-<capture_seq>[-<doc_type>][-OP<place_id>]

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
#   <place_slug>_<doc_index>p<page_index>[-<doc_type>][-OP<place_id>]
# The delimiter before doc_index may be "-" or "_", and the indices need no
# zero padding (0027p0001 and 27p1 parse identically).
#
# doc_type is optional so the scanner can re-read its own intermediate output.
# A capture-name run whose doc type is not yet known is renamed to the indexed
# form without a -<DT> segment; without this the next scan would fail to parse
# that name and mint a *second* doc index for a file already carrying one.
# "-OP12345" cannot be mistaken for a doc type: [A-Z]{2} would take "OP", but
# the trailing digits then match neither "$" nor "-OP<digits>", so the match
# backtracks to doc_type=None. See test_parsers.py.
_MEDIA_RE = re.compile(
    r"^(?P<place_slug>.+)[-_](?P<doc_index>\d+)p(?P<page_index>\d+)"
    r"(?:-(?P<doc_type>[A-Z]{2}))?(?:-OP(?P<place_id>\d+))?$",
    re.IGNORECASE,
)

# Field capture stem (pre-canonical):
#   <place_slug>-<capture_seq>[-<doc_type>][-OP<place_id>]
# What a batch looks like as it arrives, before the app has assigned document
# indices. capture_seq is the camera/scanner's own counter — it orders files
# within a run and is otherwise discarded; page numbers always restart at 1.
# The numeric token must not be a <doc_index>p<page_index> pair, so a canonical
# stem can never be read as a capture stem (parse_media_name is tried first in
# any case).
_CAPTURE_RE = re.compile(
    r"^(?P<place_slug>.+)-(?P<capture_seq>\d+)"
    r"(?:-(?P<doc_type>[A-Z]{2}))?(?:-OP(?P<place_id>\d+))?$",
    re.IGNORECASE,
)

# The delimiter written between place slug and doc index when the app assigns
# a name. _MEDIA_RE above still accepts "_" so legacy names keep parsing; this
# is only the form we now produce. When "_" is finally dropped on input, the
# change is to that character class and this comment — not a hunt for literals
# scattered across the scan and assignment paths.
DOC_INDEX_DELIM = "-"


def natural_key(text: str) -> tuple:
    """Sort key that compares digit runs numerically ("x-9" before "x-10").

    Capture runs are grouped in file order and the resulting rename is not
    reversible, so ordering a folder by plain string sort — under which
    Mass_Ave-10 precedes Mass_Ave-9 — would page a document wrongly. Canonical
    names are zero-padded and sort identically either way, so this is safe to
    apply to a whole folder listing.
    """
    return tuple(
        (1, int(part), "") if part.isdigit() else (0, 0, part.lower())
        for part in re.split(r"(\d+)", text) if part
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
    place_id (int or None), or None if the name is not in indexed format.
    doc_type is None for the intermediate form that has an index but no
    document type yet.
    """
    m = _MEDIA_RE.match(stem)
    if not m:
        return None
    doc_type = m.group("doc_type")
    return {
        "place_slug": m.group("place_slug"),
        "doc_index":  int(m.group("doc_index")),
        "page_index": int(m.group("page_index")),
        "doc_type":   doc_type.upper() if doc_type else None,
        "place_id":   int(m.group("place_id")) if m.group("place_id") else None,
    }


def parse_capture_name(stem: str) -> Optional[dict]:
    """Parse a pre-canonical field capture stem (no extension).

    Returns a dict with place_slug, capture_seq, doc_type (or None) and
    place_id (or None), or None if the stem is not in capture form. Callers
    must try parse_media_name first: a stem that is already indexed is not a
    capture name.
    """
    m = _CAPTURE_RE.match(stem)
    if not m:
        return None
    doc_type = m.group("doc_type")
    return {
        "place_slug":  m.group("place_slug"),
        "capture_seq": int(m.group("capture_seq")),
        "doc_type":    doc_type.upper() if doc_type else None,
        "place_id":    int(m.group("place_id")) if m.group("place_id") else None,
    }
