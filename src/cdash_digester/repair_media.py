"""
CDASH Repair Media Module
- Takes a media file path and a list of issues as arguments.
- If the repair succeeds, the original file is backed up to a "repaired"
  subfolder inside the file's own media folder, and the repaired image is
  saved back over the original filepath (overwriting it). If the repair is
  refused or reverted, the original file is left completely untouched —
  nothing is backed up or deleted.
- Any PIL re-save carries the source image's EXIF forward (general practice,
  not rotation-specific) so metadata isn't silently dropped by a repair.

Issues and Remedies
- flatten      -> drop the alpha/16-bit channel (RGBA/LA/I;16) to a clean mode
- compress_lzw -> re-save with LZW compression
- check_mbs    -> after flatten/compress_lzw, re-check size against the
                  prescreener's file-size limit; only commit the repair if
                  it now fits, otherwise leave the file untouched
- reject       -> not repairable; refused with a message pointing at the
                  separate Reject action (services/reject.py), which is the
                  only thing that moves/removes the file

Rotation (Media > Rotate CW / Rotate CCW, see rotate_file()) is a separate,
user-initiated action, not driven by repair_issues:
- JPEG: metadata-only — rewrite the EXIF Orientation tag via ExifTool, no
  pixel re-encode. JPEG orientation is broadly honored downstream (and this
  app's own thumbnail pane already reads it via ImageOps.exif_transpose()),
  and JPEGs never carry repairable repair_issues in this codebase, so there's
  never anything to combine with.
- TIFF: physically bake the rotation into pixels — TIFF orientation-tag
  support is too inconsistent downstream to rely on. Any pending pixel-level
  repair_issues (Flatten/Compress LZW/Check MBs) are folded into the same
  pass. Any existing orientation tag (native TIFF tag 274 or EXIF) is reset
  to 1 (Normal) afterward — TIFF orientation tags are never trusted, read or
  written, for anything other than clearing them.
"""

import argparse
import csv
import io
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PIL import Image

from . import prescreener
from .exiftool_util import read_tags, write_orientation

# EXIF Orientation (numeric) → Pillow rotation angle in degrees CCW with expand=True
# Pillow positive angles are CCW; expand=True resizes canvas to fit rotated content.
_ORIENTATION_ROTATION = {
    3: 180,
    6: 90,   # phone rotated 90° CW → correct by rotating pixels 270° CCW (= 90° CW)
    8: 270,    # phone rotated 90° CCW → correct by rotating pixels 90° CCW
}
_DEFAULT_VERT_ROTATION = 90   # orientation=1 with portrait pixels: assume 90° CW fix

# JPEG rotation: composition tables for "current EXIF Orientation value, plus
# one more 90° turn -> new Orientation value". Empirically derived and
# round-trip verified against Pillow's own ImageOps.exif_transpose() table
# (see tests/test_repair_media.py) rather than worked out by hand — this
# composition is exactly the kind of thing that's easy to get subtly wrong,
# especially for the four mirrored states (2/4/5/7).
_ORIENTATION_ROTATE_CW  = {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1}
_ORIENTATION_ROTATE_CCW = {1: 8, 2: 5, 3: 6, 4: 7, 5: 4, 6: 1, 7: 2, 8: 3}

# TIFF EXIF carryover: purely descriptive IFD0 tags only, verified safe to
# copy into a freshly-written TIFF via tiffinfo=. Deliberately excludes
# structural/raster tags (StripOffsets, Compression, BitsPerSample, ...)
# that describe the *original* file's exact byte layout and would corrupt a
# newly-encoded file if copied over verbatim.
_SAFE_TIFF_METADATA_TAGS = {
    270,    # ImageDescription
    271,    # Make
    272,    # Model
    305,    # Software
    306,    # DateTime
    315,    # Artist
    33432,  # Copyright
}


def _normalize_issue(issue: str) -> str:
    normalized = issue.strip().lower().replace("-", "_")
    return normalized


def parse_repair_issues(issues: str | Iterable[str] | None) -> List[str]:
    if not issues:
        return []
    if isinstance(issues, str):
        raw_issues = issues.split(",")
    else:
        raw_issues = issues

    parsed: List[str] = []
    for issue in raw_issues:
        normalized = _normalize_issue(str(issue))
        if normalized and normalized not in parsed:
            parsed.append(normalized)
    return parsed


