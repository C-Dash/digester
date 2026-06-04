"""
CDASH Media Prescreener Module

Checks each media file against acceptance criteria. Returns True (accepted)
or False (rejected). Rejects are moved to Rejects/<FolderName>/ and logged
to rejects.csv.

Accepted formats
----------------
- JPEG  : 24-bit RGB
- TIFF  : 24-bit RGB or 8-bit grayscale, LZW compression
- PDF   : PDF/A-1b (detected via XMP metadata marker)

Rejection criteria
------------------
- File size > 100 MB
- Width × Height > 108 megapixels
- Wrong colour mode
- Unreadable / corrupt file
- TIFF with non-LZW compression
- 16-bit TIFF
- PDF without PDF/A-1b XMP marker
"""

import argparse
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Tuple

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import TAGS
import fitz  # pymupdf
import exiftool

from .cdash_objects import BatchDB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_FILE_MB = 100.0
_MAX_MEGAPIXELS = 108_000_000   # 108 MP
_TIFF_LZW = 5                   # TIFF compression tag value for LZW

_ACCEPTED_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff", ".pdf"}

# PIL modes that need no repair for each image type.
# Any mode outside this set is added to props["repair_issues"].
_CLEAN_MODES = {
    ".jpg":  {"RGB"},
    ".jpeg": {"RGB"},
    ".tif":  {"RGB", "L"},
    ".tiff": {"RGB", "L"},
}

_REJECTS_FIELDS = [
    "filename", "filepath", "file_size_mb", "pixel_width", "pixel_height",
    "format", "capture_date", "date_source", "status", "qa_note",
]


# ---------------------------------------------------------------------------
# Low-level file screening
# ---------------------------------------------------------------------------

def _get_exiftool_tags(filepath: Path) -> dict:
    """Return ExifTool metadata for filepath with numeric tag values (-n).
    Returns empty dict if ExifTool is unavailable or fails."""
    try:
        with exiftool.ExifToolHelper() as et:
            results = et.get_metadata(str(filepath), params=["-n"])
        return results[0] if results else {}
    except Exception:
        return {}


def _check_pdf_a1b(filepath: Path) -> Tuple[bool, str, str]:
    """Return (ok, message, flavor) for PDF/A-1b conformance via XMP marker.

    flavor is "PDF/A-1b" when the conformance marker is present, "PDF" otherwise.
    """
    try:
        doc = fitz.open(str(filepath))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        if "pdfaid:conformance" in xmp and (
            "<pdfaid:conformance>B</pdfaid:conformance>" in xmp
            or "<pdfaid:conformance>b</pdfaid:conformance>" in xmp
        ):
            return True, "PDF/A-1b confirmed", "PDF/A-1b"
        # pbc flipped the following to true because we want the note, but not
        # to reject.
        return True, "PDF/A-1b conformance marker not found in XMP metadata", "PDF"
    except Exception as exc:
        return False, f"PDF error: {exc}", "PDF"


