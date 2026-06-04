"""
CDASH Repair Media Module
- Takes a media file path and a list of issues as arguments.
- The original file is backed up to a Rejects/orig/ subfolder.
- Repairs are applied with Pillow and the result is saved back to the
  original filepath (overwriting it).

Issues and Remedies
- rgba -> convert to rgb
- iphone_vert -> Rotate based on EXIF orientation; default 90 degrees CCW

"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image
import exiftool

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


def _get_exif_orientation(filepath: Path):
    """Return numeric EXIF Orientation via ExifTool, or None on failure."""
    try:
        with exiftool.ExifToolHelper() as et:
            results = et.get_metadata(str(filepath), params=["-n"])
        return (results[0] if results else {}).get("EXIF:Orientation")
    except Exception:
        return None


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
    if "multiframe_tiff" in issues:
        return False, "Cannot repair multi-frame TIFF — manual intervention required"

    orig_dir = filepath.parent / "Rejects" / "orig"
    orig_dir.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(str(filepath), str(orig_dir / filepath.name))
    except Exception as exc:
        return False, f"Cannot back up original: {exc}"

    rejects_warning = ""
    if catalog_path is not None:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"{timestamp} | {filepath} | {', '.join(issues)}\n"
            with open(catalog_path / "rejects.txt", "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as exc:
            rejects_warning = f" [rejects.txt write failed: {exc}]"

    try:
        raw_img = Image.open(filepath)
        raw_img.load()
        img = raw_img.copy()
        raw_img.close()
    except Exception as exc:
        return False, f"Cannot open image: {exc}"

    applied = []

    # 1. RGBA -> RGB (must happen before rotation so rotate works on a clean mode)
    if "rgba" in issues:
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
            applied.append("rgba->rgb")

    # 2. iphone_vert -> rotate to landscape orientation
    if "iphone_vert" in issues:
        orientation = _get_exif_orientation(filepath)
        print("orientation is: ", orientation)
        angle = _ORIENTATION_ROTATION.get(orientation, _DEFAULT_VERT_ROTATION)
        img = img.rotate(angle, expand=True)
        applied.append(f"rotated {angle} degrees CCW")

    suffix = filepath.suffix.lower()
    try:
        if suffix in (".tif", ".tiff"):
            img.save(str(filepath), compression="tiff_lzw")
        else:
            img.save(str(filepath))
    except Exception as exc:
        return False, f"Cannot save repaired image: {exc}"

    return True, "Repaired: " + ", ".join(applied) + rejects_warning


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
