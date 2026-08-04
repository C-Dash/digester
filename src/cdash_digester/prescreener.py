"""
CDASH Media Prescreener Module

Checks each media file against acceptance criteria. Returns True (accepted)
or False (rejected). screen_file() is stateless — the scan pipeline
(services/scanning.py) handles persisting results and moving rejected files.

Accepted formats
----------------
- JPEG  : 24-bit RGB
- TIFF  : 24-bit RGB or 8-bit grayscale, LZW compression
- PDF   : PDF/A-1b (detected via XMP metadata marker)

Rejection criteria
------------------
- File size > 100 MB (JPEG/TIFF) or > 200 MB (PDF) — unless it's an
  uncompressed TIFF, which is flagged "Check MBs" instead of rejected
- Width × Height > 108 megapixels
- Wrong colour mode
- Unreadable / corrupt file
- Multi-frame TIFF
- PDF without PDF/A-1b XMP marker
- Any other file type found in a media folder (the scanner registers every
  file, not just the three accepted suffixes) — always
  format_issues = "Format not supported", even if PIL can open it (e.g. a
  PNG); PIL is still given a chance to identify format = the PIL mode for
  display purposes

Repairable issues (see repair_media.py)
----------------------------------------
- "Compress LZW" — TIFF with non-LZW compression
- "Flatten" — TIFF in RGBA, LA, or I;16 (16-bit grayscale) mode
- "Check MBs" — oversized uncompressed TIFF, may fit once compressed

Every rejection path sets repair_issues to the exclusive "Reject" code —
including unreadable/corrupt files (unstat-able, won't open, won't decode)
and not-admissible files with no repair path (unsupported suffix,
multi-frame TIFF, oversized, over the megapixel limit, wrong JPEG mode,
32-bit float TIFF, non-PDF/A) — so nothing screened out is ever left
without an actionable Reject flag. When PIL/fitz can't identify a file at
all, format falls back to a filetype (magic-byte) guess (e.g. "ZIP", "MP4");
format="Unreadable" only when that guess also fails.
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Tuple

import filetype
from PIL import Image
from PIL.ExifTags import TAGS
import fitz  # pymupdf

from .exiftool_util import read_tags

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Acceptance limits — public: these are the screening policy, and
# repair_media re-checks MAX_FILE_MB after compressing an oversized TIFF.
MAX_FILE_MB = 100.0
MAX_PDF_MB = 200.0
MAX_MEGAPIXELS = 108_000_000   # 108 MP
_TIFF_LZW = 5                   # TIFF compression tag value for LZW

_ACCEPTED_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff", ".pdf"}

# PDF/A conformance level, in either element (<x:conformance>B</x:conformance>)
# or attribute (conformance="B") form, whatever namespace prefix the file uses.
# Levels A/B/U/E/F cover PDF/A-1 through -4.
_PDFA_CONFORMANCE_RE = re.compile(
    r'(?::conformance>\s*([ABUEF])\s*<|conformance\s*=\s*"([ABUEF])")',
    re.IGNORECASE,
)

# PIL modes that need no repair for each image type.
# Any mode outside this set is added to props["repair_issues"].
_CLEAN_MODES = {
    ".jpg":  {"RGB"},
    ".jpeg": {"RGB"},
    ".tif":  {"RGB", "L", "1"},
    ".tiff": {"RGB", "L", "1"},
}


_MODE_DESCRIPTIONS = {
    "1": "b&w 1-bit",
    "L": "grayscale 8-bit",
    "LA": "grayscale 8-bit + alpha",
    "P": "color-map 8-bit ",
    "RGB": "RGB 24-bit",
    "RGBA": "RGBA 24-bit + alpha",
    "CMYK": "CMYK 36-bit ink",
    "YCbCr": "color video 24-bit",
    "LAB": "LAB 24-bit",
    "HSV": "HSV 24-bit",
    "I": "32-bit signed integer",
    "I;16": "16-bit integer",
    "F": "32-bit floating point",
}


# ---------------------------------------------------------------------------
# Low-level file screening
# ---------------------------------------------------------------------------

def _ctime_date(filepath: Path) -> str:
    """Filesystem creation date as YYYY-MM-DD — the capture-date fallback used
    whenever no EXIF/PDF date is available."""
    return datetime.fromtimestamp(filepath.stat().st_ctime).strftime("%Y-%m-%d")


def _set_filesystem_date(props: dict, filepath: Path):
    """Fill capture_date from the filesystem, marking the source."""
    props["capture_date"] = _ctime_date(filepath)
    props["date_source"] = "filesystem"


def _filetype_fallback(filepath: Path) -> str:
    """Best-effort format guess via magic bytes when PIL/fitz can't identify
    the file. Returns the short extension (e.g. "ZIP", "MP4") or
    "Unreadable" if filetype can't identify it either."""
    try:
        kind = filetype.guess(str(filepath))
        if kind is not None:
            return kind.extension.upper()
    except Exception:
        pass
    return "Unreadable"


