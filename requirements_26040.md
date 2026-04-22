# CDASH Presort Digester - Requirements v4
*Revised 2026-04-02. Based on `requirements_pbc_260319_v3.md`.*

---

## Project Overview

This project produces a desktop tool that assists archivists in organizing image and PDF files from various sources into **Archival Information Packages (AIPs)** as defined by the OAIS Reference Model. Media files are associated with Folders and Places as referenced in the **Cambridge Digital Architectural Survey and History (CDASH)** and its Omeka-S online repository.

### Target Platform
- **Primary:** Windows 11 desktop application
- **Secondary (future):** macOS, Linux - architecture should not preclude this, but it is not a current requirement.

### Shareability
Code is intended to be maintained and shared with others. It will be structured as an installable Python package from the start. Eventually to be packaged as windows executable with pyInstaller.

---

## Application

CDASH is a web-based repository and access application based on Omeka-S. In the CDASH schema, media files represent pages of CDASH Document Items. Each document item is related to a single CDASH Place Item and a single CDASH Folder (an Omeka item set). The overall function of the CDASH digester is to assist the CDASH manager with organizing media files for bulk-accession to the online CDASH repository.

The CDASH manager receives image and PDF files (media files) from Historical Commission staff members for accession into CDASH. The workflow for preparing the media files involves using the desktop file explorer to assemble incoming media files into media folders that correspond with CDASH folders in the online repository.

The CDASH Pre-Sort Digester assists the manager in transforming an initial assembly of files into an archival information package that we call a CDASH Import Batch. In addition to media files, a CDASH batch that is ready for accession includes catalog information that represents the relationships inherent in the folder and file names and CSV files which serve to instruct the Omeka CSV Import tool in creating new CDASH Document Items that are children of existing places, then uploading the media files into Omeka.

The process of validating and renaming files may take more than one iteration of batch scan and interactive adjustment until all of the media files have been validated, in terms of format, and their names reflect correct relations with new and existing CDASH place items and folders. When this state is reached, the digester produces the CSV files that direct the process of creating, linking, and uploading to the CDASH repository. At that time, the batches are preserved in cold storage on our cloud provider.

## Object and Data Model

The application uses sqlite to model and persist the properties and hierarchical relationships among the batch and its media files as implicit documents and their associated CDASH folders and CDASH places. The following outlines the objects and properties as represented in the relational database along with notes regarding derivation.

## CDASH Objects Module: cdash_objects.py

The first stage of the digester process is to initialize the batch.sqlite database schema. Each of the object classes described below has its own table create statement and a set of methods as necessary for creating, updating, and removing their traces in the batch database. The object classes also hold methods that call out to validator and prescreener modules to validate names and formats for related operating system files and folders.

### batch_db
Handles database connection and executes queries and commits.

**Methods**
- Creates tables for object schema described below.
- Produces the four CSV output files.

### cdash_batch
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `batch_id` | TEXT | e.g., `CDB260319` |
| `name` | TEXT | Second token of file name |
| `batch_folder_path` | TEXT | Absolute OS path to batch root |
| `initialized_date` | TEXT | ISO 8601 date |
| `ready` | BOOL | |
| `note` | TEXT Comma Delimited | |

**Methods**
- Parse batch name.
- Validate batch name.
- Getters and setters as needed.
- Initialize batch folder and DB.
- Write CSV files.

#### `cdash_folder`
| Column | Type | Notes |
|---|---|---|
| `folder_number` | INTEGER PK | Sequential folder index within batch |
| `cdash_folder_name` | TEXT | Slugified Item Set name, e.g., `East_Cambridge` |
| `item_set_id` | Integer | References Omeka Item Set |
| `os_folder_name` | TEXT | Actual current OS folder name |
| `name_ready` | BOOL | Ready or Not Ready |
| `media_ready` | BOOL | Ready or Not Ready |
| `notes` | TEXT Comma Delimited | |

**Methods**
- Folder name parser and validator.
- Getters and setters as needed.
- OS folder renamer.
- Scan media in folder.

