# CDASH Presort Digester — Requirements v5
*Revised 2026-04-16. Based on `requirements_260407_v5_claude.md`.*

---

## 1. Project Overview

This project produces a desktop tool that assists archivists in organizing image and PDF files from various sources into **Archival Information Packages (AIPs)** as defined by the OAIS Reference Model. Media files are associated with Folders and Places as referenced in the **Cambridge Digital Architectural Survey and History (CDASH)** and its Omeka-S online repository.

### 1.1 Target Platform

- **Primary:** Windows 11 desktop application
- **Secondary (future):** macOS, Linux — architecture should not preclude this, but it is not a current requirement.

### 1.2 Shareability

Code is intended to be maintained and shared with others. It is structured as an installable Python package. Eventually to be packaged as a Windows executable with PyInstaller.

### 1.3 Starting the Application

```
cd <project_root>
python run_gui.py
```

---

## 2. Application Context

CDASH is a web-based repository and access application based on Omeka-S. In the CDASH schema, media files represent pages of CDASH Document Items. Each Document Item is related to a single CDASH Place Item and a single CDASH Folder (an Omeka Item Set).

The overall function of the CDASH Digester is to assist the CDASH manager with organizing media files for bulk accession to the online CDASH repository.

The CDASH manager receives image and PDF files from Historical Commission staff for accession into CDASH. The workflow involves assembling incoming media files into media folders on the desktop file system — one folder per CDASH Item Set.

The CDASH Pre-Sort Digester transforms that initial assembly into a CDASH Import Batch: a folder structure containing validated, renamed media files plus CSV files that instruct the Omeka CSV Import tool to create new CDASH Document Items, link them to existing Place Items, and upload the media files.

The process of validating and renaming files may take more than one iteration of batch scan and interactive adjustment. When all media files are validated (correct format, correct naming, correct CDASH references), the Digester produces the CSV output files. Completed batches are then preserved in cold storage.

---

## 3. Object and Data Model

The application uses SQLite to model and persist the properties and hierarchical relationships among the Batch, its Media Folders, implicit Documents, and associated CDASH Places.

All status columns use `BOOL` (stored as `INTEGER 0/1` in SQLite; surfaced as Python `True`/`False` via the row factory in `BatchDB`).

### 3.1 Module: `cdash_objects.py`

Contains all data classes, the `BatchDB` persistence layer, name parsers, and the `slugify` utility. This is the only module that touches the SQLite database.

#### `batch_db` class

Handles the database connection and executes all queries and commits.

**Responsibilities:**
- Create all tables (`create_all_tables`)
- CRUD for every entity (batch, folder, place, doc, media, rejects)
- Recalculate derived status fields (`recalculate_folder_status`, `recalculate_batch_ready`)
- Export the four CSV output files (delegated to `Digester`)

#### `cdash_batch` table

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Autoincrement |
| `batch_id` | TEXT | e.g., `CDB260319` — parsed from folder name |
| `name` | TEXT | Mnemonic name — second token of batch folder name |
| `batch_folder_path` | TEXT | Absolute OS path to batch root |
| `initialized_date` | TEXT | ISO 8601 date of last initialization |
| `rejected_count` | INTEGER | Count of files in the Rejects folder |
| `ready` | BOOL | True iff all folders are name-ready and media-ready |
| `note` | TEXT | Comma-delimited notes |

**Methods:** parse and validate batch name; initialize batch folder and DB; getters/setters.

#### `cdash_folder` table

| Column | Type | Notes |
|---|---|---|
| `folder_number` | INTEGER PK | Sequential folder index within batch (assigned by scanner) |
| `batch_folder_id` | TEXT | Batch_id + F + folder_number e.g. CDB260605F2
| `cdash_folder_name` | TEXT | Slugified Omeka Item Set name, e.g., `East_Cambridge` |
| `item_set_id` | INTEGER UNIQUE | Omeka Item Set ID |
| `os_folder_name` | TEXT | Actual current OS folder name |
| `name_ready` | BOOL | True iff folder name is valid and Item Set ID validated |
| `media_ready` | BOOL | True iff all media files in the folder are ready |
| `notes` | TEXT | Comma-delimited notes |

**Methods:** folder name parser and validator; OS folder renamer; scan media in folder; getters/setters.

#### `cdash_place` table

