"""Domain model dataclasses for the CDASH batch entities.

Each dataclass mirrors one working table. Repositories return them for every
*entity* read — including the media-joined-to-doc read, which has its own type
(MediaWithDoc) so that get_media() and get_media_for_folder() are substitutable.
Aggregate/COUNT reads, cache-table reads and the export joins stay plain dicts:
they are projections, not entities.

Entities are frozen dataclasses read by attribute (``media.filename``). They
briefly carried a mapping shim (``media["filename"]``, ``.get()``, ``keys()``,
``dict(media)``) so callers could migrate off dict rows incrementally. Every
caller — services, digester and the whole GUI — now uses attribute access, so
the shim is gone and `Row` is only a construction helper.

Callers are therefore coupled to the *type* rather than to column-name strings:
a renamed column is a load-time error in one place instead of a KeyError at
paint time.
"""

import dataclasses
from typing import Iterable, List, Optional

# ---------------------------------------------------------------------------
# Issue-list serialization
# ---------------------------------------------------------------------------
# cdash_media stores both issue lists as delimited strings. The delimiters and
# the join/split pairs live here — a leaf module both db/ and services/ can
# import — so no caller has to hand-roll the inverse of another's join.
#
# Both lists get a join *and* a split. repair_issues had only a join, so the
# one caller that needed to read a stored value back reached for
# repair_media.parse_repair_issues instead — which lower-cases every code
# because it exists to make matching case-insensitive. The result was a code
# that displayed as "Reject" on the scan that produced it and "reject" on the
# next scan, once the value came back from the file cache. Restoring a stored
# value and normalizing one for comparison are different jobs; keep them apart.

FORMAT_ISSUE_SEP = "|"
REPAIR_ISSUE_SEP = ", "


def join_format_issues(issues: Optional[Iterable[str]]) -> str:
    return FORMAT_ISSUE_SEP.join(issues or [])


def split_format_issues(value: Optional[str]) -> List[str]:
    return value.split(FORMAT_ISSUE_SEP) if value else []


def join_repair_issues(issues: Optional[Iterable[str]]) -> str:
    return REPAIR_ISSUE_SEP.join(issues or [])


def split_repair_issues(value: Optional[str]) -> List[str]:
    """Exact inverse of join_repair_issues: codes as stored, case preserved.

    Use this to read a stored value back. Use repair_media.parse_repair_issues
    when comparing against a known code — it normalizes, so it is the right
    tool for matching and the wrong one for display.
    """
    return [part.strip() for part in value.split(REPAIR_ISSUE_SEP.strip())
            if part.strip()] if value else []


class Row:
    """Construction helper for dataclass DB rows."""

    @classmethod
    def _field_names(cls) -> tuple:
        return tuple(f.name for f in dataclasses.fields(cls))

    @classmethod
    def from_row(cls, row: Optional[dict]):
        """Build an instance from a sqlite row dict, or return None.

        Only the dataclass's own fields are taken, so a plain SELECT * dict
        maps cleanly and any extra columns a join brings along are ignored —
        pick the matching type (e.g. MediaWithDoc) when those columns matter.
        """
        if row is None:
            return None
        return cls(**{k: row.get(k) for k in cls._field_names()})


@dataclasses.dataclass(frozen=True)
class Batch(Row):
    id: Optional[int] = None
    batch_id: Optional[str] = None
    name: Optional[str] = None
    batch_folder_path: Optional[str] = None
    initialized_date: Optional[str] = None
    rejected_count: int = 0
    ready: bool = False
    note: Optional[str] = None
    folders_count: int = 0
    places_count: int = 0
    documents_count: int = 0
    media_count: int = 0
    repaired_count: int = 0


@dataclasses.dataclass(frozen=True)
class Folder(Row):
    folder_number: Optional[int] = None
    batch_folder_id: Optional[str] = None
    cdash_folder_name: Optional[str] = None
    item_set_id: Optional[int] = None
    os_folder_name: Optional[str] = None
    name_ready: bool = False
    media_ready: bool = False
    notes: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class Place(Row):
    place_item_id: Optional[int] = None
    place_name: Optional[str] = None
    place_type: Optional[str] = None
    house_num: Optional[str] = None
    street_name: Optional[str] = None
    street_sort: Optional[str] = None
    neighborhood: Optional[str] = None
    chc_dist: Optional[str] = None
    item_set_ids: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    ready: bool = False
    notes: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class Doc(Row):
    doc_item_id: Optional[int] = None
    place_item_id: Optional[int] = None
    folder_doc_sequence: Optional[int] = None
    item_set_id: Optional[int] = None
    doc_title: Optional[str] = None
    doc_type_code: Optional[str] = None
    doc_type_description: Optional[str] = None
    date_accepted: Optional[str] = None
    batch_doc_id: Optional[str] = None
    num_pages: int = 0
    ready: bool = False
    notes: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class Media(Row):
    media_id: Optional[int] = None
    doc_item_id: Optional[int] = None
    item_set_id: Optional[int] = None
    filename: Optional[str] = None
    batch_media_id: Optional[str] = None
    filepath: Optional[str] = None
    page_num: Optional[int] = None
    capture_date: Optional[str] = None
    file_size_mb: Optional[float] = None
    pixel_width: Optional[int] = None
    pixel_height: Optional[int] = None
    format: Optional[str] = None
    format_issues: Optional[str] = None
    repair_issues: Optional[str] = None
    ready: bool = False
    filename_issues: Optional[str] = None
    # Name-side readiness, separate from `ready` (which also requires the file
    # to pass format screening). True only when every token the canonical stem
    # needs — doc index, page, doc type and a *validated* place — is present.
    name_ready: bool = False


@dataclasses.dataclass(frozen=True)
class MediaWithDoc(Media):
    """A media row joined to its document.

    get_media() and get_media_for_folder() read the same entity, but the
    latter joins cdash_doc for the two columns the media table and thumbnail
    pane need. Without a type for that shape the two accessors returned
    incompatible things — a Media and a plain dict — for the same rows.
    """
    doc_type_code: Optional[str] = None
    num_pages: Optional[int] = None