#### `cdash_place`
| Column | Type | Notes |
|---|---|---|
| `place_item_id` | INTEGER PK | Omeka Place Item ID |
| `place_name` | TEXT | |
| `place_type` | TEXT | |
| `house_num` | TEXT | |
| `street_name` | TEXT | |
| `street_sort` | TEXT | |
| `neighborhood` | TEXT | Comma-delimited |
| `chc_dist` | TEXT | Comma-delimited |
| `item_set_ids` | TEXT | Comma-delimited, from validator |
| `lat` | REAL | |
| `lon` | REAL | |
| `ready` | BOOL | |
| `notes` | TEXT | Comma-delimited |

**Methods**
- Validate place ID.
- Getters and setters as needed.

#### `cdash_doc`
| Column | Type | Notes |
|---|---|---|
| `doc_item_id` | INTEGER PK | |
| `place_item_id` | INTEGER FK -> places | |
| `folder_doc_sequence` | Integer | Distinguishes documents within folder |
| `item_set_id` | INTEGER FK -> folders | |
| `doc_title` | TEXT | `place_name` + " - " + `doc_type_description` |
| `doc_type_code` | TEXT | e.g., `VE` |
| `doc_type_description` | TEXT | e.g., `Exterior View` |
| `date_accepted` | TEXT | Earliest EXIF media capture date, from validator |
| `doc_identifier` | TEXT | Media filename omitting page number, place_id, and suffix |
| `num_pages` | INTEGER | |
| `ready` | BOOL | |
| `notes` | TEXT Comma Delimited | |

**Methods**
- Getters and setters as needed.
- File name evaluator.
- Renumber pages.

#### `cdash_media`
| Column | Type | Notes |
|---|---|---|
| `media_id` | INTEGER PK | |
| `doc_item_id` | INTEGER FK -> docs | |
| `item_set_id` | INTEGER FK -> folders | |
| `filename` | TEXT | Current OS file name |
| `filepath` | TEXT | Path including batch root |
| `page_num` | INTEGER | Page number within document (1-based) |
| `capture_date` | TEXT | From EXIF, ISO 8601 |
| `file_size_mb` | REAL | |
| `pixel_width` | INTEGER | |
| `pixel_height` | INTEGER | |
| `Format Note` | TEXT | e.g., `TIF-RGB`, `Grey8`, `PDF/a Variety` |
| `ready` | BOOL | |
| `notes` | TEXT Comma Delimited | |

**Methods**
- Parse and validate name.
- Screen media format.
- Getters and setters as needed.
- OS file renamer.

#### `media_rejects`
| Same schema as cdash_media |

**Methods**
- Getters and setters as needed.
- Repair and restore tools (future).
- Count rejected files.

### Batch Development and Initialization

The CDASH manager takes media files submitted by colleagues and places them in a folder structure on her desktop file-system. The names of media folders reference folders in CDASH folders that exist as Omeka item sets in the online collection.

- F[folder_index]-[mnemonic folder name]-OF[CDASH Folder ID]
- Example: F1-725_Mass_Av-City_Hall-OF8989898

Before the batch is initialized, media folder names are not required to begin with F[folder index]. The digester will add these and make sure that no folder index is used more than once in the batch.

Media folders contain media files. The only restriction on media file names is suffixes: tif, pdf, or jpg. A primary function of the CDASH digester is to assist with changing these file names to create and validate the connection of each media file with a new CDASH document, an existing CDASH place item, and CDASH folder. A media file that is ready for accession would have a name in the following form:

- `[PlaceName]_[DocIndex]p[PageIndex]-[DocType]-OP[CDASHPlaceItemID].[sfx]`
- Example: `12_Reservoir_St_0017p0001-VE-OP196223.pdf`

During the pre-sort digester process, this application assists the manager in validating and updating references between folder and file names with the existing contextual fabric of CDASH. The digester also handles distinct document and page index tokens and document type.

## CDASH Digester

The CDASH digester module is the controller that sets up and validates the batch and launches the GUI. Functions in the digester make use of classes defined in cdash_objects to carry out functions initiated by GUI menus.

