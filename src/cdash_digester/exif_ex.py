"""
exif_ex.py  —  Dump all ExifTool tags for an image file.

Usage
-----
    python -m cdash_digester.exif_ex <filepath>

Requires ExifTool (https://exiftool.org) on PATH. Tags are read via the shared
one-shot subprocess helper (see exiftool_util.read_tags).
"""

import argparse

from .exiftool_util import read_tags


def main():
    parser = argparse.ArgumentParser(
        description="Extract and display all ExifTool tags for an image file."
    )
    parser.add_argument("filepath", help="Path to the image file")
    args = parser.parse_args()

    # numeric=False keeps human-readable tag values (e.g. Orientation names).
    tags = read_tags(args.filepath, numeric=False)

    if not tags:
        print("No metadata returned — is ExifTool installed and on PATH?")
        return

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