| Column | Type | Notes |
|---|---|---|
| `place_item_id` | INTEGER PK | Omeka Place Item ID — parsed from media filename (`-OP<id>`) |
| `place_name` | TEXT | From API: `cdash:placeItem[0]["@value"]` |
| `place_type` | TEXT | From API: `cdash:placeType` |
| `house_num` | TEXT | From API: `cdash:houseNum` |
| `street_name` | TEXT | From API: `cdash:streetName` |
| `street_sort` | TEXT | From API: `cdash:streetSort` — used for CSV sort order |
| `neighborhood` | TEXT | From API: `cdash:neighborhood` (comma-delimited if multiple) |
| `chc_dist` | TEXT | From API: `cdash:chcDist` (comma-delimited if multiple) |
| `item_set_ids` | TEXT | From API: `o:item_set[*]["o:id"]` — comma-delimited list of Item Set IDs this place belongs to |
| `lat` | REAL | From API: `cdash:lat` |
| `lon` | REAL | From API: `cdash:lon` |
| `ready` | BOOL | True iff place was successfully validated via API or cache |
| `notes` | TEXT | Comma-delimited notes |

**Methods:** validate place ID (calls `CDASHValidator`); cache API result; getters/setters.

> **Note (future feature):** `item_set_ids` enables a cross-reference check — if the parent folder's `item_set_id` is not in `item_set_ids`, a warning should be logged. This check is deferred.

#### `cdash_doc` table

| Column | Type | Notes |
|---|---|---|
| `doc_item_id` | INTEGER PK | Autoincrement — internal batch document ID |
| `place_item_id` | INTEGER FK → `cdash_place` | |
| `folder_doc_sequence` | INTEGER | Sequential document index within the folder |
| `item_set_id` | INTEGER FK → `cdash_folder` | |
| `doc_title` | TEXT | `place_name + " — " + doc_type_description` |
| `doc_type_code` | TEXT | e.g., `VE` — from `DOC_TYPES` |
| `doc_type_description` | TEXT | e.g., `Exterior View` |
| `date_accepted` | TEXT | Earliest EXIF capture date among all pages; ISO 8601 |
| `batch_doc_id` | TEXT | Stem shared by all pages: `<batch_folder_id>-<place_slug>-<doc_index:04d>-<doc_type>` |
| `num_pages` | INTEGER | Count of associated media files |
| `ready` | BOOL | True iff all pages are ready and place is valid |
| `notes` | TEXT | Comma-delimited notes |

**Methods:** getters/setters; file name evaluator; `renumber_doc_pages` (reassigns `page_num` 1..N in filename-alphabetical order after un-grouping).

#### `cdash_media` table

| Column | Type | Notes |
|---|---|---|
| `media_id` | INTEGER PK | Autoincrement |
| `doc_item_id` | INTEGER FK → `cdash_doc` | NULL if not yet associated with a document |
| `item_set_id` | INTEGER FK → `cdash_folder` | |
| `filename` | TEXT | Current OS filename |
| `batch_media_id` | TEXT |<batch_folder_id>-<place_slug>-<doc_index:04d>p<page_index:04d>-<doc_type>`
| `filepath` | TEXT | Path relative starting at batch root |
| `page_num` | INTEGER | Page number within document (1-based) |
| `capture_date` | TEXT | From EXIF `DateTimeOriginal`; ISO 8601 |
| `file_size_mb` | REAL | |
| `pixel_width` | INTEGER | |
| `pixel_height` | INTEGER | |
| `format_note` | TEXT | e.g., `RGB`, `L`, `PDF/A-1b` |
| `ready` | BOOL | True iff format accepted, name valid, and place/doc references valid |
| `notes` | TEXT | Comma-delimited list of issues |

**Methods:** parse and validate name; screen media format (calls `prescreener.screen_file`); OS file renamer; getters/setters.

#### `media_rejects` table

Same schema as `cdash_media`. Populated when a file fails format screening and is moved to `Rejects/`.

**Methods:** getters/setters; count rejected files; repair and restore tools (future).

---

## 4. Batch Folder Structure

### 4.1 Before Initialization

The archivist creates this folder structure manually before running the tool.

```
CDB<YYMMDD>[a-z]?-<batch_name>/
  Media/
    <folder_name>-OF<OmekaItemSetID>/   ← one folder per CDASH Item Set
      <media_file_name>.<sfx>            ← tif, tiff, jpg, jpeg, pdf