### Batch Initializer (method of cdash_batch)

When a batch directory is chosen from Batch -> Open Batch, the following sequence of events takes place:

1. Check batch name. It must be in the form CDBYYMMDD. The date does not have to be the current day.
2. Check that there is a Media folder.
3. If 1 and 2 are not satisfied, errors are printed to the console and the folder will not open.
4. If the batch has no Catalog folder, create one. If catalog folder and log file exist, append a timestamp to the tail of the log file.
5. If a batch.sqlite database already exists, delete it.

### Batch Folder Scanner (method of cdash_batch)

For each media folder in batch, use methods of cdash_folder to do the following:

6. Check media folder names:
   - Does the CDASH folder ID match an existing CDASH folder?
   - If yes, change the media folder name, if necessary, to a slugified version of the CDASH folder name.
   - Add a unique folder index to folder name, if necessary.
7. Insert folder properties and name_status in the cdash_folder table.

### Scan Media (method of cdash_folder)

For each media file in folder (sorted alphabetically), use methods of cdash_media to:

1. Check media format. If format is not valid, move file to rejects folder, write message to console and log, and create entry in media_rejects table.
2. Validate file name. If the file name follows the valid pattern:
   - Check place name reference (method of place class).
   - If cdash_place exists and has not already been recorded in this batch:
     - Create entry in places table.
     - [future feature] Place validator will provide list of item sets (folders) referenced by place item. If this list does not include folder ID of parent media folder, emit red console error. Any media file referencing this place is Not Ready.
   - Document pagination:
     - If a new document is encountered, set page number to 1. If continuing document, increment page number.
     - If doc_index has already been used in this folder with a different mnemonic name, log error and mark file invalid, then skip rest of file-fixing procedure.
   - Media file renaming:
     - For files with valid document, page, and place references, rename and register.
     - Replace mnemonic portion of file name with slugified version of CDASH PlaceItem name as returned by place validator.
     - Register media file with status in Media_File table.
     - Register document with properties in Doc_Item table.
   - After all media files in folder are read, if all media files in folder are Ready, set folder media status to Ready; otherwise set to false.

- Finish batch: At end of batch scan, count number of rejected media files and record this in cdash_batch table.

There should now be entries in the database and in-memory representations for each media file, media folder, cdash_document, cdash_place, and cdash_batch as a whole reflecting status of each. For any file with Not Ready status, Notes field will have comma-delimited list of issues. Log file records errors, warnings, and any time a file or folder was renamed. Batch table includes count of files that remain in Rejects folder.

The digester module should have a __main__ class that, when invoked from command line, makes a copy of CDB260320-Test_batch, scans it, and populates the batch database. The GUI is not invoked in this case. GUI should be able to run on folder and file properties recorded in batch DB.

## Digester Phase 2: Interactive Media Tagging

After batch scan, some media folders and media files may have Not Ready status. Some of these files may have non-conforming names or a broken reference to required cdash_folder or cdash_place. In phase 2 of CDASH Digester process, the digester offers a GUI to assist with fixing folder and file names.

The digester GUI displays folders and their media files as thumbnails and in a metadata table. Files and folders that are not ready for integration with the online CDASH repository are highlighted, and user can select one or more media files to apply metadata identifying intended CDASH place, or grouping of media files as new document items of a particular document type.

## Graphical Interface

Built with **PySide6**. Main window uses `QSplitter` to create resizable panes.

### Pane Layout

```
┌─────────────────────────────────────────────────────────┐
│  Menu Bar                                               │
├──────────────┬──────────────────────────────────────────┤
│              │  Media Table Pane (top-right)            │
│ Folder Pane  │  QTableView - rows = media files in      │
│ (left)       │  selected folder                         │
│ QTreeView    ├──────────────────────────────────────────┤
│              │  Thumbnail Pane (bottom-right)           │
│ green = go   │  Scrollable grid of image thumbnails     │
│ red = no-go  │  Thumbnails generated by Pillow          │
│              │  Failed renders show blank rectangle     │
└──────────────┴──────────────────────────────────────────┘
|  Console Window - QDockWidget, detachable               |
└─────────────────────────────────────────────────────────┘
```

