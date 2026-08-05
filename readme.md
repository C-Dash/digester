# CDASH Presort Digester

A Windows desktop application that helps an archivist turn loose batches of
scanned images and PDFs into a validated **CDASH Import Batch** — renamed media
files plus CSV catalogs ready for the Omeka-S CSV Import tool.

CDASH is the **Cambridge Digital Architectural Survey and History**, a web
repository (Omeka-S) maintained by the Cambridge Historical Commission. In the
CDASH schema each media file is a page of a *Document*, which links to one
*Place* and one *Folder* (an Omeka Item Set). The Digester validates incoming
files, names them to the CDASH convention, resolves their Place/Folder
references against the live Omeka API, and exports the catalog CSVs.

---

## Features

- **Batch validation & cataloging** — format prescreening, filename parsing, and
  live validation of Place / Item-Set IDs against the Omeka-S REST API.
- **Interactive triage GUI** (PySide6) — folder tree, media table, thumbnail
  grid, and a docked console, with bidirectional selection sync.
- **Metadata assignment** — group/ungroup pages into Documents, assign Place ID
  and Document Type; files are renamed to canonical form and the folder is
  re-scanned so status reflects the change.
- **Auto-repair** of common defects (alpha/16-bit flattening, LZW compression),
  plus manual **Rotate CW/CCW** and **Reject** actions.
- **Persistent caches** for folder/place/file results so re-scans stay fast,
  with a one-click purge.
- **CSV export** of batch / folder / place / document / media tables.
- Runs fully responsive — long operations run on a background thread.

---

## Requirements

- **Python 3.11+**
- **ExifTool** on the `PATH` (used for EXIF capture-date / orientation reads).
- Python packages (installed via the project): PySide6, Pillow, PyMuPDF,
  requests.

## Install (development)

```
git clone <repo-url>
cd cdash_digester
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"        # runtime + test deps
```

## Run

```
python run_gui.py              # launch the GUI
cdash-digester                 # console-script entry point (after install)
```

Command-line helpers:

```
python -m cdash_digester.digester              # scan the bundled test batch, print status
python -m cdash_digester.prescreener <file>    # inspect one file's screening result
python -m cdash_digester.repair_media <file> <issue_code> [...]
python -m cdash_digester.exif_ex <file>        # dump all ExifTool tags
```

---

## Workflow

1. **Assemble** incoming files on disk: one folder per CDASH Item Set, inside a
   batch folder's `media/` directory (see *Batch structure* below).
2. **Choose Batch Folder** — the app validates the structure, opens/creates the
   SQLite catalog, and runs a full scan.
3. **Triage** in the GUI: red entries are not-ready. Fix names, assign metadata,
   repair files, or re-scan a folder. Validation and renaming usually take a few
   iterations.
4. **Produce CSV Files** — enabled only once the whole batch is ready.
5. Archive the completed batch to cold storage.

### Menus

| Menu | Item | Action |
|---|---|---|
| **Batch** | Choose Batch Folder… | Open a batch; initialise + full scan |
| | Re-Scan Batch | Rebuild the working tables from a fresh full scan |
| | Produce CSV Files | Export catalogs — **dim until the batch is ready** |
| | Write Status to Console | Print a folder/ready/reject summary |
| | Purge Validation Caches | Clear the folder/place/file caches |
| **Folder** | Rescan Selected Folder | Re-scan just the selected folder |
| **Media** | Assign Metadata… | Set Place ID / Doc Type, group or ungroup pages |
| | Repair Selected Media | Auto-repair fixable issues on the selection |
| **Digester** | Help / GitHub | Open the docs / source in a browser |
| | About… | Show version and build date |

The GUI panes:

```
┌───────────────────────────────────────────────┐
│ Menu Bar                                        │
├──────────────┬──────────────────────────────────┤
│ Folder Pane  │ Folder-info strip                │
│ (tree;       │ Media Table (rows = files)       │
│  green=ready ├──────────────────────────────────┤
│  red=not)    │ Thumbnail grid (Pillow / PyMuPDF)│
├──────────────┴──────────────────────────────────┤
│ Console (docked, timestamped, colour-coded)     │
└───────────────────────────────────────────────┘
```

Selection is synced both ways between the media table and thumbnails
(Ctrl-click to toggle, Shift-click for a range).

---

## Batch structure

**Before** (you create this):

```
CDB<YYMMDD>[a-z]?-<batch_name>/
  media/
    <name>-OF<OmekaItemSetID>/        ← one folder per Item Set
      *.tif  *.tiff  *.jpg  *.jpeg  *.pdf
```