```

The trailing letter on the batch ID is optional and distinguishes multiple batches created on the same day (e.g., `CDB260319a`, `CDB260319b`).

### 4.2 After Initialization

```
CDB<YYMMDD>[a-z]?-<batch_name>/
  Media/
    F<folder_index>-<Item_Set_Slug>-OF<OmekaItemSetID>/
      <place_slug>-<doc_index:04d>p<page_index:04d>-<doc_type>-OP<place_id>.<sfx>
  Catalog/
    batch_db.sqlite
    batch.csv
    place.csv
    document.csv
    media.csv
    batch.log
  Rejects/
    rejects.csv
    <Item_Set_Folder>/
      <rejected media files>
```

If batch, folder, or media file names already follow the post-initialized pattern, they are not renamed when re-scanning.

### 4.3 Name Conventions

#### Batch folder name

```
CDB<YYMMDD>[a-z]?-<batch_name>
```
Example: `CDB260319-Cambridge_Hill`

#### Media folder name (ready form)

```
F<folder_index>-<Omeka_Item_Set_Slug>-OF<OmekaItemSetID>
```
Example: `F3-168_Brattle_St-OF160936`

A folder name that omits `F<folder_index>` is accepted on input; the scanner assigns the index. A folder whose `OmekaItemSetID` fails API validation has `name_ready = False`.

#### Media file name (ready form)

```
<place_slug>_<doc_index:04d>p<page_index:04d>-<doc_type>-OP<place_id>.<sfx>
```
Example: `12_Reservoir_St_0017p0001-VE-OP196223.pdf`

A rudimentary name (e.g., `photo001.tif`) is accepted on input and registered with `ready = False`. If the stem contains a hint token such as `-OP<place_id>` or `-<doc_type>`, these are extracted and used during interactive assignment.

#### `slugify` function

Replaces spaces with underscores; removes all non-alphanumeric characters except `_`. 
` - ` is replaced by `-`

---

## 5. Module Architecture

### Package layout

```
cdash_digester/
├── run_gui.py                    ← launch script
├── pyproject.toml
├── src/
│   └── cdash_digester/
│       ├── __init__.py
│       ├── cdash_objects.py      ← data model, BatchDB, name parsers, slugify, DOC_TYPES
│       ├── validator.py          ← CDASHValidator — Omeka-S REST API calls
│       ├── prescreener.py        ← screen_file() — format and size checks
│       ├── digester.py           ← Digester controller; __main__ CLI harness
│       └── gui/
│           ├── __init__.py
│           ├── main_window.py    ← MainWindow, _Worker (QThread), _AssignDialog
│           ├── folder_pane.py    ← FolderPane (QTreeWidget)
│           ├── media_table.py    ← MediaTablePane (QTableView + model)
│           ├── thumbnail_pane.py ← ThumbnailPane (scrollable grid)
│           └── console_window.py ← ConsoleWindow (QDockWidget)
└── tests/
```

### 5.1 `validator.py` — `CDASHValidator`

Uses the Omeka-S REST API (`https://cdash.cambridgema.gov/api`) to validate Place and Item Set resource IDs. Returns data to the caller — does not write to the database.

Key API parameters:
- **Place items:** `GET /api/items?id=<id>&resource_template_id=4`
- **Item sets (folders):** `GET /api/item_sets?id=<id>&resource_template_id=8`

Place properties extracted from the API response:

| Property | JSON key |
|---|---|
| `place_name` | `cdash:placeItem[0]["@value"]` |
| `place_type` | `cdash:placeType[0]["@value"]` |
| `house_num` | `cdash:houseNum[0]["@value"]` |
| `street_name` | `cdash:streetName[0]["@value"]` |
| `street_sort` | `cdash:streetSort[0]["@value"]` |
| `neighborhood` | `cdash:neighborhood[*]["@value"]` (joined) |
| `chc_dist` | `cdash:chcDist[*]["@value"]` (joined) |
| `item_set_ids` | `o:item_set[*]["o:id"]` (joined) |
| `lat` | `o-module-mapping:lat[0]["@value"]` |
| `lon` | `o-module-mapping:lon[0]["@value"]` |

The validator degrades gracefully when offline. Callers use the `BatchDB` cache as fallback.

**Methods:**
- `validate_place(place_id) → (status: str, props: dict)` — returns all place properties or `{}` on failure
- `validate_folder(item_set_id) → (status: str, folder_name: str)`
- `validate_resource(resource_type, resource_id) → (status: str, title: str)` — generic