### Interaction Behavior
- Initially, all panes and menu items are empty or grayed out until user chooses valid batch folder.
- Selecting valid batch folder that has media folders causes folder pane to show list of folders.
- Folder names in folder pane have 50% red background if status is Not Ready.
- Selecting folder in folder pane populates both media table and thumbnail pane with folder files.
- Media table shows attributes from cdash_media table for each media file in currently selected folder, sorted alphabetically by file name. First three column names are File Name, Status, Note. Rows for files with Not Ready status have 50% red background.
- Thumbnail outline color reflects file status: thumbnails for Ready files have border color that alternates green and orange by document; thumbnails for Not Ready files have 50% red border color. Thumbnails fit within 200px height and width.
- Selecting rows in media table highlights corresponding thumbnails in thumbnail pane, and vice versa. Selection is synchronized bidirectionally.
- Selected set of media file records becomes subject for metadata manipulation options invoked from menus.

### Console Window
- Implemented as a `QDockWidget` containing a read-only `QTextEdit`.
- Can be floated and docked.
- Displays log messages from Digester module.
- Color-coded: errors in red, warnings in orange.

Console log output is appended to `Catalog/batch.log`.

---

## Menus

Operations initiated by menus are implemented using methods on cdash_object classes. They affect in-memory objects and batch database. On completion of these operations, folder, media table, and thumbnail panes are refreshed.

### Batch Menu
| Item | Action |
|---|---|
| Choose Batch Folder | Presents folder picker, runs initialization and batch scan on selected batch folder |
| Produce CSV Files | Exports `batch.csv`, `place.csv`, `document.csv`, `media.csv` to `Catalog/`. If batch status is Not Ready, this function is grayed out |
| Commit Changes | For each folder: page numbers for all documents are consolidated (closing gaps caused by splitting documents). Renames files based on properties in database, commits changes to batch DB, rescans folder and batch, and refreshes status |

### Folder Menu
Operates on single media folder selected in folder panel.

| Item | Action |
|---|---|
| Scan Selected Folder | Validates folder name, file names and references, writes result to console, updates thumbnail and media table panes, updates folder status, renames files as needed based on accumulated properties in database |
| Commit Changes | Consolidates document page numbers, renames files based on database properties, commits changes to batch DB, then rescans folder and batch |

### Media Menu
Operates on currently selected set of media files in media table.

| Item | Action |
|---|---|
| Assign Metadata | Opens dialog to validate place ID, pick document type, and determine whether selected files form single multi-page document or multiple single-page documents |
| Un-Group Media | If selected media files are associated with multi-page document, they are assigned to a new unique document ID |

### Assign Metadata Dialog

This dialog affects only attributes of selected media files. Submit action has no effect on file names.

Submit action (a function in digester module):
- Operates on selected set of media.
- When multiple files are selected:
  - If more than one file is selected and only one value of place_item_id or doc_type_code exists among selected files, that value becomes default in CDASH Place ID or Doc Type entry box.
  - Ungroup toggle causes Submit to assign each selected page to its own individual document. Default is off.
  - If ungroup is off (default), Submit sets all selected pages to a new document.
  - If only one document is selected, behavior applies to that single selection.

#### Layout
**Informative text:**
- Number selected

Drop-down Document Type picker displays expanded name but assigns associated code.

Text entry box: CDASH Place ID
Static text box: Place Name

Option: Ungroup selection
Submit button

---

## Technical Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Existing codebase; strong ecosystem for required tasks |
| GUI framework | PySide6 (Qt for Python) | Native Windows look, powerful multi-pane layout, LGPL license |
| Database | SQLite via `sqlite3` | Embedded, no server required, sufficient for batch scale |
| Image handling | Pillow | Pure-Python, handles JPEG/TIFF/PNG EXIF and thumbnails |
| PDF handling | `pymupdf` | |
| REST API | `requests` | |
| Packaging | PyInstaller + Inno Setup | PyInstaller bundles app; Inno Setup wraps it into `.exe` installer |

