"""Domain model dataclasses for the CDASH batch entities.

Each dataclass mirrors one working table. They are returned by the repositories
for single-table reads (joined/aggregate reads stay plain dicts).

To avoid a big-bang rewrite, every entity is mapping-compatible via the `Row`
mixin: in addition to attribute access (``media.filename``) the legacy dict-style
access used throughout the codebase keeps working unchanged —
``media["filename"]``, ``media.get("filename")``, ``"x" in media``,
``dict(media)``, and ``**media``.
"""

import dataclasses
from typing import Iterable, List, Optional

# ---------------------------------------------------------------------------
# Issue-list serialization
# ---------------------------------------------------------------------------
# cdash_media stores both issue lists as delimited strings. The delimiters and
# the join/split pairs live here — a leaf module both db/ and services/ can
# import — so no caller has to hand-roll the inverse of another's join.
# repair_media.parse_repair_issues remains the canonical *parser* for repair
# issues, since it also normalizes each code.

FORMAT_ISSUE_SEP = "|"
REPAIR_ISSUE_SEP = ", "


def join_format_issues(issues: Optional[Iterable[str]]) -> str:
    return FORMAT_ISSUE_SEP.join(issues or [])


def split_format_issues(value: Optional[str]) -> List[str]:
    return value.split(FORMAT_ISSUE_SEP) if value else []


def join_repair_issues(issues: Optional[Iterable[str]]) -> str:
    return REPAIR_ISSUE_SEP.join(issues or [])


class Row:
    """Mapping-compatible mixin for dataclass DB rows."""

    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __contains__(self, key) -> bool:
        return key in self._field_names()

    def keys(self):
        return self._field_names()

    def values(self):
        return [getattr(self, k) for k in self._field_names()]

    def items(self):
        return [(k, getattr(self, k)) for k in self._field_names()]

    @classmethod
    def _field_names(cls) -> tuple:
        return tuple(f.name for f in dataclasses.fields(cls))

    @classmethod
    def from_row(cls, row: Optional[dict]):
        """Build an instance from a sqlite row dict, or return None.

        Only the dataclass's own fields are taken, so a plain SELECT * dict maps
        cleanly; extra columns (from joins) are ignored by callers that still use
        dicts for those queries.
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