### 5.2 `prescreener.py` — `screen_file`

Checks a single media file against acceptance criteria. Returns `(True/False, props_dict)`. Does not touch the database.

**Accepted formats:**
- TIFF: LZW compression, 24-bit RGB or 8-bit grayscale (RGBA and 16-bit rejected)
- JPEG: 24-bit RGB
- PDF: PDF/A-1b (detected via `pdfaid:conformance` XMP marker, using `pymupdf`)

**Rejection criteria:**
- File size > 100 MB
- Width × Height > 108 megapixels
- Wrong color mode (CMYK, RGBA, Palette, etc.)
- Corrupt or unreadable file
- TIFF with non-LZW compression
- PDF without PDF/A-1b conformance marker
- EXIF tags reflecting 

`props` dict keys: `file_size_mb`, `pixel_width`, `pixel_height`, `color_mode`, `capture_date`, `qa_note`.

**On rejection (handled by `Digester`):** file is moved to `Rejects/<FolderName>/`; an entry is written to `media_rejects`; `media_ready` on the parent folder is set to `False`.

### 5.3 `digester.py` — `Digester`

The controller. All GUI menu actions delegate to `Digester` methods, which run on a `QThread` worker so the UI stays responsive.

#### `load_or_initialize(batch_path)`

Called when the user chooses a batch folder (`Batch → Choose Batch Folder`). Also triggers `scan_batch` automatically.

1. Validate batch folder name (must match `CDB<YYMMDD>[a-z]?-<name>`).
2. Confirm `Media/` sub-folder exists.
3. Create `Catalog/` if absent; append a timestamp separator to `batch.log` if it already exists.
4. Open (or create) `batch_db.sqlite` and create all tables.
5. Insert a `cdash_batch` record if none exists.

#### `scan_batch()`

Full batch scan. Rebuilds the database from scratch.

1. Delete and recreate `batch_db.sqlite`.
2. For each media folder in `Media/` (sorted alphabetically):
   a. Parse the folder name for `item_set_id`.
   b. Validate `item_set_id` via `CDASHValidator.validate_folder()`.
   c. Rename the folder to canonical form `F<index>-<slug>-OF<item_set_id>` if needed.
   d. Insert/update a `cdash_folder` record.
   e. For each media file in the folder (sorted alphabetically):
      - Parse the filename. If not in ready format: insert with `ready=False`, continue.
      - Validate `place_id` via `CDASHValidator.validate_place()` (or use cached DB record).
      - Track doc grouping by `doc_index`: new index → create `cdash_doc`; same index → increment pages.
      - Rename file to canonical form if place is fully known.
      - Screen format via `screen_file()`. 
      - Insert `cdash_media` record.
   f. Set `status` on the folder.
3. Set `rejected_count` on the batch. Recalculate `batch.ready`.

#### `validate_folder(item_set_id)` — Folder → Scan Selected Folder

Re-scans one folder: deletes existing media/doc records for the folder, re-runs `_scan_media_in_folder`, recalculates batch ready.

#### `assign_media_to_doc(media_ids, place_id, doc_type_code, is_multi_page)` — Media → Assign Metadata

- Validates `place_id` via API; registers place if not already cached.
- If `is_multi_page=True`: creates one new `cdash_doc`; assigns all selected files as sequential pages.
- If `is_multi_page=False`: creates one `cdash_doc` per selected file (each page 1 of 1).
- Renames files to canonical form.
- Sets `ready=True` on each affected media record; recalculates folder and batch status.

#### `export_csv()`

Only runs if `batch.ready = True`. Writes four CSV files to `Catalog/` (see Section 8).

#### CLI test harness (`__main__`)

```
python -m cdash_digester.digester
```

Copies `CDB260320-Test_batch` to a temp directory, runs `load_or_initialize` and `scan_batch`, and prints the status summary. The GUI is not launched. Useful for regression testing the scan pipeline.

---

## 6. Graphical Interface

Built with **PySide6**. Long-running Digester operations run on a `QThread` worker (`_Worker`) so the UI stays responsive. The worker redirects the Digester's log callback to a Qt signal, which Qt routes safely back to the main thread.

### 6.1 Pane Layout