def _check_pdf_a1b(filepath: Path) -> Tuple[bool, str, str]:
    """Return (ok, message, flavor) for PDF/A conformance via XMP marker.

    Checks for the pdfaid namespace URI and a conformance value (A/B/U/E/F)
    in either element or attribute form, regardless of the namespace prefix
    used by the file.  Accepts PDF/A-1, -2, -3, and -4 at any level.

    flavor is "PDF/A" when the conformance marker is present, "PDF" when
    absent, "Unreadable" if the PDF itself can't be opened.
    """
    try:
        doc = fitz.open(str(filepath))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        fitz.TOOLS.mupdf_warnings(reset=True)
        has_pdfa_ns = "aiim.org/pdfa/ns/id/" in xmp
        has_conformance = bool(_PDFA_CONFORMANCE_RE.search(xmp))
        if has_pdfa_ns and has_conformance:
            return True, "PDF/A conformance marker found", "PDF/A"
        return False, "Non-archival PDF", "PDF"
    except Exception as exc:
        return False, f"PDF error: {exc}", _filetype_fallback(filepath)


def screen_file(filepath: Path) -> Tuple[bool, dict]:
    """Screen a single media file.

    Returns
    -------
    (status, props)
        status : True (accepted) | False (rejected)
        props  : dict with keys file_size_mb, pixel_width, pixel_height,
                 format, capture_date, format_issues
    """
    props = {
        "file_size_mb":  None,
        "pixel_width":   None,
        "pixel_height":  None,
        "format":        None,
        "capture_date":  None,
        "date_source":   None,
        "format_issues": [],
        "repair_issues": [],
        "pdf_pages":     None,
    }

    suffix = filepath.suffix.lower()

    if suffix not in _ACCEPTED_SUFFIXES:
        # Not one of the three accepted formats — always Reject, and always
        # "Format not supported" regardless of whether PIL can open it (a
        # PNG is readable but still not a supported format). PIL is still
        # given a chance to identify the mode for display purposes.
        try:
            props["file_size_mb"] = filepath.stat().st_size / (1024 * 1024)
            _set_filesystem_date(props, filepath)
        except OSError:
            pass
        try:
            img = Image.open(filepath)
            img.load()
            props["format"] = _MODE_DESCRIPTIONS.get(img.mode, img.mode)
            props["pixel_width"], props["pixel_height"] = img.size
        except Exception:
            # Not an image PIL understands — fall back to a lightweight
            # magic-byte sniff so format still gets a short description
            # (e.g. "ZIP", "MP4") instead of staying blank.
            fmt = _filetype_fallback(filepath)
            if fmt != "Unreadable":
                props["format"] = fmt
        props["format_issues"].append("Format not supported")
        props["repair_issues"] = ["Reject"]
        return False, props

    # For TIFFs: check compression before the size check so Compress LZW is
    # recorded in repair_issues even when the file is also oversized.  PIL.open
    # is lazy here — only the header is read, no pixel decode.
    img = None
    if suffix in (".tif", ".tiff"):
        try:
            img = Image.open(filepath)
            tag_v2 = getattr(img, "tag_v2", {})
            if tag_v2.get(259) != _TIFF_LZW:
                props["repair_issues"].append("Compress LZW")
        except Exception:
            img = None  # open failure is surfaced in the main image path below

    # File-size check. An oversized uncompressed TIFF isn't hard-rejected —
    # it may still come in under the limit once LZW-compressed, so it's
    # flagged Check MBs and screening continues. Everything else oversized
    # (compressed TIFF, JPEG, PDF) is an exclusive Reject.
    try:
        mb = filepath.stat().st_size / (1024 * 1024)
        props["file_size_mb"] = mb
        max_mb = MAX_PDF_MB if suffix == ".pdf" else MAX_FILE_MB
        if mb > max_mb:
            if suffix in (".tif", ".tiff") and "Compress LZW" in props["repair_issues"]:
                props["repair_issues"].append("Check MBs")
                props["format_issues"].append(
                    f"{mb:.1f} MB — exceeds {max_mb} MB limit (uncompressed)"
                )
            else:
                props["format_issues"].append(
                    f"File too large: {mb:.1f} MB (limit {max_mb} MB)"
                )
                props["repair_issues"] = ["Reject"]
                return False, props
    except OSError as exc:
        props["format_issues"].append(f"Cannot read file: {exc}")
        props["format"] = _filetype_fallback(filepath)
        props["repair_issues"] = ["Reject"]
        return False, props

    # PDF path
    if suffix == ".pdf":
        ok, msg, flavor = _check_pdf_a1b(filepath)
        props["format"]  = flavor
        if not ok:
            props["format_issues"].append(msg)
            props["repair_issues"].append("Reject")
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
            _set_filesystem_date(props, filepath)
        return ok, props

    # Image path (JPEG / TIFF)
    # Open lazily — mode/size/n_frames/tag_v2 are readable without decoding
    # pixels. Full decode (img.load) is deferred to after all fast checks so
    # files that fail early never pay the decode cost.
    # For TIFFs img may already be open from the early compression check above.
    try:
        if img is None:
            img = Image.open(filepath)
    except Exception as exc:
        props["format_issues"].append(f"Cannot open image: {exc}")
        props["format"] = _filetype_fallback(filepath)
        props["repair_issues"] = ["Reject"]
        return False, props

    props["format"] = _MODE_DESCRIPTIONS.get(img.mode, img.mode)
    if img.mode not in _CLEAN_MODES.get(suffix, set()):
        if suffix in (".tif", ".tiff") and img.mode in ("RGBA", "LA", "I;16"):
            props["repair_issues"].append("Flatten")
        else:
            props["repair_issues"].append(img.mode.lower())
    w, h = img.size
    props["pixel_width"]  = w
    props["pixel_height"] = h

    # Megapixel check
    if w * h > MAX_MEGAPIXELS:
        props["format_issues"].append(
            f"Exceeds 108 MP: {w * h / 1_000_000:.1f} MP ({w}×{h})"
        )
        props["repair_issues"] = ["Reject"]
        return False, props

    # EXIF capture date via ExifTool (called once; et_tags reused below)
    et_tags = read_tags(filepath)
    raw = et_tags.get("EXIF:DateTimeOriginal") or et_tags.get("EXIF:ModifyDate")
    if raw:
        props["capture_date"] = str(raw)[:10].replace(":", "-")
        props["date_source"]  = "exif"
    if props["capture_date"] is None:
        _set_filesystem_date(props, filepath)

    # Format-specific checks
    if suffix in (".jpg", ".jpeg"):
        if img.mode != "RGB":
            props["repair_issues"] = ["Reject"]
            return False, props

    elif suffix in (".tif", ".tiff"):
        if getattr(img, 'n_frames', 1) > 1:
            props["repair_issues"] = ["Reject"]
            props["format_issues"].append("Multi-frame TIFF is not accepted")
            return False, props

        tag_v2 = getattr(img, "tag_v2", {})
        compression = tag_v2.get(259)   # TIFF tag 259 = Compression

        # 32-bit float is non-repairable — return immediately.
        if img.mode == "F":
            props["format_issues"].append("32-bit float TIFFs are not accepted")
            props["repair_issues"] = ["Reject"]
            return False, props

        # Repairable checks — Compress LZW was already added to repair_issues
        # before the size check.
        if compression != _TIFF_LZW:
            props["format_issues"].append(
                f"TIFF must use LZW compression (code {compression} found)"
            )


        if props["repair_issues"]:
            return False, props

    # All checks passed — force full decode now to catch corrupt files.
    try:
        img.load()
    except Exception as exc:
        props["format_issues"].append(f"Cannot decode image: {exc}")
        props["format"] = _filetype_fallback(filepath)
        props["repair_issues"] = ["Reject"]
        return False, props

    return True, props


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
