# CDASH Presort Digester — Requirements v4
*Revised 2026-04-02. Based on `requirements_pbc_260319_v3.md`.*

---

## Project Overview

This project produces a desktop tool that assists archivists in organizing image and PDF files from various sources into **Archival Information Packages (AIPs)** as defined by the OAIS Reference Model. Media files are associated with Folders and Places as referenced in the **Cambridge Digital Architectural Survey and History (CDASH)** and its Omeka-S online repository.

### Target Platform
- **Primary:** Windows 11 desktop application
- **Secondary (future):** macOS, Linux — architecture should not preclude this, but it is not a current requirement.

### Shareability
Code is intended to be maintained and shared with others. It will be structured as an installable Python package from the start.  Eventually to be packaged as windows executable with pyInstaller

---
## Application 
CDASH is a web-based repository and access application based on Omeka-S.  In the CDASH Schema, media files represent pages of CDASH Document Items.  Each document item is related to a single CDASH Place Item and a single CDASH Folder (an Omeka item set). The overall function of the CDASH digester is to assist the CDASH manager with organizing media files for bulk-accession to the on-line CDASH repository. 

The CDASH manager receives image and PDF files (media files) from Historical Commission staff members for accession into CDASH.  The workflow for preparing the media files involves using the desktop file explorer to assemble incoming media files into media folders that correspond with CDASH Folders in the on-line repository. 

The CDASH Pre-Sort Digestor assists the manager in transforming an initial assembly of files into an archival information package that we call a CDASH Import Batch.  In addition to media files, a CDASH Batch that is Ready for accession includes catalog information that represents the relationships inherent in the folder and file names and CSV files which serve to instruct the Omeka CSV Import tool in creating new CDASH Document Items that are children of existing places; then uploading the media files into Omeka. 

The process of validating and renaming files may take more than one iteration of batch scan and interactive adjustment until all of the media files have been validated, in terms of format, and their names reflect correct relations with new and existing cdash place items and folders. When this state is reached, then the digester produces the CSV files that direct the process of creating, linking and uploading to the CDASH repository.   At that time, the batches are preserved in cold storage on our cloud provider. 

## Object and Data Model 

The application uses sqlite to model and persist the properties and hierarchal relationships among the Batch and its Media Files as implicit Documents and their associated CDASH Folders, CDASH Places.  The following will outline the objects and properties as represented in the relational database along with notes regarding the derivation.  

## CDASH Objects Module cdash_objects.py

THe first stage of the Digester process is to initialize the batch.sqlite database schema. Each of the object classes described below has its own table create statement and a set of methods as necessary for creating updating and removing their traces in the batch database.  The object classes also hold the methods that call out to our validator and prescreener modules to validate names and formats the related operating system files and folders  

### `batch_db`
Handles database connection and executes queries & commits.

**Methods**
* Creates tables for object schema described below. 
* Produces the four CSV output files

### `cdash_batch`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `batch_id` | TEXT | e.g., `CDB260319` |
| `name` | TEXT | Second token of file name | 
| `batch_folder_path` | TEXT | Absolute OS path to batch root |
| `initialized_date` | TEXT | ISO 8601 date |
| `ready` | BOOL | | |
| `note` | TEXT Comma Delimited | |


**Methods**
* Parse batch name,
* validate batch name 
* getters & setters as needed, 
* Initialize Batch folder and DB, 
* Writer CSV FIles


### `cdash_folder`
| Column | Type | Notes |
|---|---|---|
| `folder_number` | INTEGER PK | Sequential folder index within batch |
| `identifier` | TEXT | Batch_id + F + folder_number e.g. CDB260605F2
| `cdash_folder_name` | TEXT | Slugified Item Set name, e.g., `East_Cambridge` |
| `item_set_id` | Integer | References Omeka Item Set | 
| `os_folder_name` | TEXT | Actual current OS folder name |
| `name_ready` | BOOL | Ready or Not Ready | 
| `media_ready`| BOOL | Ready or Not Ready |
| `notes` | TEXT Comma Delimited | |

