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
    def __init__(self, dig):
        self._dig = dig

    def export_csv(self):
        """Export batch.csv, folder.csv, place.csv, document.csv, media.csv."""
        dig = self._dig
        if not dig.db:
            dig.log("No open batch.", "error")
            return
        batch = dig.db.get_batch()
        if not batch or not batch["ready"]:
            dig.log("Batch is not Ready — CSV export skipped.", "warning")
            return
        counts = dig._collect_and_store_counts()
        batch = dig.db.get_batch()
        self._write_batch_csv(batch)
        self._write_folder_csv()
        self._write_place_csv()
        self._write_document_csv()
        self._write_media_csv()
        self._copy_csv_mappings()
        dig.log(
            "CSV files written to catalog/.  " + dig._counts_summary(counts),
            "info",
        )

    def _copy_csv_mappings(self):
        """Copy the bundled csv_mappings/ tree into catalog/csv_mappings/,
        skipping files that already exist (preserves per-batch edits)."""
        dig = self._dig
        if not _CSV_MAPPINGS_DIR.is_dir():
            dig.log("csv_mappings source folder not found — skipped.", "warning")
            return
        dest_root = dig.catalog_path / "csv_mappings"
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
            dig.log(f"Copied {copied} CSV-mapping file(s) to "
                    f"catalog/csv_mappings/.", "info")

    def _write_batch_csv(self, batch: dict):
        out = self._dig.catalog_path / "batch.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "id", "batch_id", "mnemonic_name", "batch_folder_path",
                "initialized_date", "status", "note",
                "folders", "places", "documents", "media", "rejects", "repaired",
            ])
            w.writeheader()
            w.writerow({
                "id":                batch["id"],
                "batch_id":          batch["batch_id"],
                "mnemonic_name":     batch["name"],
                "batch_folder_path": batch["batch_folder_path"],
                "initialized_date":  batch["initialized_date"],
                "status":            "go" if batch["ready"] else "no-go",
                "note":              batch.get("note", ""),
                "folders":           batch.get("folders_count", 0),
                "places":            batch.get("places_count", 0),
                "documents":         batch.get("documents_count", 0),
                "media":             batch.get("media_count", 0),
                "rejects":           batch.get("rejected_count", 0),
                "repaired":          batch.get("repaired_count", 0),
            })

    def _write_folder_csv(self):
        out = self._dig.catalog_path / "folder.csv"
        rows = self._dig.db._con.execute(
            "SELECT * FROM cdash_folder ORDER BY folder_number"
        ).fetchall()
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
        out = self._dig.catalog_path / "place.csv"
        rows = self._dig.db._con.execute(
            """SELECT p.*,
                      f.cdash_folder_name,
                      f.item_set_id AS folder_item_set_id
               FROM cdash_place p
               LEFT JOIN cdash_doc d ON d.place_item_id = p.place_item_id
               LEFT JOIN cdash_folder f ON f.item_set_id = d.item_set_id
               GROUP BY p.place_item_id"""
        ).fetchall()
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
        out = self._dig.catalog_path / "document.csv"
        rows = self._dig.db._con.execute(
            """SELECT d.*,
                      p.place_name, p.street_sort, p.neighborhood,
                      p.chc_dist, p.lat AS place_lat, p.lon AS place_lon,
                      f.cdash_folder_name,
                      f.item_set_id AS folder_item_set_id
               FROM cdash_doc d
               LEFT JOIN cdash_place  p ON d.place_item_id = p.place_item_id
               LEFT JOIN cdash_folder f ON d.item_set_id   = f.item_set_id
               ORDER BY f.folder_number, d.folder_doc_sequence"""
        ).fetchall()
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
        out = self._dig.catalog_path / "media.csv"
        rows = self._dig.db._con.execute(
            """SELECT m.*, d.doc_type_code, d.batch_doc_id, d.num_pages AS doc_pages
               FROM cdash_media m
               LEFT JOIN cdash_doc d ON m.doc_item_id = d.doc_item_id
               ORDER BY m.item_set_id, m.filename"""
        ).fetchall()
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
                    "Source":           self._dig.batch_path.name + "/" + m["filepath"].replace("\\", "/"),
                    "number":           m["page_num"],
                    "dateAccepted":     m["capture_date"],
                    "format_issues":    m["format_issues"] or "",
                    "doc_pages":        m["doc_pages"] or "",
                })
