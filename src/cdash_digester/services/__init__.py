"""Service layer for the Digester.

Each service owns one responsibility extracted from the former monolithic
Digester. They share batch state (db, log, paths, validator) through a reference
to the Digester, which acts as the session context — notably so the GUI worker's
runtime reassignment of `digester.log` reaches every service.
"""

from .validation import ValidationService
from .screening import ScreeningService
from .scanning import ScanService
from .assignment import AssignmentService
from .repair import RepairService
from .reject import RejectService
from .export import CatalogExportService

__all__ = [
    "ValidationService", "ScreeningService", "ScanService",
    "AssignmentService", "RepairService", "RejectService", "CatalogExportService",
]