**Methods**
* folder name parser and validator, 
* getters & setters as needed,  
* OS folder renamer
* scan media in folder

### `cdash_place`
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
| `item_set_ids` | TEXT | Comma-delimited | from validator| 
| `lat` | REAL | |
| `lon` | REAL | |
| `ready` | BOOL | | |
| `notes` | TEXT | Comma-delimited |

**Methods**
* validate place ID, 
* getters & setters as needed

### `cdash_doc`
| Column | Type | Notes |
|---|---|---|
| `doc_item_id` | INTEGER PK | |
| `place_item_id` | INTEGER FK → places | |
| `folder_doc_sequence` | Integer | distinguishes documents within folder.
| `item_set_id` | INTEGER FK → folders | |
| `doc_title` | TEXT | `place_name` + " - " + `doc_type_description` |
| `doc_type_code` | TEXT | e.g., `VE` |
| `doc_type_description` | TEXT | e.g., `Exterior View` |
| `date_accepted` | TEXT | Earliest EXIF media capture date | From validator|
| `doc_identifier` | TEXT | folder.identifier - place_name_slug -d doc_index - doc_type_code 
| `num_pages` | INTEGER | |
| `ready` | BOOL | | |
| `notes` | TEXT Comma Delimited | |

**Methods**
* getters & setters as needed, 
* file name evaluator, 
* renumber pages

### `cdash_media`
| Column | Type | Notes |
|---|---|---|
| `media_id` | INTEGER PK | |
| `doc_item_id` | INTEGER FK → docs | |
| `item_set_id` | INTEGER FK → folders | |
| `filename` | TEXT | Current OS file name |
| `filepath` | TEXT | Path including batch root |
| `page_num` | INTEGER | Page number within document (1-based) |
| `capture_date` | TEXT | From EXIF; ISO 8601 |
| `file_size_mb` | REAL | |
| `pixel_width` | INTEGER | |
| `pixel_height` | INTEGER | |
| `Format Note` | TEXT | e.g., `TIF-RGB`, `Grey8`, `PDF/a Variety` |
| `ready` | BOOL | | |
| `notes` | TEXT Comma Delimited | |

**Methods**
* parse and validate name, 
* screen media format, 
* getters & setters as needed, 
* OS file renamer

### `media_rejects`
| Same schema as cdash_media

**Methods**

* getters & setters as needed, 
* repair & restore tools (future) 
* count rejected files
---


### Batch Development and Initialization
The CDASH Manager takes media files submitted by colleagues and places them in a folder structure on her desktop file-system.   The names of Media Folders reference folders in the CDASH Folders that exist as Omeka item sets in the on-line collection.   

* F[folder_index]-[mnemonic folder name]-OF[CDASH Folder ID]
* example:  F1-725_Mass_Av-City_Hall-OF8989898

Before the batch is initialized, media folders names are not required to begin with a F[folder index].  The digester will add these and make sure that no folder index is used more than once in the batch.  

Media folders contain media files. the only restriction on media file names is in the suffixes: tif, pdf or jpg.  A primary function of the CDASH digester is to assist with changing the names of these files to create and validate the connection of each media file with a new CDASH Document, an existing CDASH place item, and CDASH folder. A media file that is Ready for accession would have a name with the following form:


* `[PlaceName]_[DocIndex]p[PageIndex]-[DocType]-OP[CDASHPlaceItemID].[sfx]`
* Example: `12_Reservoir_St_0017p0001-VE-OP196223.pdf`

During the Pre-Sort Digester process this application will assist the manager to validate and update the references between the folder and file names with the existing contextual fabric of CDASH.  The Digester also handles the distinct Document and Page Index tokens number and doc type.   

# CDASH Digester
The CDASH digester module is the controller that sets up and validates the batch, launches the gui.  Functions in the digester make use of classes defined in the cdash_objects module to carry out functions initiated by the gui menus. 