def _append_repair_reject_csv(
    catalog_path: Path, filepath: Path, issues: List[str], action: str
) -> str:
    """Append one row to catalog/repair_reject.csv — the shared log for both
    repair attempts/refusals (repair_file) and reject moves (RejectService).
    Returns a warning string on failure."""
    if catalog_path is None:
        return ""
    try:
        csv_path = catalog_path / "repair_reject.csv"
        write_header = not csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["MediaFolder", "Filename", "Repair_Issues", "Repair_Action"])
            w.writerow([
                filepath.parent.name,
                filepath.name,
                "|".join(issues),
                action,
            ])
        return ""
    except Exception as exc:
        return f" [repair_reject.csv write failed: {exc}]"


def _get_exif_orientation(filepath: Path):
    """Return numeric EXIF Orientation via ExifTool, or None on failure.

    ExifTool's flat -json output only group-prefixes a tag (e.g.
    "EXIF:Orientation") when it needs to disambiguate a same-named tag from
    another group (e.g. XMP) in that particular file — otherwise it reports
    the bare tag name. Both forms have to be checked.
    """
    tags = read_tags(filepath)
    return tags.get("EXIF:Orientation", tags.get("Orientation"))


def repair_file(
    filepath: Path,
    issues: List[str],
    catalog_path: Path = None,
) -> Tuple[bool, str]:
    """Apply repairs to filepath for the given issue codes.

    The original is backed up to <filepath.parent>/repaired/ and the repaired
    image written back over filepath, but only once every check has passed —
    a Reject-flagged file or a Check MBs repair that's still oversized after
    compression leaves the original completely untouched. If catalog_path is
    provided, appends an entry to catalog_path/repair_reject.csv either way.
    Returns (success, message).
    """
    issues = parse_repair_issues(issues)
    if not issues:
        return True, "No issues to repair"

    if "reject" in issues or "multiframe_tiff" in issues:
        msg = ("Cannot repair — file is flagged Reject. Use the Reject "
               "action to move it out of the batch.")
        return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)

    return _apply_pixel_repairs_and_commit(filepath, issues, catalog_path)


def _apply_pixel_repairs_and_commit(
    filepath: Path,
    issues: List[str],
    catalog_path: Path,
    rotate_degrees: Optional[int] = None,
) -> Tuple[bool, str]:
    """Shared pixel pipeline for repair_file() and rotate_file()'s TIFF path:
    open, apply flatten/compress_lzw/check_mbs for whatever issues are
    present, optionally rotate, then commit (backup + save) — or, for a
    Check MBs repair that's still oversized, leave the file untouched.
    ``issues`` is assumed already parsed/normalized and Reject-free.
    """
    try:
        raw_img = Image.open(filepath)
        raw_img.load()
        img = raw_img.copy()
        # JPEG carries EXIF forward via exif=; TIFF must NOT use exif= on
        # save — passing a TIFF-sourced getexif()/tag_v2 back in via exif=
        # corrupts the written file (reproduced against Pillow 12.2). TIFF
        # instead gets a small allowlist of purely descriptive IFD0 tags
        # (Make/Model/DateTime/etc.) via tiffinfo=, deliberately excluding
        # structural/raster tags (StripOffsets, Compression, ...) that only
        # make sense for the exact byte layout of the original file.
        source_exif = raw_img.getexif()
        source_tiffinfo = {
            tag: value for tag, value in getattr(raw_img, "tag_v2", {}).items()
            if tag in _SAFE_TIFF_METADATA_TAGS
        }
        raw_img.close()
    except Exception as exc:
        msg = f"Cannot open image: {exc}"
        return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)

    applied = []

    # 1. Flatten -> drop the alpha/16-bit channel to a clean mode
    if "flatten" in issues:
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
            applied.append("rgba->rgb")
        elif img.mode == "LA":
            background = Image.new("L", img.size, 255)
            background.paste(img.convert("L"), mask=img.split()[1])
            img = background
            applied.append("la->l")
        elif img.mode == "I;16":
            # PIL requires an intermediate "I" (32-bit int) step to correctly
            # scale 16-bit values down to the 0-255 range.
            img = img.convert("I").convert("L")
            applied.append("i;16->l")

    # 2. compress_lzw -> LZW applied on save (no pixel transform needed)
    if "compress_lzw" in issues:
        applied.append("lzw compression applied")

    # 3. Rotation (TIFF only, requested by rotate_file() — not repair_file())
    if rotate_degrees is not None:
        img = img.rotate(rotate_degrees, expand=True)
        applied.append(f"rotated {rotate_degrees} degrees")

    suffix = filepath.suffix.lower()
    tiff_save_kwargs = {"tiffinfo": source_tiffinfo} if source_tiffinfo else {}
    other_save_kwargs = {"exif": source_exif}

    # 4. check_mbs -> render to a buffer first and re-check size before
    # committing anything. Check MBs is TIFF-only by construction (it's only
    # ever paired with Compress LZW for an uncompressed oversized TIFF).
    buf = None
    if "check_mbs" in issues:
        buf = io.BytesIO()
        try:
            img.save(buf, format="TIFF", compression="tiff_lzw", **tiff_save_kwargs)
        except Exception as exc:
            msg = f"Cannot save repaired image: {exc}"
            return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)
        size_mb = buf.tell() / (1024 * 1024)
        if size_mb > prescreener._MAX_FILE_MB:
            msg = (
                f"Still {size_mb:.1f} MB after compression "
                f"(limit {prescreener._MAX_FILE_MB} MB) — cannot repair; "
                f"use the Reject action."
            )
            return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)
        applied.append(
            f"size now {size_mb:.1f} MB, within {prescreener._MAX_FILE_MB} MB limit"
        )

    # Only now commit: back up the original, then write the repaired image.
    orig_dir = filepath.parent / "repaired"
    orig_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(str(filepath), str(orig_dir / filepath.name))
    except Exception as exc:
        return False, f"Cannot back up original: {exc}"

    try:
        if buf is not None:
            with open(filepath, "wb") as f:
                f.write(buf.getvalue())
        elif suffix in (".tif", ".tiff"):
            img.save(str(filepath), compression="tiff_lzw", **tiff_save_kwargs)
        else:
            img.save(str(filepath), **other_save_kwargs)
    except Exception as exc:
        msg = f"Cannot save repaired image: {exc}"
        return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)

    if rotate_degrees is not None:
        # TIFF orientation tags are never trusted — always reset to Normal
        # after baking a rotation into pixels, regardless of what the source
        # had (including whatever the exif= carryover above preserved).
        write_orientation(filepath, 1)

    msg = "Repaired: " + "|".join(applied)
    return True, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)


