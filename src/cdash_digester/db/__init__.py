"""Persistence layer for a CDASH batch.

`Database` owns the SQLite connection, schema, migrations, and the bool row
factory. The repository classes hold the SQL for each aggregate. `BatchDB`
(in cdash_objects) is a thin facade over these, kept for API stability.
"""

from .database import Database
from .repositories import (
    BatchRepo, FolderRepo, PlaceRepo, DocRepo, MediaRepo, CacheRepo,
    ExportRepo,
)

__all__ = [
    "Database", "BatchRepo", "FolderRepo", "PlaceRepo",
    "DocRepo", "MediaRepo", "CacheRepo", "ExportRepo",
]