### Batch Initializer (method of cdash_batch)

When a batch directory is chosen from the Batch->Open Batch menu item, following sequence of events takes place. 


1. Check the Batch Name.  It must be in the form CDBYYMMDD.  The date does not have to be the current day.  
2. Check that there is a Media folder.  
3. If 1. and 2. are not satisfied, errors are printed to the console and the folder won't open.
4. If the batch has no Catalog folder, one is created.   If the catalog folder and a log file exists, append a time-stamp to the tail of the log file. 
5. If a batch.sqlite database already exists, delete it. 


### Batch Folder Scanner (method of cdash_batch)

For each Media Folder in batch: use methods of cdash_folder to do the followng

6. The media folder names are checked.  
  -- Does the CDASH Folder ID match an existing CDASH Folder?
  -- if yes, change the name of the media folder, if necessary to a sluggifed version of the CDASH Folder Name. 
  -- Add a unique folder index to the folder name, if necessary.
7. The folder properties and name_status are inserted in the cdash_folder table 

### Scan media (method of cdash folder)

For each Media File in folder:  (sorted alphabetically) use methods of cdash_media to 

1. Check media format.  If format is not valid, move file to rejects folder write message to console and log, and create an entry in the media rejects table. (method of media item)
2. Validate file Name If the file name follows the valid pattern:
  * Check the place name reference  (method of place class)
  * if the cdash_place exists and has not already been recorded in this batch:    
      * create an entry in the places table.  (method of cdash_place class)
      * [future feature] The place validator will provide a list of item sets (folders) referenced by the place item.  If this list of folder IDs does not include the folder ID of the parent media folder, make a red error in the console log. Any media file referencing this place is Not Ready.    
  
  * Document Pagination
    * if a new document is encountered, set page number to 1 if continuing with a document, increment page number.
    * if the doc_index has already been used in this folder with a mnemonic name that is different.  Log an error and the file's status is invalid and skip the rest of this file-fixing procedure.
    
  * Media file renaming
  * For files that have valid document, page and place references, rename and register:   
    * replace the mnemonic portion of the file name with a sluggified version of the CDASH PlaceItem's name as returned by the placeitem validator. 
    * Register media file with status in the Media_File table.
    * Register the document with its properties in the Doc_Item table. 
  * After all media files in a folder have been read, if all of the media files in the folder have had a Ready status, set folder media status to Ready.  If not set to Ready to false.

* Finish Batch
 At the end of the batch scan: count the number of rejected media files and record this in the cdash_batch table. 

There should now be entries in the database and in-memory representations for each media file, media_folders, cdash_document, cdash_place and the cdash_batch as a whole reflecting the status of each.  FOr any file with a Not Ready Status the Notes field will have comma-delimited list of issues.    The log file records any errors, warnings and any time a file or folder was renamed. The batch table includes a count of the files that remain in the Rejected folder. 

The digester module should have a __main__ class that when invoked from the command line will make a copy of CDB260320-Test_batch, scan it and make and populate the batch database.  THe gui is not invoked in this case.  The GUI should be able to run on just the recorded folder and file properties as recorded in the batch.db. 

## Digester Phase 2: Interactive Media Tagging 

After the batch scan, some media folders and media files may have a Not Ready status. Some of these files may have non-conforming names, or a broken reference to a required cdash_folder or cdash_place. In phase 2 of the CDASH Digester process the digester offers a GUI to the manager to assist with fixing folder and file names. 

The digester GUI displays folders, and their media files as thumbnails and also in a metadata table. Files and folders that are not ready for integration with the on-line CDASH repository are highlighted, and the user can select one or more media files to apply metadata identifying the intended CDASH Place, or the grouping or media files as new Document Items of a particular document type. 

## Graphical Interface

Built with **PySide6**. The main window uses `QSplitter` to create resizable panes.

### Pane Layout

