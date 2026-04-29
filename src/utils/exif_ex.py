"""
exif_ex.py  —  Dump all ExifTool tags for an image file.

Usage
-----
    python src/utils/exif_ex.py <filepath>

Requires ExifTool (https://exiftool.org) on PATH and the pyexiftool package:
    pip install pyexiftool
"""

import argparse
import sys

import exiftool


def main():
    parser = argparse.ArgumentParser(
        description="Extract and display all ExifTool tags for an image file."
    )
    parser.add_argument("filepath", help="Path to the image file")
    args = parser.parse_args()

    try:
        with exiftool.ExifToolHelper() as et:
            results = et.get_metadata(args.filepath)
    except exiftool.exceptions.ExifToolExecuteError as exc:
        print(f"ExifTool error: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("ExifTool executable not found — install it and ensure it is on PATH.",
              file=sys.stderr)
        sys.exit(1)

    if not results:
        print("No metadata returned.")
        return

    tags = results[0]
    max_key = max((len(k) for k in tags), default=0)
    sep = "=" * 70

    print(f"\n{sep}")
    print(f"File: {args.filepath}")
    print(sep)

    for key, val in sorted(tags.items()):
        print(f"  {key:<{max_key}}  :  {val!r}")

    print()


if __name__ == "__main__":
    main()