```
┌─────────────────────────────────────────────────────────┐
│  Menu Bar                                               │
├──────────────┬──────────────────────────────────────────┤
│              │  Media Table Pane (top-right)            │
│ Folder Pane  │  QTableView — rows = media files in      │
│ (left)       │  selected folder, sorted by filename    │
│ QTreeWidget  ├──────────────────────────────────────────┤
│              │  Thumbnail Pane (bottom-right)           │
│ green = ready│  Scrollable grid of image thumbnails.    │
│ red = not    │  Rendered by Pillow (images) /           │
│   ready      │  pymupdf (PDF). Blank rect on failure.   │
└──────────────┴──────────────────────────────────────────┘
│  Console Window — QDockWidget, detachable               │
└─────────────────────────────────────────────────────────┘
```

### 6.2 Interaction Behavior

- All panes and menu items are disabled until the user chooses a valid batch folder.
- Choosing a batch folder triggers initialization and a full scan automatically.
- Folder names in the Folder Pane are colored green (`name_ready AND media_ready`) or red (either false).
- Selecting a folder populates both the Media Table and Thumbnail Pane with that folder's files.
- The Media Table shows: `Filename`, `Type` (doc_type_code), `Page`, `Status` (Ready/Not Ready), `Notes`. Not-ready rows have a light red background.
- Thumbnail border color reflects `ready` status: green (ready), red (not ready), grey (pending/unseen). Thumbnails fit within 200×200 px.
- Selection is synchronized bidirectionally between the Media Table and Thumbnail Pane (Ctrl-click for multi-select in the Thumbnail Pane).
- The selected set of media file records is the subject for Media menu operations.

### 6.3 Console Window

- `QDockWidget` containing a read-only `QTextEdit`. Can be floated and docked.
- Timestamped, color-coded messages: errors in red, warnings in orange, success in green, info in black.
- Output is also appended to `Catalog/batch.log`.

---

## 7. Menus

All menu operations call `Digester` methods. On completion, the Folder Pane, Media Table, and Thumbnail Pane are refreshed.

### 7.1 Batch Menu

| Item | Action |
|---|---|
| Choose Batch Folder | Presents folder picker; runs `load_or_initialize` then `scan_batch` on the selected folder |
| Initialize / Validate Batch | Re-runs `scan_batch` on the currently open batch (rebuilds DB from scratch) |
| Produce CSV Files | Runs `export_csv`; grayed out if `batch.ready = False` |
| Write Status to Console | Prints the status summary (folder count, ready count, reject count) to the console |

### 7.2 Folder Menu

Operates on the single media folder currently selected in the Folder Pane.

| Item | Action |
|---|---|
| Scan Selected Folder | Validates folder name and re-scans all media in the folder; updates DB, console, and all panes |

### 7.3 Media Menu

Operates on the currently selected set of media files in the Media Table.

| Item | Action |
|---|---|
| Assign Metadata… | Opens the Assign Metadata dialog |
| Un-Group Media | Assigns each selected file to its own new single-page document; renumbers remaining pages in the original document |

### 7.4 Assign Metadata Dialog

Affects only the in-database attributes of the selected media files. File renaming is applied when the dialog is submitted.

**Layout:**

| Widget | Purpose |
|---|---|
| Label: "N files selected" | Informational |
| Drop-down: Document Type | Displays expanded name (e.g., `VE — Exterior View`); assigns the code (`VE`) |
| Text entry: CDASH Place ID | Integer Omeka Place Item ID |
| Static text: Place Name | Populated after Place ID is validated |
| Toggle: Ungroup Selection | Default off. When off, all selected files become pages of one document; when on, each file becomes its own single-page document |
| Submit / Cancel buttons | |

**Submit behavior:**
- Validate Place ID via `CDASHValidator`. If invalid, show error in console; do not rename files.
- Call `Digester.assign_media_to_doc()`.
- On failure, show error in console; files revert to their pre-dialog state.
- On success, refresh all panes.

---

## 8. CSV Output Files

Produced by `Digester.export_csv()` when `batch.ready = True`. Written to `Catalog/`.

### `batch.csv`

| Column | Source |
|---|---|
| `id` | `cdash_batch.id` |
| `batch_id` | `cdash_batch.batch_id` |
| `mnemonic_name` | `cdash_batch.name` |
| `batch_folder_path` | `cdash_batch.batch_folder_path` |
| `initialized_date` | `cdash_batch.initialized_date` |
| `status` | `"go"` if `ready`, else `"no-go"` |
| `qa_note` | `cdash_batch.note` |