```
┌─────────────────────────────────────────────────────────┐
│  Menu Bar                                               │
├──────────────┬──────────────────────────────────────────┤
│              │  Media Table Pane (top-right)            │
│ Folder Pane  │  QTableView — rows = media files in      │
│ (left)       │  selected folder                        │
│ QTreeView    ├──────────────────────────────────────────┤
│              │  Thumbnail Pane (bottom-right)           │
│ green = go   │  Scrollable grid of image thumbnails.    │
│ red = no-go  │  Thumbnails generated by Pillow.         │
│              │  Failed renders show blank rectangle.    │
└──────────────┴──────────────────────────────────────────┘
|  Console Window — QDockWidget, detachable               |
└─────────────────────────────────────────────────────────┘  
```


### Interaction Behavior
- Initially, all of the panes and menu items are empty or grayed out until
the user chooses a valid batch folder.  
- selecting a valid batch folder that has media folders, causes the folder pane to show a list of folders.  
- folder names in the folder pane have a 50% red background if their status is Not Ready.   
- Selecting a folder in the folder pane populates both the Media Table and Thumbnail Pane with that folder's files.
- The Media table shows attributes from the cdash_media table for each media file in the currently selected folder these are sorted alphabetically by file name.  The first three column names are FIle Name, Status, Note.  Table rows for files having a not ready status have a 50% red background.    
- Thumbnail outline color reflects file status: Thumbnails for files with Ready status have a border color that alternates green and orange by document, thumbnail for files having Not Ready status have a 50% red border color.  Thumbnails fit within 200px height and width.
- Selecting rows in the Media Table highlights the corresponding thumbnails in the Thumbnail Pane, and vice versa. Selection is synchronized bidirectionally.
- The selected set of media file records becomes the subject for the various metadata manipulation options that may ve invoked from the menus. 

### Console Window

- Implemented as a `QDockWidget` containing a `QTextEdit` (read-only).
- Can be floated and docked.
- Displays log messages from the Digester Module.
- Color-coded: errors in red, warnings in orange.

Console log output be appended to file `Catalog/batch.log`

---

## Menus
Operations initiated by menus are implemented using methods on the cdash_object class.  They affect the in-memory objects and the batch database.  On completion of any of these operations, the contents of each of the Folder, Media Table and Thumbnail panes are refreshed. 

### Batch Menu
| Item | Action |
|---|---|
| Choose Batch Folder | Presents folder picker, runs initialization and batch-scan on selected batch folder |
| Produce CSV Files | Exports `batch.csv`, `place.csv`, `document.csv`, `media.csv` to `Catalog/`. If the Batch status is Not Ready, the Produce CSV FIles function is grayed out.
| Commit Changes | For each folder: Page numbers for all documents in the folder are consolidated (closing any gaps that may have been caused by splitting documents.) Renames files based on properties in the database, Commits the changes to the batchDB, rescans the folder and the batch.  All of this should refresh the status for everything,

### Folder Menu
* Operates on single media folder selected in Folder Panel
| Item | Action |
|---|---|
| Scan Selected Folder | Validates folder name, file names and references. writes result to Console, updates thumbnail and media table panes, updates folder status, renames files as needed based on accumulated new properties database |
| Commit Changes | Page numbers for all documents in the folder are consolidated (closing any gaps that may have been caused by splitting documents.) Renames files based on properties in the database, Commits the changes to the batchDB, rescans the folder and the batch.


### Media Menu
- Operates on the currently selected set of media files in the Media Table.
| Item | Action |
|---|---|
| Assign Metadata | Opens a dialog to: validate a Place ID, pick a Document Type, and determine whether selected files form a single multi-page document or multiple single-page documents |
| Un-Group Media | If selected media file(s) are associated with a multi-page document, they are assigned to a new unique document ID.  


### Assign Metadata Dialog 
This dialog affects only the attributes of the selected media files. The submit action has no effect on the names of the files.  