def screen_file(filepath: Path) -> Tuple[bool, dict]:
    """Screen a single media file.

    Returns
    -------
    (status, props)
        status : True (accepted) | False (rejected)
        props  : dict with keys file_size_mb, pixel_width, pixel_height,
                 format, capture_date, qa_note
    """
    props = {
        "file_size_mb":  None,
        "pixel_width":   None,
        "pixel_height":  None,
        "format":        None,
        "capture_date":  None,
        "date_source":   None,
        "qa_note":       "",
        "repair_issues": [],
    }

    suffix = filepath.suffix.lower()

    if suffix not in _ACCEPTED_SUFFIXES:
        props["qa_note"] = f"Unsupported file type: {suffix}"
        return False, props

    # File-size check
    try:
        mb = filepath.stat().st_size / (1024 * 1024)
        props["file_size_mb"] = mb
        if mb > _MAX_FILE_MB:
            props["qa_note"] = f"File too large: {mb:.1f} MB (limit {_MAX_FILE_MB} MB)"
            return False, props
    except OSError as exc:
        props["qa_note"] = f"Cannot read file: {exc}"
        return False, props

    # PDF path
    if suffix == ".pdf":
        ok, msg, flavor = _check_pdf_a1b(filepath)
        props["qa_note"] = msg
        props["format"]  = flavor
        # Extract creation date from PDF metadata: "D:YYYYMMDDHHmmSS..."
        try:
            doc = fitz.open(str(filepath))
            raw = (doc.metadata or {}).get("creationDate", "") or ""
            doc.close()
            if raw.startswith("D:") and len(raw) >= 10:
                digits = raw[2:10]   # YYYYMMDD
                props["capture_date"] = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
                props["date_source"]  = "pdf"
        except Exception:
            pass
        if props["capture_date"] is None:
            props["capture_date"] = datetime.fromtimestamp(
                filepath.stat().st_ctime
            ).strftime("%Y-%m-%d")
            props["date_source"] = "filesystem"
        return ok, props

    # Image path (JPEG / TIFF)
    try:
        img = Image.open(filepath)
        img.load()
    except (UnidentifiedImageError, Exception) as exc:
        props["qa_note"] = f"Cannot open image: {exc}"
        return False, props

    props["format"] = img.mode
    if img.mode not in _CLEAN_MODES.get(suffix, set()):
        props["repair_issues"].append(img.mode.lower())
    w, h = img.size
    props["pixel_width"]  = w
    props["pixel_height"] = h

    # Megapixel check
    if w * h > _MAX_MEGAPIXELS:
        props["qa_note"] = (
            f"Exceeds 108 MP: {w * h / 1_000_000:.1f} MP ({w}×{h})"
        )
        return False, props

    # EXIF capture date via ExifTool (called once; et_tags reused below)
    et_tags = _get_exiftool_tags(filepath)
    raw = et_tags.get("EXIF:DateTimeOriginal") or et_tags.get("EXIF:ModifyDate")
    if raw:
        props["capture_date"] = str(raw)[:10].replace(":", "-")
        props["date_source"]  = "exif"
    if props["capture_date"] is None:
        props["capture_date"] = datetime.fromtimestamp(
            filepath.stat().st_ctime
        ).strftime("%Y-%m-%d")
        props["date_source"] = "filesystem"

    # Format-specific checks
    if suffix in (".jpg", ".jpeg"):
        if img.mode != "RGB":
            props["qa_note"] = f"JPEG must be 24-bit RGB; got {img.mode}"
            return False, props

    elif suffix in (".tif", ".tiff"):
        if getattr(img, 'n_frames', 1) > 1:
            props["repair_issues"].insert(0, "multiframe_tiff")
            props["qa_note"] = "multiframe-tiff"
            return False, props

        # Non-repairable structural checks — return immediately.
        tag_v2 = getattr(img, "tag_v2", {})
        compression = tag_v2.get(259)   # TIFF tag 259 = Compression
        if compression != _TIFF_LZW:
            props["qa_note"] = (
                f"TIFF must use LZW compression "
                f"(compression code {compression} found)"
            )
            return False, props

        if img.mode in ("I;16", "F"):
            props["qa_note"] = "16-bit TIFFs are not accepted"
            return False, props

        # Repairable checks — accumulate all issues before returning.
        qa_parts = []

        if img.mode not in ("RGB", "L"):
            qa_parts.append(
                f"TIFF must be 24-bit RGB or 8-bit grayscale; got {img.mode}"
            )

        # iPhone portrait: non-normal EXIF Orientation or portrait pixel dimensions.
        host = et_tags.get("EXIF:HostComputer", "") or ""
        orientation = et_tags.get("EXIF:Orientation")  # int with -n; 1 = normal
        if "iphone" in host.lower() and (orientation not in (None, 1) or h > w):
            props["repair_issues"].append("iphone_vert")
            qa_parts.append("iphone-vert")

        if props["repair_issues"]:
            props["qa_note"] = "; ".join(qa_parts) if qa_parts else ", ".join(props["repair_issues"])
            return False, props

    props["qa_note"] = "OK"
    return True, props


# ---------------------------------------------------------------------------
# MediaPrescreener
# ---------------------------------------------------------------------------

