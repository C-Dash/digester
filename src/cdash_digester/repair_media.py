"""
CDASH Repair Media Module
- Takes a media file path and a list of issues as arguments.
- If necessary, a rejects folder with subdirectories for orig and repaired are created in the parent folder.
- the media file is copied to the orig folder
- the python pillow library is used to address the issues and writes the repaired media file to the repaired folder.

Issues and Remedies
- rgba format -> convert to rgb
- iphone-vert -> Rotate based on EXIF orientation; default 270° CCW

"""

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

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


def _get_exif_orientation(filepath: Path):
    """Return numeric EXIF Orientation via ExifTool, or None on failure."""
    try:
        with exiftool.ExifToolHelper() as et:
            results = et.get_metadata(str(filepath), params=["-n"])
        return (results[0] if results else {}).get("EXIF:Orientation")
    except Exception:
        return None


def repair_file(filepath: Path, issues: List[str]) -> Tuple[bool, str]:
    """Apply repairs to filepath for the given issue codes.

    Creates <filepath.parent>/Rejects/orig/ and .../repaired/ as needed.
    Copies the original to orig/ then writes the repaired image to repaired/.
    Returns (success, message).
    """
    if not issues:
        return True, "No issues to repair"

    rejects_root = filepath.parent / "Rejects"
    orig_dir     = rejects_root / "orig"
    repaired_dir = rejects_root / "repaired"
    orig_dir.mkdir(parents=True, exist_ok=True)
    repaired_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(str(filepath), str(orig_dir / filepath.name))

    try:
        img = Image.open(filepath)
        img.load()
    except Exception as exc:
        return False, f"Cannot open image: {exc}"

    applied = []

    # 1. RGBA → RGB (must happen before rotation so rotate works on a clean mode)
    if "rgba" in issues:
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
            applied.append("rgba→rgb")

    # 2. iphone-vert → rotate to landscape orientation
    if "iphone-vert" in issues:
        orientation = _get_exif_orientation(filepath)
        angle = _ORIENTATION_ROTATION.get(orientation, _DEFAULT_VERT_ROTATION)
        img = img.rotate(angle, expand=True)
        applied.append(f"rotated {angle}° CCW")

    suffix = filepath.suffix.lower()
    dest = repaired_dir / filepath.name
    try:
        if suffix in (".tif", ".tiff"):
            img.save(str(dest), compression="tiff_lzw")
        else:
            img.save(str(dest))
    except Exception as exc:
        return False, f"Cannot save repaired image: {exc}"

    return True, "Repaired: " + ", ".join(applied)


def main():
    # python -m cdash_digester.repair_media ".\CDB260430-Test_batch\media\F6-Mass_Ave_Quincy_Central_Sqs_Views_Both_Sides-OF43111\Mass_Ave_0027p0001-VE-OP43296.tif" rgba iphone-vert

    parser = argparse.ArgumentParser(
        description="Repair a media file by applying fixes for known issues."
    )
    parser.add_argument("filepath", help="Path to the media file to repair")
    parser.add_argument(
        "issues", nargs="+",
        help="Issue codes to fix (e.g. rgba iphone-vert)"
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