submit action: (a function in the digester module) 

- Operates on the currently selected set of media 
When multiple files are selected 
 - if more than one file is selected and only one value of place_item_id or doc_type_code is extant among all of the selected files, this value becomes the default value in the CDASH Place ID or Doc Type text entry box. 
 - Ungroup Toggle causes the submit button to assign each of the selected pages to their own individual documents.  Default is off.
 - If ungroup option is off (the default) Submit Button sets all of the selected pages to a new document  
 - If only one document is selected 

#### Layout
**Informative text:** 
* Number Selected

Drop-Down Document Type Picker, displays expanded name, but assigns the associated code. 
 - Defaults to 

Text Entry Box: CDASH Place ID            StaticText Box: Place Name 
 - 

Option: Ungroup Selection.                Submit Button

---

## Technical Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Existing codebase; strong ecosystem for all required tasks |
| GUI framework | PySide6 (Qt for Python) | Native Windows look, powerful multi-pane layout, LGPL license |
| Database | SQLite via `sqlite3` | Embedded, no server required, sufficient for batch scale |
| Image handling | Pillow | Pure-Python, handles JPEG/TIFF/PNG EXIF and thumbnails |
| PDF handling | `pymupdf` | 
| REST API | `requests` |  |
| Packaging | PyInstaller + Inno Setup | PyInstaller bundles the app; Inno Setup wraps it into a Windows installer |

---

## Project Package Structure

```
cdash_digester/
├── pyproject.toml                  ← modern packaging; replaces setup.py
├── README.md
├── requirements_260329_v5_pbc.md   ← this document
├── src/
│   └── cdash_digester/
│       ├── __init__.py
│       ├── validator.py            ← CDASH Validator Module
│       ├── digester.py             ← Digester Module
│       ├── cdash_objects.py        ← CDASH Object/Data Model
│       ├── prescreener.py          ← Media Prescreener Module
│       └── gui/
│           ├── __init__.py
│           ├── main_window.py      ← main PySide6 window and splitter layout
│           ├── folder_pane.py      ← left folder tree
│           ├── media_table.py      ← top-right table view
│           ├── thumbnail_pane.py   ← bottom-right thumbnail grid
│           └── console_window.py   ← detachable console log
└── tests/
    └── ...
```

Development install: `pip install -e .`
Distribution: PyInstaller produces a bundle; Inno Setup wraps it into a `.exe` installer.

---

## 4. Batch Folder Structure

### 4.1 Before Initialization

The archivist creates this folder structure manually before running the tool.

```
'CDB'[YYMMDD][a-z]?-[batch_name]     ← The trailing letter is optional in case there is more than one batch in a day.
  'Media'/
    <folder_name>-OF<OmekaItemSetID>/  ← one folder per CDASH Item Set
      <media_file_name>.<suffix>        ← media files, named to sort in page order.
```


### 4.2 After Initialization
If batch, folder or media file names are already following the post-initialized pattern it is not necessary to change the names when re-scanning

