"""
CDASH Repair Media Module
- Takes a media file path and a list of issues as arguments.
- The original file is backed up to a Rejects/orig/ subfolder.
- Repairs are applied with Pillow and the result is saved back to the
  original filepath (overwriting it).

Issues and Remedies
- rgba -> convert to rgb
- la   -> convert grayscale+alpha to 8-bit grayscale
- i;16 -> convert 16-bit grayscale to 8-bit grayscale
- iphone_vert -> Rotate based on EXIF orientation; default 90 degrees CCW

"""

import argparse
import csv
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image

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


def _append_rejects_csv(
    catalog_path: Path, filepath: Path, issues: List[str], action: str
) -> str:
    """Append one row to Catalog/rejects.csv. Returns a warning string on failure."""
    if catalog_path is None:
        return ""
    try:
        csv_path = catalog_path / "rejects.csv"
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
        return f" [rejects.csv write failed: {exc}]"


def _get_exif_orientation(filepath: Path):
    """Return numeric EXIF Orientation via ExifTool, or None on failure."""
    return read_tags(filepath).get("EXIF:Orientation")


def repair_file(
    filepath: Path,
    issues: List[str],
    catalog_path: Path = None,
) -> Tuple[bool, str]:
    """Apply repairs to filepath for the given issue codes.

    Creates <filepath.parent>/Rejects/orig/ and .../repaired/ as needed.
    Copies the original to orig/ then writes the repaired image to repaired/.
    If catalog_path is provided, appends an entry to catalog_path/rejects.txt.
    Returns (success, message).
    """
    issues = parse_repair_issues(issues)
    if not issues:
        return True, "No issues to repair"

    orig_dir = filepath.parent / "rejects" 
    orig_dir.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(str(filepath), str(orig_dir / filepath.name))
    except Exception as exc:
        return False, f"Cannot back up original: {exc}"

    if "multiframe_tiff" in issues:
        msg = "Cannot repair multi-frame TIFF — manual intervention required"
        warn = _append_rejects_csv(catalog_path, filepath, issues, msg)
        try:
            filepath.unlink()
        except Exception as exc:
            warn += f" [remove failed: {exc}]"
        return False, msg + warn

    if "reject" in issues:
        msg = "Cannot repair non-PDF/A — manual conversion required"
        warn = _append_rejects_csv(catalog_path, filepath, issues, msg)
        try:
            filepath.unlink()
        except Exception as exc:
            warn += f" [remove failed: {exc}]"
        return False, msg + warn

    try:
        raw_img = Image.open(filepath)
        raw_img.load()
        img = raw_img.copy()
        raw_img.close()
    except Exception as exc:
        msg = f"Cannot open image: {exc}"
        return False, msg + _append_rejects_csv(catalog_path, filepath, issues, msg)

    applied = []

    # 1. Mode conversions (must happen before rotation so rotate works on a clean mode)
    if "rgba" in issues:
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
            applied.append("rgba->rgb")

    if "la" in issues:
        if img.mode == "LA":
            background = Image.new("L", img.size, 255)
            background.paste(img.convert("L"), mask=img.split()[1])
            img = background
            applied.append("la->l")

    if "i;16" in issues:
        if img.mode == "I;16":
            # PIL requires an intermediate "I" (32-bit int) step to correctly
            # scale 16-bit values down to the 0-255 range.
            img = img.convert("I").convert("L")
            applied.append("i;16->l")

    # 2. iphone_vert -> rotate to landscape orientation
    if "iphone_vert" in issues:
        orientation = _get_exif_orientation(filepath)
        angle = _ORIENTATION_ROTATION.get(orientation, _DEFAULT_VERT_ROTATION)
        img = img.rotate(angle, expand=True)
        applied.append(f"rotated {angle} degrees CCW")

    # 3. wrong_compression -> LZW applied on save (no pixel transform needed)
    if "wrong_compression" in issues:
        applied.append("lzw compression applied")

    suffix = filepath.suffix.lower()
    try:
        if suffix in (".tif", ".tiff"):
            img.save(str(filepath), compression="tiff_lzw")
        else:
            img.save(str(filepath))
    except Exception as exc:
        msg = f"Cannot save repaired image: {exc}"
        return False, msg + _append_rejects_csv(catalog_path, filepath, issues, msg)

    msg = "Repaired: " + "|".join(applied)
    return True, msg + _append_rejects_csv(catalog_path, filepath, issues, msg)


def main():
    # python -m cdash_digester.repair_media ".\CDB260430-Test_batch\media\F6-Mass_Ave_Quincy_Central_Sqs_Views_Both_Sides-OF43111\Mass_Ave_0027p0001-VE-OP43296.tif" rgba iphone_vert

    parser = argparse.ArgumentParser(
        description="Repair a media file by applying fixes for known issues."
    )
    parser.add_argument("filepath", help="Path to the media file to repair")
    parser.add_argument(
        "issues", nargs="+",
        help="Issue codes to fix (e.g. rgba iphone_vert)"
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
