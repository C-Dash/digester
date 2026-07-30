"""
CDASH Repair Media Module
- Takes a media file path and a list of issues as arguments.
- If the repair succeeds, the original file is backed up to a "repaired"
  subfolder inside the file's own media folder, and the repaired image is
  saved back over the original filepath (overwriting it). If the repair is
  refused or reverted, the original file is left completely untouched —
  nothing is backed up or deleted.

Issues and Remedies
- flatten      -> drop the alpha/16-bit channel (RGBA/LA/I;16) to a clean mode
- compress_lzw -> re-save with LZW compression
- check_mbs    -> after flatten/compress_lzw, re-check size against the
                  prescreener's file-size limit; only commit the repair if
                  it now fits, otherwise leave the file untouched
- reject       -> not repairable; refused with a message pointing at the
                  separate Reject action (services/reject.py), which is the
                  only thing that moves/removes the file

The EXIF-orientation rotation helpers below (_ORIENTATION_ROTATION,
_DEFAULT_VERT_ROTATION, _get_exif_orientation) are not currently wired to any
repair_issues code — iphone_vert was retired from the prescreener — but are
kept for the planned Media > Rotate CW / Rotate CCW menu actions.
"""

import argparse
import csv
import io
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image

from . import prescreener
from .exiftool_util import read_tags

# EXIF Orientation (numeric) → Pillow rotation angle in degrees CCW with expand=True
# Pillow positive angles are CCW; expand=True resizes canvas to fit rotated content.
_ORIENTATION_ROTATION = {
    3: 180,
    6: 90,   # phone rotated 90° CW → correct by rotating pixels 270° CCW (= 90° CW)
    8: 270,    # phone rotated 90° CCW → correct by rotating pixels 90° CCW
}
_DEFAULT_VERT_ROTATION = 90   # orientation=1 with portrait pixels: assume 90° CW fix


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
    """Return numeric EXIF Orientation via ExifTool, or None on failure."""
    return read_tags(filepath).get("EXIF:Orientation")


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

    try:
        raw_img = Image.open(filepath)
        raw_img.load()
        img = raw_img.copy()
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

    suffix = filepath.suffix.lower()

    # 3. check_mbs -> render to a buffer first and re-check size before
    # committing anything. Check MBs is TIFF-only by construction (it's only
    # ever paired with Compress LZW for an uncompressed oversized TIFF).
    buf = None
    if "check_mbs" in issues:
        buf = io.BytesIO()
        try:
            img.save(buf, format="TIFF", compression="tiff_lzw")
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
            img.save(str(filepath), compression="tiff_lzw")
        else:
            img.save(str(filepath))
    except Exception as exc:
        msg = f"Cannot save repaired image: {exc}"
        return False, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)

    msg = "Repaired: " + "|".join(applied)
    return True, msg + _append_repair_reject_csv(catalog_path, filepath, issues, msg)


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
