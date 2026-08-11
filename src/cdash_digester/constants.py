"""
CDASH domain constants.

A leaf module: it imports nothing from this package, so every layer — db/,
services/, gui/ — can depend on it freely. These used to live in
cdash_objects.py alongside BatchDB, which meant db/repositories.py imported
*upwards* into the persistence facade's own module and the resulting cycle had
to be broken with a function-local import.
"""

# Separator used to join cdash_media.filename_issues into one column.
FILENAME_ISSUE_SEP = ", "

# The name-side note for a document that is indexed and placed but not yet
# typed. Lives here rather than in services/scanning.py, which raises it,
# because db/repositories.py has to recognise and retract it when Assign
# Metadata supplies the missing type — and db/ must not import from services/.
NEEDS_DOC_TYPE_NOTE = "Needs Doc Type"

# Document type code → human description. Fixed vocabulary; the GUI's Assign
# Metadata dialog renders this in order.
DOC_TYPES: dict[str, str] = {
    "VE": "Exterior View",
    "VI": "Interior View",
    "RF": "Research Form",
    "AI": "Architectural Inventory Form",
    "VP": "Plan View",
    "CD": "Correspondence",
    "RN": "Research Notes",
    "HS": "Historic American Buildings Survey",
    "CS": "Contact Sheet",
    "AM": "Published Material",
    "SM": "Supplemental Material",
    "VD": "Detail View",
    "EP": "Ephemera",
    "DM": "Demolition Memo",
    "UC": "Uncategorized",
}

# ---------------------------------------------------------------------------
# Repair issue vocabulary
# ---------------------------------------------------------------------------
# The canonical spelling of each code, as written to cdash_media.repair_issues
# and shown in the GUI. These live here, in the leaf both sides can import,
# because prescreener.py *raises* these codes and repair_media.py *matches*
# them — and repair_media already imports prescreener (for MAX_FILE_MB), so
# the vocabulary cannot live in either without a cycle.
#
# They were bare string literals at both ends, and the two ends disagreed:
# the prescreener raised "Compress LZW" / "Check MBs", while repair_media
# tested for "compress_lzw" / "check_mbs". normalize_repair_issue() only
# translated hyphens, not spaces, so neither branch ever ran. LZW still got
# applied — the TIFF save path applies it unconditionally — but the "Check MBs"
# size re-check, whose whole job is to REFUSE a file that is still oversized
# after compression, was dead code, and every such file was committed and
# reported as repaired. Naming the codes once, and deriving the match token
# from the same string by the same function, is what stops that recurring.
REPAIR_REJECT = "Reject"
REPAIR_FLATTEN = "Flatten"
REPAIR_COMPRESS_LZW = "Compress LZW"
REPAIR_CHECK_MBS = "Check MBs"
# No longer raised by the prescreener (a multi-frame TIFF is "Reject" now), but
# still matched, so a value stored by an older version keeps its meaning.
REPAIR_MULTIFRAME_TIFF = "multiframe-tiff"


def normalize_repair_issue(issue: str) -> str:
    """Canonical match token for a repair code: lowercase, separators unified.

    Comparison form only — lossy, and never what gets displayed or stored.
    Whitespace and hyphens both fold to "_", so "Compress LZW", "compress-lzw"
    and "compress_lzw" are one code.
    """
    return "_".join(str(issue).strip().lower().replace("-", " ").split())


# Match tokens, derived rather than written out, so a code and the token used
# to test for it cannot drift apart.
REJECT_TOKEN = normalize_repair_issue(REPAIR_REJECT)
FLATTEN_TOKEN = normalize_repair_issue(REPAIR_FLATTEN)
COMPRESS_LZW_TOKEN = normalize_repair_issue(REPAIR_COMPRESS_LZW)
CHECK_MBS_TOKEN = normalize_repair_issue(REPAIR_CHECK_MBS)
MULTIFRAME_TIFF_TOKEN = normalize_repair_issue(REPAIR_MULTIFRAME_TIFF)


# Separator between the place and the document type in a document title.
# A plain hyphen-minus, deliberately: an em dash had crept into the scanner's
# copy of this format string while both assignment paths used "-", so the same
# document was titled differently depending on which one created it.
DOC_TITLE_SEP = " - "


def format_doc_title(place: str, doc_type_code: str) -> str:
    """Build a document title: "<place> - <document type description>".

    The single definition of the format. It was inlined at three call sites,
    which is how the separators drifted apart in the first place. An
    unrecognised 2-letter code shows itself; an absent one reads
    "Uncategorized".
    """
    description = DOC_TYPES.get(doc_type_code, doc_type_code or "Uncategorized")
    return f"{place}{DOC_TITLE_SEP}{description}"


# Place property keys shared by the validator output, the cdash_place working
# table, and the cdash_place_cache table.
PLACE_PROP_KEYS = (
    "place_name", "place_type", "house_num", "street_name", "street_sort",
    "neighborhood", "chc_dist", "item_set_ids", "lat", "lon",
)