def rotate_file(
    filepath: Path,
    direction: str,
    issues: str | Iterable[str] | None = None,
    catalog_path: Path = None,
) -> Tuple[bool, str]:
    """Rotate filepath 90 degrees. direction is "cw" or "ccw".

    JPEG: metadata-only EXIF Orientation rewrite, no pixel re-encode.
    TIFF: pixels physically rotated (any pending flatten/compress_lzw/
    check_mbs issues are folded into the same pass), orientation tag reset
    to 1 (Normal) afterward.
    Reject-flagged files and PDFs are refused, left completely untouched.
    Returns (success, message).
    """
    issues = parse_repair_issues(issues)
    suffix = filepath.suffix.lower()

    if "reject" in issues or "multiframe_tiff" in issues:
        msg = ("Cannot rotate — file is flagged Reject. Use the Reject "
               "action to move it out of the batch.")
        return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)

    if suffix == ".pdf":
        msg = "Rotation does not apply to PDF files"
        return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)

    if suffix in (".jpg", ".jpeg"):
        table = _ORIENTATION_ROTATE_CW if direction == "cw" else _ORIENTATION_ROTATE_CCW
        current = _get_exif_orientation(filepath) or 1
        new_value = table.get(current, table[1])
        if not write_orientation(filepath, new_value):
            msg = "Cannot write EXIF Orientation tag (is ExifTool on PATH?)"
            return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)
        msg = f"Rotated: orientation {current} -> {new_value}"
        return True, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)

    if suffix in (".tif", ".tiff"):
        degrees = -90 if direction == "cw" else 90
        return _apply_pixel_repairs_and_commit(
            filepath, issues, catalog_path, rotate_degrees=degrees
        )

    msg = "Rotation only applies to JPEG and TIFF files"
    return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)


def main():
    # python -m cdash_digester.repair_media ".\CDB260430-Test_batch\media\F6-Mass_Ave_Quincy_Central_Sqs_Views_Both_Sides-OF43111\Mass_Ave_0027p0001-VE-OP43296.tif" flatten compress_lzw

    parser = argparse.ArgumentParser(
        description="Repair a media file by applying fixes for known issues."
    )
    parser.add_argument("filepath", help="Path to the media file to repair")
    parser.add_argument(
        "issues", nargs="+",
        help="Issue codes to fix (e.g. flatten compress_lzw)"
    )
    args = parser.parse_args()

    filepath = Path(args.filepath)
    if not filepath.exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    success, message = repair_file(filepath, args.issues)
    print(message)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