```
CDB<YYMMDD>(?:[a-z])-<batch_name> /        ← Batch ID prepended; letter suffix if > 1 batch/day
  Media/
    (F<folder_index>)-(<Omeka_Item_Set_Slug>)(-OF<OmekaItemSetID>)/   ← folders renamed with sequential index N
      <omeka_place_name_slug>_((<doc_index>)p(<page_index>))(-<doc_type_code>)(-OP<cdash_place_ID>).(<suffix>) ← media renamed with full CDASH identifiers
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

#### Preliminary VS Ready Names 
Because the initial batch organization is hand-rolled, the requirements for folder and file names is loose.  Folders and files can be dropped into a batch folder at any time, with rudimentary file names.   

-- Media folder names may omit the folder_index.  It will be assigned.
-- Media file names may be as simple as a simple [file_stem].sfx where .sfx is in the validator's list. 

The batch.folder_scan method takes care of validating and renaming media folders with a folder_index and replacing the original foldername with the sluggified rendition of the cdash_folder_name.  

A media folder name that is ready can be parsed into the following tokens:
(F<folder_index>)-(<Omeka_Item_Set_Name>)(-OF<OmekaItemSetID>)

the sluggify function replaces spaces with underscores and omits all other non alphanumeric characters, except "-".

A media folder that has an OmekaItemSetID token that fails to validate will have status = Not Ready. 

#### Pre-initialization Media File name:
At any time during the lifecycle of a batch, the manager might drop new files into a media folder.  These files may have rudimentary names e.g. (<mediafile_stem>).(<sfx>).  if the mediafile_stem includes a token like (-<doc_type_id>) or (-OP<cdash_place_id>) these may be read as hints by the interactive file renamer discussed later.

Example: `12_Prospect_St-OP45678.jpg` can have its cdash_place_id validated and associated place_item properties registered in the database by the batch scanner, even though the filename and therefore, the media file itself will have an Not Ready status until it has been associated with a document, etc. 


### 5.2 Batch Initializer
- Initialize batch DB
- scan, validate & fix foldernames
- scan, validate & fix foldernames
- Pre-screen and validate media, initializing database records for  
- Produce CSV files (After Validating Batch.)
- Emits progress and error messages to the Console Window.


### 5.3 CDASH Validator Module (`validator.py`)

- Uses the Omeka-S REST API to validate Place and Item Set resource IDs.
- Writes validation results (title or error) into the appropriate DB table.
- Must degrade gracefully when the API is unreachable (network offline); cached results from the DB may be used for previously validated IDs.

If a Place or Folder ID was previously validated and cached in the DB, the tool can
 accept that cached result as valid.

### 5.4 Media Prescreener Module (`prescreener.py`)

Checks media file properties and rejects files that do not conform. Uses **Pillow** for image files.

**Accepted formats:**
- TIFF: LZW compression, 24-bit RGB or 8-bit grayscale  
- JPEG: 24-bit RGB
- PDF verified conformance level PDF/A-1b
- 16-bit TIFFs are rejected

**Rejection criteria:**
- File size > 100 MB
- Pixel dimensions: Width × Height > 108 megapixels
- Wrong color mode (e.g., CMYK, Palette)
- Corrupt or unreadable file

**On rejection:**
- File is moved to `Rejects/<ItemSetFolderName>/`
- An entry is written to `rejects.csv`
- `go_nogo` status of the parent folder and batch is set to `no-go`


---


## 9. CSV Output Files

Produced by the Database Module when batch status is `go`. Written to `Catalog/`.

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
| `Source` | `Path to file, beginning with to Batch folder` |
| `number` | `page_num` |
| `dateAccepted` | `exif_date_created` |


### `media_rejects.csv`
| Column | Source |
|---|---|
| `filename` | Current OS file name |
| `filepath` | Path relative to batch root |
| `page_num` | Page number within document (1-based) |
| `capture_date` | From EXIF; ISO 8601 |
| `file_size_mb` |  |
| `pixel_width` |  |
| `pixel_height` |  |
| `color_mode` | e.g., `RGB`, `L`, `CMYK` |
| `status` |  |
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
| `placeItemID` | `place_id` | foreign key
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
| `resourceTemplate` | constant "CDASH Place"|
| `resourceClass` | constant "dcterms:Location" |
| `identifier` | `place_id` |
| `placeItem` | `place_name` |
| `placeName` | `place_name` |
| `PlaceItemID` | `PlaceItemId` | primary key
| `placeType` | `place_type` |
| `lat` | `lat` |
| `lon` | `lon` |
| `houseNum` | `house_num` |
| `streetName` | `street_name` |
| `streetSort` | `street_sort` |
| `Neighborhood` | `neighborhood` | comma delimited
| `chcDist` | `chc_dist` | comma delimited



---

## 10. Document Type Codes
This is a fixed list which is saved as a configurable variable in the project.
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