---

## Project Package Structure

```
cdash_digester/
├── pyproject.toml                  <- modern packaging; replaces setup.py
├── README.md
├── requirements_260329_v5_pbc.md   <- source requirements document
├── src/
│   └── cdash_digester/
│       ├── __init__.py
│       ├── validator.py            <- CDASH Validator Module
│       ├── digester.py             <- Digester Module
│       ├── cdash_objects.py        <- CDASH Object/Data Model
│       ├── prescreener.py          <- Media Prescreener Module
│       └── gui/
│           ├── __init__.py
│           ├── main_window.py      <- main PySide6 window and splitter layout
│           ├── folder_pane.py      <- left folder tree
│           ├── media_table.py      <- top-right table view
│           ├── thumbnail_pane.py   <- bottom-right thumbnail grid
│           └── console_window.py   <- detachable console log
└── tests/
    └── ...
```

Development install: `pip install -e .`
Distribution: PyInstaller produces a bundle; Inno Setup wraps it into an `.exe` installer.

---

## Batch Folder Structure

### Before Initialization

The archivist creates this folder structure manually before running the tool.

```
'CDB'[YYMMDD][a-z]?-[batch_name]      <- trailing letter optional if more than one batch in a day
  'Media'/
    <folder_name>-OF<OmekaItemSetID>/ <- one folder per CDASH item set
      <media_file_name>.<suffix>      <- media files named to sort in page order
```

### After Initialization

If batch, folder, or media file names are already following post-initialized pattern, it is not necessary to change names when re-scanning.

```
CDB<YYMMDD>(?:[a-z])-<batch_name>/
  Media/
    (F<folder_index>)-(<Omeka_Item_Set_Slug>)(-OF<OmekaItemSetID>)/
      <omeka_place_name_slug>_((<doc_index>)p(<page_index>))(-<doc_type_code>)(-OP<cdash_place_ID>).(<suffix>)
  Catalog/
    batch_db.sqlite
    batch.csv
    place.csv
    document.csv
    media.csv
    batch.log
  Rejects/
    rejects.csv
    <Item Set Folder(s)>/
      <rejected media files>
```

#### Preliminary vs Ready Names

Because initial batch organization is hand-rolled, naming requirements for folders and files are loose. Folders and files can be dropped into a batch folder at any time with rudimentary names.

- Media folder names may omit folder_index. It will be assigned.
- Media file names may be as simple as [file_stem].sfx where sfx is in validator list.

The batch.folder_scan method validates and renames media folders with folder_index and replaces original folder name with slugified cdash_folder_name.

A media folder name that is ready can be parsed into tokens:
(F<folder_index>)-(<Omeka_Item_Set_Name>)(-OF<OmekaItemSetID>)

The slugify function replaces spaces with underscores and omits all other non-alphanumeric characters, except `-`.

A media folder with OmekaItemSetID token that fails validation has status = Not Ready.

#### Pre-initialization Media File Name

At any time during batch lifecycle, manager might drop new files into media folder. These may have rudimentary names, e.g., (<mediafile_stem>).(<sfx>). If mediafile_stem includes token like (-<doc_type_id>) or (-OP<cdash_place_id>), these may be read as hints by interactive file renamer.

Example: `12_Prospect_St-OP45678.jpg` can have cdash_place_id validated and associated place item properties registered in database by batch scanner, even though the filename and media file itself have Not Ready status until associated with document, etc.

## Batch Processing Modules

### Batch Initializer
- Initialize batch DB.
- Scan, validate, and fix folder names.
- Pre-screen and validate media, initializing database records.
- Produce CSV files (after validating batch).
- Emit progress and error messages to console window.

### CDASH Validator Module (`validator.py`)
- Uses Omeka-S REST API to validate Place and Item Set resource IDs.
- Writes validation results (title or error) into appropriate DB table.
- Must degrade gracefully when API is unreachable (network offline); cached DB results may be used for previously validated IDs.

