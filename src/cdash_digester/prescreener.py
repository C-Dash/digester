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

from .cdash_objects import BatchDB
from .exiftool_util import read_tags

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
    ".tif":  {"RGB", "L", "1"},
    ".tiff": {"RGB", "L", "1"},
}

_REJECTS_FIELDS = [
    "filename", "filepath", "file_size_mb", "pixel_width", "pixel_height",
    "format", "capture_date", "date_source", "status", "qa_note",
]


# ---------------------------------------------------------------------------
# Low-level file screening
# ---------------------------------------------------------------------------

def _check_pdf_a1b(filepath: Path) -> Tuple[bool, str, str]:
    """Return (ok, message, flavor) for PDF/A conformance via XMP marker.

    Checks for the pdfaid namespace URI and a conformance value (A/B/U/E/F)
    in either element or attribute form, regardless of the namespace prefix
    used by the file.  Accepts PDF/A-1, -2, -3, and -4 at any level.

    flavor is "PDF/A" when the conformance marker is present, "PDF" otherwise.
    """
    try:
        doc = fitz.open(str(filepath))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        fitz.TOOLS.mupdf_warnings(reset=True)
        has_pdfa_ns = "aiim.org/pdfa/ns/id/" in xmp
        has_conformance = any(
            marker in xmp for marker in (
                ":conformance>B<", ":conformance>b<",
                ":conformance>A<", ":conformance>a<",
                ":conformance>U<", ":conformance>u<",
                ":conformance>E<", ":conformance>F<",
                'conformance="B"', 'conformance="b"',
                'conformance="A"', 'conformance="a"',
                'conformance="U"', 'conformance="u"',
                'conformance="E"', 'conformance="F"',
            )
        )
        if has_pdfa_ns and has_conformance:
            return True, "PDF/A conformance marker found", "PDF/A"
        return False, "Reject:PDF/A conformance marker not found in XMP metadata", "PDF"
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
        "pdf_pages":     None,
    }

    suffix = filepath.suffix.lower()

    if suffix not in _ACCEPTED_SUFFIXES:
        props["qa_note"] = f"Unsupported file type: {suffix}"
        return False, props

    # For TIFFs: check compression before the size check so wrong_compression is
    # recorded in repair_issues even when the file is also oversized.  PIL.open
    # is lazy here — only the header is read, no pixel decode.
    img = None
    if suffix in (".tif", ".tiff"):
        try:
            img = Image.open(filepath)
            tag_v2 = getattr(img, "tag_v2", {})
            if tag_v2.get(259) != _TIFF_LZW:
                props["repair_issues"].append("wrong_compression")
        except Exception:
            img = None  # open failure is surfaced in the main image path below

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
        if not ok: 
            props["repair_issues"].append("not_pdfa")
            ok = False
        # Extract creation date and page count from PDF metadata.
        try:
            doc = fitz.open(str(filepath))
            raw = (doc.metadata or {}).get("creationDate", "") or ""
            props["pdf_pages"] = doc.page_count
            doc.close()
            fitz.TOOLS.mupdf_warnings(reset=True)
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
    # Open lazily — mode/size/n_frames/tag_v2 are readable without decoding
    # pixels. Full decode (img.load) is deferred to after all fast checks so
    # files that fail early never pay the decode cost.
    # For TIFFs img may already be open from the early compression check above.
    try:
        if img is None:
            img = Image.open(filepath)
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
    et_tags = read_tags(filepath)
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
            props["qa_note"] = "REJECT: multiframe-tiff"
            return False, props

        tag_v2 = getattr(img, "tag_v2", {})
        compression = tag_v2.get(259)   # TIFF tag 259 = Compression

        # 32-bit float is non-repairable — return immediately.
        if img.mode == "F":
            props["qa_note"] = "32-bit float TIFFs are not accepted"
            return False, props

        # Repairable checks — accumulate qa_parts; wrong_compression was already
        # added to repair_issues before the size check.
        qa_parts = []

        if compression != _TIFF_LZW:
            qa_parts.append(
                f"TIFF must use LZW compression (code {compression} found)"
            )

        if img.mode not in ("RGB", "L", "1"):
            qa_parts.append(
                f"TIFF must be 24-bit RGB, 8-bit grayscale, or 1-bit bilevel; got {img.mode}"
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

    # All checks passed — force full decode now to catch corrupt files.
    try:
        img.load()
    except Exception as exc:
        props["qa_note"] = f"Cannot decode image: {exc}"
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
                et_tags = read_tags(filepath)
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
            fitz.TOOLS.mupdf_warnings(reset=True)
        except Exception as exc:
            print(f"\nCannot open PDF: {exc}")

    print()


if __name__ == "__main__":
    main()