### `folder.csv`
| Column | Source |
|---|---|
|`ResourceTemplate` | `"CDASH Folder"` |
|`ResourceClass` | `"bibo:Collection"` |
|`folder`        | `cdash_folder_name` |
|`itemSetID`     | `item_set_id` |



### `place.csv`

| Column | Source |
|---|---|
| `resourceTemplate` | constant `"CDASH Place"` |
| `resourceClass` | constant `"dcterms:Location"` |
| `identifier` | `place_item_id` |
| `placeItem` | `place_name` |
| `placeName` | `place_name` |
| `PlaceItemID` | `place_item_id` |
| `placeType` | `place_type` |
| `lat` | `lat` |
| `lon` | `lon` |
| `Folder` | `cdash_folder_name` |
| `ItemSetID` | `folder.item_set_id` |
| `houseNum` | `house_num` |
| `streetName` | `street_name` |
| `streetSort` | `street_sort` |
| `Neighborhood` | `neighborhood` (comma-delimited) |
| `chcDist` | `chc_dist` (comma-delimited) |

### `document.csv`

| Column | Source |
|---|---|
| `resourceTemplate` | constant `"CDASH Document"` |
| `resourceClass` | constant `"bibo:Document"` |
| `identifier` | `batch_doc_id` |
| `title` | `doc_title` |
| `Type` | `doc_type_description` |
| `bibliographicCitation` | constant `"Cambridge Historical Commission, Digital Architectural Survey and History."` |
| `rights` | constant `"Rights status not evaluated."` |
| `placeitem` | `place_name` |
| `placeItemID` | `place_item_id` |
| `Folder` | `cdash_folder_name` |
| `ItemSetID` | `item_set_id` |
| `numPages` | `num_pages` |
| `streetsort` | `street_sort` |
| `Neighborhood` | `neighborhood` |
| `chcDist` | `chc_dist` |
| `dateAccepted` | `date_accepted` |
| `lat` | `lat` |
| `lon` | `lon` |

### `media.csv`

| Column | Source |
|---|---|
| `ResourceTemplate` | constant `"CDASH Media"` |
| `Title` | `filename` |
| `identifier` | `batch_media_id` |
| `Relation` | `batch_doc_id` |
| `type` | `doc_type_code` |
| `Source` | `filepath` (relative, beginning with batch root) |
| `number` | `page_num` |
| `dateAccepted` | `capture_date` |

### `rejects.csv` (in `Rejects/`)

| Column | Source |
|---|---|
| `filename` | Current OS filename |
| `filepath` | Path relative to batch root |
| `file_size_mb` | |
| `pixel_width` | |
| `pixel_height` | |
| `color_mode` | e.g., `RGB`, `L`, `RGBA`, `CMYK` |
| `capture_date` | From EXIF; ISO 8601 |
| `status` | `False` |
| `qa_note` | Prescreener rejection reason |

---

## 9. Document Type Codes

Fixed list; stored as `DOC_TYPES` constant in `cdash_objects.py`.

```python
DOC_TYPES = {
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
```

---

## 10. Technical Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| GUI framework | PySide6 (Qt for Python) | Native Windows look; LGPL license |
| Database | SQLite via `sqlite3` | Embedded; no server required |
| Image handling | Pillow | JPEG/TIFF/PNG; EXIF extraction; thumbnails |
| PDF handling | `pymupdf` (`fitz`) | PDF/A-1b XMP check; PDF thumbnail rendering |
| REST API | `requests` | Omeka-S API calls in `CDASHValidator` |
| Packaging | PyInstaller + Inno Setup | PyInstaller bundles; Inno Setup wraps as `.exe` installer |

Development install:
```
pip install -e .
```

---

## 11. Known Limitations / Future Work

- **`item_set_ids` cross-reference check** — the API returns which Item Sets a Place belongs to (`o:item_set`). A future scan step should warn when the parent folder's `item_set_id` is not in the place's `item_set_ids`.
- **Commit Changes menu item** — consolidates page numbers across all documents in a folder (closing gaps from un-grouping) and renames files to match DB state. Specified but not yet implemented.
- **`media_rejects` repair tools** — restore a rejected file after fixing the format issue. Deferred.
- **macOS / Linux support** — architecture does not preclude this; path handling uses `pathlib.Path` throughout.
- **PyInstaller packaging** — not yet configured.