**After** scanning / assignment:

```
CDB<YYMMDD>[a-z]?-<batch_name>/
  media/
    F<index>-<Item_Set_Slug>-OF<OmekaItemSetID>/
      <place_slug>_<doc_index>p<page>-<doc_type>-OP<place_id>.<sfx>
      repaired/                       ← originals, backed up before a repair
  catalog/
    batch_db.sqlite
    batch.csv  folder.csv  place.csv  document.csv  media.csv
    repair_reject.csv                 ← log of repair attempts and reject moves
    batch.log
  rejects/                            ← only if files were rejected
    <folder>/ ...
```

Names already in canonical form are left untouched on re-scan. The optional
trailing letter on the batch ID disambiguates same-day batches
(`CDB260319a`, `CDB260319b`).

### Naming conventions

| Thing | Pattern | Example |
|---|---|---|
| Batch folder | `CDB<YYMMDD>[a-z]?-<name>` | `CDB260430-Test_batch` |
| Media folder | `F<index>-<slug>-OF<item_set_id>` | `F3-168_Brattle_St-OF160936` |
| Media file stem | `<place_slug>-<doc_index:04d>p<page:04d>-<doc_type>-OP<place_id>` | `12_Reservoir_St_0017p0001-VE-OP196223` |

A folder without the `F<index>` prefix is accepted on input (the scanner assigns
the next free index and renames the folder). Once a folder carries an index it
keeps it: the scanner reads `F<index>` from the folder's own name, so numbering
survives **Purge Validation Caches** and the folder is not renumbered. Only the
descriptive slug is re-canonicalised if the Omeka folder name changes. If two
folders claim the same index, the first keeps it and the second is reassigned
with a warning.

A rudimentary file name (e.g. `photo001.tif`) is accepted and
registered `ready=False`; any `-OP<id>` / `-<doc_type>` hint tokens in the stem
are reused during assignment. `slugify()` replaces spaces with `_`, maps ` - `
to `-`, and drops other non-alphanumerics.

---

## Acceptance criteria (prescreener)

| Format | Rules |
|---|---|
| **JPEG** | 24-bit RGB, ≤ 100 MB, ≤ 108 MP |
| **TIFF** | 24-bit RGB **or** 8-bit grayscale (L); LZW compression; single-frame; ≤ 100 MB, ≤ 108 MP |
| **PDF** | Must carry a PDF/A conformance marker in its XMP metadata |

Files that fail are flagged with a **repair issue code** in `repair_issues` and
left not-ready:

| Code | Meaning | Auto-repairable? |
|---|---|---|
| `Flatten` | TIFF is RGBA, LA, or I;16 (16-bit grayscale) | Yes — dropped to a clean mode (RGB/L) |
| `Compress LZW` | TIFF not using LZW compression | Yes — re-saved with LZW |
| `Check MBs` | Uncompressed TIFF over the size limit | Only if it fits once compressed; otherwise left untouched |
| `Reject` | Unsupported file type, multi-frame TIFF, PDF without a PDF/A marker, or any other oversized/wrong-format file | No — use **Media > Reject Selected Media** |

`repair_file()` backs up originals to `media/<folder>/repaired/` and refuses
anything flagged `Reject`, pointing at the separate Reject action — the only
thing that moves a file out of the batch. A refused or reverted repair leaves
the original completely untouched. Every attempt, refusal, and reject move is
logged to `catalog/repair_reject.csv`.

A media file is **ready** only when format, name, and Place/Folder references
all pass.

### Place ↔ folder association

A Place may belong to several Item Sets (`cdash_place.item_set_ids`). During scan
and assignment, if the parent folder's `item_set_id` is not among them the file
is marked not-ready with: *"Place_ID is not associated with this folder in
CDASH."* (An empty/unknown `item_set_ids` skips the check.)

---

## Architecture

