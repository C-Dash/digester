"""CatalogExportService — write the catalog CSV files.

Extracted from Digester.export_csv and the _write_*_csv family. SQL and column
layouts preserved verbatim.
"""

import csv
import shutil
from pathlib import Path

# Bundled folder of Omeka CSV-mapping JSON files, copied into each batch's
# catalog on export. Mirrors the gui/assets resource pattern; export.py lives
# one level deeper (services/), so go up two parents to the package root.
_CSV_MAPPINGS_DIR = Path(__file__).resolve().parent.parent / "csv_mappings"


class CatalogExportService:
    def __init__(self, session):
        self._session = session

    def export_csv(self):
        """Export batch.csv, folder.csv, place.csv, document.csv, media.csv."""
        session = self._session
        if not session.db:
            session.log("No open batch.", "error")
            return
        batch = session.db.get_batch()
        if not batch or not batch.ready:
            session.log("Batch is not Ready — CSV export skipped.", "warning")
            return
        counts = session.collect_and_store_counts()
        batch = session.db.get_batch()
        self._write_batch_csv(batch)
        self._write_folder_csv()
        self._write_place_csv()
        self._write_document_csv()
        self._write_media_csv()
        self._copy_csv_mappings()
        session.log(
            "CSV files written to catalog/.  " + session.counts_summary(counts),
            "info",
        )

    def _copy_csv_mappings(self):
        """Copy the bundled csv_mappings/ tree into catalog/csv_mappings/,
        skipping files that already exist (preserves per-batch edits)."""
        session = self._session
        if not _CSV_MAPPINGS_DIR.is_dir():
            session.log("csv_mappings source folder not found — skipped.", "warning")
            return
        dest_root = session.catalog_path / "csv_mappings"
        copied = 0
        for src_file in _CSV_MAPPINGS_DIR.rglob("*"):
            if src_file.is_dir():
                continue
            target = dest_root / src_file.relative_to(_CSV_MAPPINGS_DIR)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, target)
            copied += 1
        if copied:
            session.log(f"Copied {copied} CSV-mapping file(s) to "
                    f"catalog/csv_mappings/.", "info")

    def _write_batch_csv(self, batch: dict):
        out = self._session.catalog_path / "batch.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "id", "batch_id", "mnemonic_name", "batch_folder_path",
                "initialized_date", "status", "note",
                "folders", "places", "documents", "media", "rejects", "repaired",
            ])
            w.writeheader()
            w.writerow({
                "id":                batch.id,
                "batch_id":          batch.batch_id,
                "mnemonic_name":     batch.name,
                "batch_folder_path": batch.batch_folder_path,
                "initialized_date":  batch.initialized_date,
                "status":            "go" if batch.ready else "no-go",
                "note":              batch.note or "",
                "folders":           batch.folders_count,
                "places":            batch.places_count,
                "documents":         batch.documents_count,
                "media":             batch.media_count,
                "rejects":           batch.rejected_count,
                "repaired":          batch.repaired_count,
            })

    def _write_folder_csv(self):
        out = self._session.catalog_path / "folder.csv"
        rows = self._session.db.folders_for_export()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "ResourceTemplate", "ResourceClass", "folder", "itemSetID",
            ])
            w.writeheader()
            for r in rows:
                w.writerow({
                    "ResourceTemplate": "CDASH Folder",
                    "ResourceClass":    "bibo:Collection",
                    "folder":           r["cdash_folder_name"],
                    "itemSetID":        r["item_set_id"],
                })

    def _write_place_csv(self):
        out = self._session.catalog_path / "place.csv"
        rows = self._session.db.places_for_export()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "resourceTemplate", "resourceClass", "identifier",
                "placeItem", "placeName", "PlaceItemID", "placeType",
                "lat", "lon", "houseNum", "streetName", "streetSort",
                "Neighborhood", "chcDist", "Folder", "ItemSetID",
            ])
            w.writeheader()
            for p in rows:
                w.writerow({
                    "resourceTemplate": "CDASH Place",
                    "resourceClass":    "dcterms:Location",
                    "identifier":       p["place_item_id"],
                    "placeItem":        p["place_name"],
                    "placeName":        p["place_name"],
                    "PlaceItemID":      p["place_item_id"],
                    "placeType":        p["place_type"],
                    "lat":              p["lat"],
                    "lon":              p["lon"],
                    "houseNum":         p["house_num"],
                    "streetName":       p["street_name"],
                    "streetSort":       p["street_sort"],
                    "Neighborhood":     p["neighborhood"],
                    "chcDist":          p["chc_dist"],
                    "Folder":           p["cdash_folder_name"] or "",
                    "ItemSetID":        p["folder_item_set_id"] or "",
                })

    def _write_document_csv(self):
        out = self._session.catalog_path / "document.csv"
        rows = self._session.db.docs_for_export()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "resourceTemplate", "resourceClass", "identifier", "title",
                "Type", "bibliographicCitation", "rights",
                "placeitem", "placeItemID", "Folder", "ItemSetID",
                "numPages", "streetsort", "Neighborhood", "chcDist",
                "dateAccepted", "lat", "lon",
            ])
            w.writeheader()
            for d in rows:
                w.writerow({
                    "resourceTemplate":    "CDASH Document",
                    "resourceClass":       "bibo:Document",
                    "identifier":          d["batch_doc_id"],
                    "title":               d["doc_title"],
                    "Type":                d["doc_type_description"],
                    "bibliographicCitation": (
                        "Cambridge Historical Commission, "
                        "Digital Architectural Survey and History."
                    ),
                    "rights":              "Rights status not evaluated.",
                    "placeitem":           d["place_item_id"],
                    "placeItemID":         d["place_item_id"],
                    "Folder":              d["cdash_folder_name"],
                    "ItemSetID":           d["folder_item_set_id"],
                    "numPages":            d["num_pages"],
                    "streetsort":          d["street_sort"],
                    "Neighborhood":        d["neighborhood"],
                    "chcDist":             d["chc_dist"],
                    "dateAccepted":        d["date_accepted"],
                    "lat":                 d["place_lat"],
                    "lon":                 d["place_lon"],
                })

    def _write_media_csv(self):
        out = self._session.catalog_path / "media.csv"
        rows = self._session.db.media_for_export()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "ResourceTemplate", "Title", "identifier",
                "Relation", "type", "Source", "number", "dateAccepted",
                "format_issues", "doc_pages",
            ])
            w.writeheader()
            for m in rows:
                w.writerow({
                    "ResourceTemplate": "CDASH Media",
                    "Title":            m["filename"],
                    "identifier":       m["batch_media_id"] or "",
                    "Relation":         m["batch_doc_id"] or "",
                    "type":             m["doc_type_code"] or "",
                    "Source":           self._session.batch_path.name + "/" + m["filepath"].replace("\\", "/"),
                    "number":           m["page_num"],
                    "dateAccepted":     m["capture_date"],
                    "format_issues":    m["format_issues"] or "",
                    "doc_pages":        m["doc_pages"] or "",
                })