If place or folder ID was previously validated and cached in DB, tool can accept cached result as valid.

### Media Prescreener Module (`prescreener.py`)

Checks media file properties and rejects files that do not conform. Uses **Pillow** for image files.

**Accepted formats:**
- TIFF: LZW compression, 24-bit RGB, or 8-bit grayscale
- JPEG: 24-bit RGB
- PDF: verified conformance level PDF/A-1b
- 16-bit TIFFs are rejected

**Rejection criteria:**
- File size > 100 MB
- Pixel dimensions: width x height > 108 megapixels
- Wrong color mode (e.g., CMYK, Palette)
- Corrupt or unreadable file

**On rejection:**
- File is moved to `Rejects/<ItemSetFolderName>/`
- Entry is written to `rejects.csv`
- `go_nogo` status of parent folder and batch is set to `no-go`

---

## CSV Output Files

Produced by database module when batch status is `go`. Written to `Catalog/`.

### `batch.csv`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `batch_id` | TEXT | e.g., `CDB260319` |
| `mnemonic_name` | TEXT | User-chosen batch folder name |
| `batch_folder_path` | TEXT | Absolute OS path to batch root |
| `initialized_date` | TEXT | ISO 8601 date |
| `go_nogo` | TEXT | Overall batch status |
| `qa_note` | TEXT | |

### `media.csv`
| Column | Source |
|---|---|
| `ResourceTemplate` | constant |
| `Title` | `filename` |
| `identifier` | `batch_id` + `-` + `filename` |
| `Relation` | `doc_id` |
| `type` | `doc_type_code` |
| `Source` | `Path to file, beginning with Batch folder` |
| `number` | `page_num` |
| `dateAccepted` | `exif_date_created` |

### `media_rejects.csv`
| Column | Source |
|---|---|
| `filename` | Current OS file name |
| `filepath` | Path relative to batch root |
| `page_num` | Page number within document (1-based) |
| `capture_date` | From EXIF, ISO 8601 |
| `file_size_mb` | |
| `pixel_width` | |
| `pixel_height` | |
| `color_mode` | e.g., `RGB`, `L`, `CMYK` |
| `status` | |
| `note` | Prescreener or validator message |

### `document.csv`
| Column | Source |
|---|---|
| `resourceTemplate` | constant `CDASH Document` |
| `resourceClass` | constant `bibo:Document` |
| `identifier` | `batch_id` + `-` + `batch_doc_num` + `doc_type_code` |
| `title` | `doc_title` |
| `Type` | `doc_type_description` |
| `bibliographicCitation` | constant `Cambridge Historical Commission, Digital Architectural Survey and History.` |
| `rights` | constant `Rights status not evaluated.` |
| `placeitem` | `place_name` |
| `placeItemID` | `place_id` (foreign key) |
| `Folder` | `cdash_folder_name` |
| `ItemSetID` | `ItemSetID` |
| `numPages` | `num_pages` |
| `streetsort` | `street_sort` |
| `Neighborhood` | `neighborhood` |
| `chcDist` | `chc_dist` |
| `dateAccepted` | `date_accepted` |
| `lat` | `lat` |
| `lon` | `lon` |

### `place.csv`
| Column | Source |
|---|---|
| `resourceTemplate` | constant "CDASH Place" |
| `resourceClass` | constant "dcterms:Location" |
| `identifier` | `place_id` |
| `placeItem` | `place_name` |
| `placeName` | `place_name` |
| `PlaceItemID` | `PlaceItemId` (primary key) |
| `placeType` | `place_type` |
| `lat` | `lat` |
| `lon` | `lon` |
| `houseNum` | `house_num` |
| `streetName` | `street_name` |
| `streetSort` | `street_sort` |
| `Neighborhood` | `neighborhood` (comma-delimited) |
| `chcDist` | `chc_dist` (comma-delimited) |

---

## Document Type Codes

This is a fixed list saved as a configurable variable in the project.

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