```
src/cdash_digester/
  cdash_objects.py   constants (DOC_TYPES, ACCEPTED_SUFFIXES), name parsers,
                     slugify, and BatchDB (facade over the db layer)
  digester.py        Digester — session facade/controller; owns batch state and
                     delegates to the service layer; CLI harness
  validator.py       CDASHValidator — Omeka-S REST calls (read-only)
  prescreener.py     screen_file() — format/size/mode checks
  repair_media.py    repair_file() — auto-repair transforms
  models.py          dataclass rows (mapping-compatible via a Row mixin)
  db/
    database.py      SQLite connection, schema, bool row factory
    repositories.py  Batch/Folder/Place/Doc/Media/Cache repos (the SQL)
  services/
    validation.py    folder/place validation with persistent caches
    screening.py     prescreener results with the file cache
    scanning.py      ScanService + FolderScanner (the scan pipeline)
    assignment.py    interactive metadata assignment
    repair.py        media repair orchestration
    export.py        CatalogExportService — CSV writers
  gui/
    main_window.py   MainWindow, _Worker (QThread), _AssignDialog, _AboutDialog
    folder_pane.py / folder_info_pane.py / media_table.py /
    thumbnail_pane.py / console_window.py / status_colors.py
```

**Threading.** The GUI runs each long operation on a `QThread` worker. The
Digester holds a single SQLite connection shared between threads
(`check_same_thread=False`); safety comes from the main window gating the UI
busy for a worker's lifetime (`_set_busy`) so main-thread reads never overlap a
worker. The worker redirects the Digester's `log` callback to a Qt signal.

**Caches.** `cdash_folder_cache`, `cdash_place_cache`, and `cdash_file_cache`
persist across scans (cleared by *Purge Validation Caches*). They let a re-scan
skip re-fetching Omeka data and re-screening unchanged files.

### Data model (SQLite)

Working tables `cdash_batch`, `cdash_folder`, `cdash_place`, `cdash_doc`,
`cdash_media`, `cdash_rejects` plus the three cache tables. Status fields are
stored as `INTEGER 0/1` and surfaced as Python `True/False`. Key derived flags:
`media.ready`, `folder.name_ready` / `folder.media_ready`, and `batch.ready`
(true iff every folder is name- and media-ready). Exact columns live in
`models.py` / `db/repositories.py`.

---

## CSV output

`Produce CSV Files` (only when the batch is ready) writes to `catalog/`:

| File | Contents |
|---|---|
| `batch.csv` | One row: batch identity, status, counts |
| `folder.csv` | One row per Item Set folder |
| `place.csv` | One row per validated Place (name, type, address, lat/lon, …) |
| `document.csv` | One row per Document (title, type, page count, dates) |
| `media.csv` | One row per media file (identifier, relation, page number) |
| `repair_reject.csv` | Log of repair attempts/refusals and reject moves — `MediaFolder, Filename, Repair_Issues, Repair_Action, Format_Issues` |

The exact column→source mappings are defined in
[`services/export.py`](src/cdash_digester/services/export.py).

### Document type codes (`DOC_TYPES`)

`VE` Exterior View · `VI` Interior View · `RF` Research Form ·
`AI` Architectural Inventory Form · `VP` Plan View · `CD` Correspondence ·
`RN` Research Notes · `HS` Historic American Buildings Survey · `CS` Contact
Sheet · `AM` Published Material · `SM` Supplemental Material · `VD` Detail View ·
`EP` Ephemera · `DM` Demolition Memo · `UC` Uncategorized

---

## Packaging (Windows)

Build a self-contained folder distribution with PyInstaller (run in the venv).
The build is driven by the version-controlled `cdash-digester.spec`, which is the
source of truth for what gets bundled (the `gui/assets` and `csv_mappings`
folders, the `exiftool.exe` binary, and the `fitz` package):

```
pyinstaller cdash-digester.spec
```

For a clean rebuild, clear the caches first so source changes are picked up:

First: Update the Build Date and Version number in __init__.py.
```
rm -rf build dist
pyinstaller cdash-digester.spec
```

Output is `dist/cdash-digester/` (ship the **whole** folder — the `.exe` needs
`_internal/` beside it). ExifTool is bundled and found at runtime via
`sys._MEIPASS`.

> The spec was originally generated from a long `pyinstaller --onedir --windowed
> …` command, but that command has since drifted (e.g. it predates the
> `csv_mappings` bundle). Edit `cdash-digester.spec` directly rather than
> regenerating it — regenerating would overwrite the tracked spec and drop
> bundled data.

## Testing

```
python -m pytest -q
```

`pytest` + `pytest-qt` (headless via the offscreen Qt platform). Suites cover
parsers, prescreener, repair, the DB/repository layer, the scan/assign services,
status colors, the worker threading model, and GUI smoke tests.

---

## Roadmap / limitations

- **Commit Changes** — consolidate page numbers across a folder and rename files
  to match DB state. Specified, not yet implemented.
- **Reject repair/restore tooling** beyond the current auto-repairs.
- **macOS / Linux** — not a current target, but paths use `pathlib` throughout.