class MediaPrescreener:
    """Screens all media in a batch and moves rejects."""

    def __init__(self, db: BatchDB, batch_root: Path,
                 log: Callable[[str, str], None] = None):
        self.db = db
        self.batch_root = batch_root
        self.rejects_root = batch_root / "Rejects"
        self.log = log or (lambda msg, level: None)
        self._reject_rows: list = []

    def screen_folder(self, item_set_id: int, folder_path: Path):
        """Screen every registered media file in one Item Set folder."""
        self.rejects_root.mkdir(exist_ok=True)
        reject_folder = self.rejects_root / folder_path.name

        for row in self.db.get_media_for_folder(item_set_id):
            filepath = self.batch_root / row["filepath"]

            if not filepath.exists():
                self.db.set_media_status(row["media_id"], False,
                                         "File not found on disk")
                self.log(f"  NOT FOUND: {row['filename']}", "error")
                continue

            status, props = screen_file(filepath)

            self.db.update_media_prescreener_props(
                row["media_id"],
                props["file_size_mb"],
                props["pixel_width"],
                props["pixel_height"],
                props["format"],
                props["capture_date"],
                props["date_source"],
                ", ".join(props.get("repair_issues", [])),
            )
            self.db.set_media_status(row["media_id"], status, props["qa_note"])

            if not status:
                reject_folder.mkdir(parents=True, exist_ok=True)
                dest = reject_folder / row["filename"]
                shutil.copy2(str(filepath), str(dest))
                self.db.insert_reject(
                    item_set_id=item_set_id,
                    filename=row["filename"],
                    filepath=row["filepath"],
                    file_size_mb=props["file_size_mb"],
                    pixel_width=props["pixel_width"],
                    pixel_height=props["pixel_height"],
                    format_note=props["format"],
                    capture_date=props["capture_date"],
                    date_source=props["date_source"],
                    qa_note=props["qa_note"],
                )
                self._reject_rows.append({
                    "filename":     row["filename"],
                    "filepath":     row["filepath"],
                    "file_size_mb": props["file_size_mb"],
                    "pixel_width":  props["pixel_width"],
                    "pixel_height": props["pixel_height"],
                    "format":       props["format"],
                    "capture_date": props["capture_date"],
                    "date_source":  props["date_source"],
                    "status":       False,
                    "qa_note":      props["qa_note"],
                })
                self.log(
                    f"  REJECTED {row['filename']}: {props['qa_note']}",
                    "warning",
                )
            else:
                self.log(f"  OK: {row['filename']}", "info")

        self.db.recalculate_folder_status(item_set_id)

    def write_rejects_csv(self):
        """Write Rejects/rejects.csv and update rejected_count on the batch."""
        self.db.set_rejected_count(len(self._reject_rows))
        if not self._reject_rows:
            return
        out = self.rejects_root / "rejects.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_REJECTS_FIELDS)
            writer.writeheader()
            writer.writerows(self._reject_rows)


# ---------------------------------------------------------------------------
# CLI entry point
# To run:
# python -m cdash_digester.prescreener "CDB260430-Test_batch\media\F6-Mass_Ave_Quincy_Central_Sqs_Views_Both_Sides-OF43111\Massachusetts_Ave-Mass_Ave_Quincy_Square_to_Central_Square_0027p0001-VE-OP43296.tif"
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prescreen a media file and report its properties, EXIF data, and PIL format attributes."
    )
    parser.add_argument("filepath", help="Path to the media file to inspect")
    args = parser.parse_args()

    filepath = Path(args.filepath)
    if not filepath.exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"File : {filepath.name}")
    print(f"Path : {filepath}")
    print(sep)

    # --- Prescreener result ---
    status, props = screen_file(filepath)
    print(f"\n--- Prescreener Result ---")
    print(f"  {'status':<16}: {'ACCEPTED' if status else 'REJECTED'}")
    for key, val in props.items():
        print(f"  {key:<16}: {val}")

    suffix = filepath.suffix.lower()

    # --- PIL attributes (images only) ---
    if suffix in (".jpg", ".jpeg", ".tif", ".tiff"):
        try:
            img = Image.open(filepath)
            img.load()

            print(f"\n--- PIL Format Attributes ---")
            print(f"  {'format':<16}: {img.format}")
            print(f"  {'mode':<16}: {img.mode}")
            print(f"  {'size':<16}: {img.size[0]} x {img.size[1]}")

            if img.info:
                print(f"\n  img.info:")
                for k, v in img.info.items():
                    print(f"    {str(k):<20}: {v!r}")

            tag_v2 = getattr(img, "tag_v2", None)
            if tag_v2:
                print(f"\n  TIFF tag_v2:")
                for tag_id, val in sorted(tag_v2.items()):
                    name = TAGS.get(tag_id, f"tag_{tag_id}")
                    print(f"    [{tag_id:5d}] {name:<30}: {val!r}")

            # --- ExifTool tags ---
            print(f"\n--- ExifTool Tags ---")
            try:
                et_tags = _get_exiftool_tags(filepath)
                if et_tags:
                    max_key = max((len(k) for k in et_tags if k != "SourceFile"), default=30)
                    for key, val in sorted(et_tags.items()):
                        if key != "SourceFile":
                            print(f"  {key:<{max_key}}  :  {val!r}")
                else:
                    print("  (no data — is ExifTool on PATH?)")
            except Exception as exc:
                print(f"  ExifTool error: {exc}")

        except Exception as exc:
            print(f"\nCannot open image with PIL: {exc}")

    # --- PDF attributes ---
    elif suffix == ".pdf":
        try:
            doc = fitz.open(str(filepath))
            print(f"\n--- PDF Attributes ---")
            print(f"  {'page_count':<16}: {doc.page_count}")
            meta = doc.metadata or {}
            if meta:
                print(f"\n  metadata:")
                for k, v in meta.items():
                    print(f"    {k:<20}: {v!r}")
            xmp = doc.get_xml_metadata() or ""
            if xmp:
                print(f"\n  XMP (first 800 chars):")
                for line in xmp[:800].splitlines():
                    print(f"    {line}")
            doc.close()
        except Exception as exc:
            print(f"\nCannot open PDF: {exc}")

    print()


if __name__ == "__main__":
    main()
