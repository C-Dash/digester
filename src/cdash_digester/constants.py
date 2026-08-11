"""
CDASH domain constants.

A leaf module: it imports nothing from this package, so every layer — db/,
services/, gui/ — can depend on it freely. These used to live in
cdash_objects.py alongside BatchDB, which meant db/repositories.py imported
*upwards* into the persistence facade's own module and the resulting cycle had
to be broken with a function-local import.
"""

# Document type code → human description. Fixed vocabulary; the GUI's Assign
# Metadata dialog renders this in order.
# Separator used to join cdash_media.filename_issues into one column.
FILENAME_ISSUE_SEP = ", "

# The name-side note for a document that is indexed and placed but not yet
# typed. Lives here rather than in services/scanning.py, which raises it,
# because db/repositories.py has to recognise and retract it when Assign
# Metadata supplies the missing type — and db/ must not import from services/.
NEEDS_DOC_TYPE_NOTE = "Needs Doc Type"

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

# Place property keys shared by the validator output, the cdash_place working
# table, and the cdash_place_cache table.
PLACE_PROP_KEYS = (
    "place_name", "place_type", "house_num", "street_name", "street_sort",
    "neighborhood", "chc_dist", "item_set_ids", "lat", "lon",
)
