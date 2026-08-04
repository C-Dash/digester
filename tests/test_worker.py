"""Phase 5 threading-model tests.

These verify the busy-gating that serializes access to the single shared SQLite
connection: while a worker runs, the folder pane and DB-touching menu actions
are disabled, and they re-enable once the worker's ``done`` signal fires.
"""

from pathlib import Path

from cdash_digester.digester import Digester
from cdash_digester.gui.folder_pane import folder_key
from cdash_digester.gui.main_window import MainWindow
from conftest import FakeValidator, make_pdf, make_tiff, place_props


# The DB-touching actions that _set_busy blanket-gates, alongside the folder
# pane.  _act_csv is intentionally excluded: it is gated on batch ready, not
# blanket-enabled with the rest (see test_csv_action_gated_on_batch_ready).
def _gated_actions(win):
    return [win._act_choose, win._act_init, win._act_status,
            win._act_purge_cache, win._act_val_folder, win._act_assign,
            win._act_repair]


def test_set_busy_toggles_db_touching_ui(qtbot, make_batch):
    root = make_batch("CDB260430-Test_batch")
    win = MainWindow()
    qtbot.addWidget(win)
    win.digester = Digester(root, log=lambda *a, **k: None)
    win.digester.load_or_initialize()

    win._set_busy(True)
    assert win.folder_pane.isEnabled() is False
    assert all(not a.isEnabled() for a in _gated_actions(win))
    assert win._act_csv.isEnabled() is False   # always dim while busy

    win._set_busy(False)
    assert win.folder_pane.isEnabled() is True
    assert all(a.isEnabled() for a in _gated_actions(win))
    # No open/ready batch → CSV stays dim even when idle.
    assert win._act_csv.isEnabled() is False
    win.digester.close()


def test_set_busy_keeps_batch_actions_dim_without_a_batch(qtbot):
    """Lifting the busy gate must not enable batch actions when no batch is
    open. Choose Batch Folder is the exception — it needs no batch.

    Regression: Choose Batch Folder now routes load_or_initialize through a
    worker, so _set_busy(False) fires even when the open failed.
    """
    win = MainWindow()
    qtbot.addWidget(win)

    win._set_busy(False)
    assert win._act_choose.isEnabled() is True
    assert all(not a.isEnabled() for a in win._batch_actions())


def test_csv_action_gated_on_batch_ready(qtbot, make_batch):
    """Produce CSV Files is enabled only when the batch ready status is True."""
    root = make_batch("CDB260430-Test_batch")
    win = MainWindow()
    qtbot.addWidget(win)
    win.digester = Digester(root, log=lambda *a, **k: None)
    win.digester.load_or_initialize()

    win.digester.db.set_batch_ready(False)
    win._sync_actions()
    assert win._act_csv.isEnabled() is False

    win.digester.db.set_batch_ready(True)
    win._sync_actions()
    assert win._act_csv.isEnabled() is True

    win.digester.close()


def test_run_gates_ui_for_worker_lifetime(qtbot, make_batch):
    """_run disables the UI on start, then re-enables it on the worker's done."""
    root = make_batch("CDB260430-Test_batch")
    win = MainWindow()
    qtbot.addWidget(win)
    win.digester = Digester(root, log=lambda *a, **k: None)
    win.digester.load_or_initialize()

    # Kick off a fast op (reads the DB only on the worker thread) and confirm
    # the UI is gated busy synchronously, before the worker can finish.
    win._run(win._log_status_summary)
    assert win.folder_pane.isEnabled() is False
    assert all(not a.isEnabled() for a in _gated_actions(win))

    # Once the worker finishes, the gate lifts (poll to avoid racing a fast op
    # that could emit done before a waitSignal connects).
    qtbot.waitUntil(lambda: win.folder_pane.isEnabled(), timeout=2000)
    assert all(a.isEnabled() for a in _gated_actions(win))
    win.digester.close()


def test_run_passes_args_to_the_callable(qtbot, make_batch):
    """_run(fn, *args, **kwargs) calls fn on the worker thread with those args.

    Replaces the old stringly-typed op dispatch, where the op name and each
    kwarg key were untyped strings looked up per branch.
    """
    root = make_batch("CDB260430-Test_batch")
    win = MainWindow()
    qtbot.addWidget(win)
    win.digester = Digester(root, log=lambda *a, **k: None)
    win.digester.load_or_initialize()

    seen = []
    win._run(lambda *a, **k: seen.append((a, k)), 1, 2, three=3)
    qtbot.waitUntil(lambda: win.folder_pane.isEnabled(), timeout=2000)

    assert seen == [((1, 2), {"three": 3})]
    win.digester.close()


def test_run_reports_worker_exceptions(qtbot, make_batch):
    """An exception in the operation reaches the console via the error signal.

    Regression: the old if/elif dispatch had no else branch, so an unrecognized
    op did nothing, emitted done, and ran the success refresh silently.
    """
    root = make_batch("CDB260430-Test_batch")
    win = MainWindow()
    qtbot.addWidget(win)
    win.digester = Digester(root, log=lambda *a, **k: None)
    win.digester.load_or_initialize()

    def boom():
        raise ValueError("kaboom")

    # Assert via the console rather than the error signal: _run connects the
    # console handler before starting the worker, so there is no race between
    # the worker raising and the test subscribing.
    win._run(boom)
    qtbot.waitUntil(lambda: win.folder_pane.isEnabled(), timeout=2000)

    logged = win.console._text.toPlainText()
    assert "ERROR: ValueError: kaboom" in logged
    win.digester.close()


def _open_batch_in_window(qtbot, root, win):
    """Drive Choose Batch Folder's worker half, minus the file dialog."""
    win.batch_root = root
    win.digester = Digester(root, log=lambda *a, **k: None)
    win.digester._validator = FakeValidator(
        folders={101: "Main St Folder"}, places={55: place_props("Main Place")})
    win._run(win._open_and_scan, on_finish=win._after_batch_opened)
    qtbot.waitUntil(lambda: win.folder_pane.isEnabled(), timeout=10000)


def test_open_and_scan_opens_batch_off_the_main_thread(qtbot, make_batch):
    """Choose Batch Folder opens AND scans in one worker, then wires up the UI.

    Regression: load_or_initialize used to run synchronously on the main
    thread, un-gated, despite a (dead) worker branch existing for it.
    """
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
              compression="tiff_lzw")

    win = MainWindow()
    qtbot.addWidget(win)
    _open_batch_in_window(qtbot, root, win)

    assert win.digester.is_open
    assert win.folder_pane.topLevelItemCount() == 1
    # Batch actions are enabled now that a batch really is open.
    assert all(a.isEnabled() for a in win._batch_actions())
    # No folder is selected yet on a fresh open, so an empty media table here
    # is correct — see test_rescan_batch_refreshes_media_table for the case
    # where a selection exists.
    assert win.media_table.model().rowCount() == 0
    win.digester.close()


def test_rescan_batch_refreshes_media_table(qtbot, make_batch):
    """Re-Scan Batch reloads the media table, not just the folder pane.

    Regression: _initialize_batch passed on_finish=_reload_folders, so after a
    full rescan the media table and thumbnail pane kept showing pre-scan rows
    for the still-selected folder.
    """
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
              compression="tiff_lzw")

    win = MainWindow()
    qtbot.addWidget(win)
    _open_batch_in_window(qtbot, root, win)

    # Select the folder, as a user would, so the media table is populated.
    row = win.digester.get_folders()[0]
    win.folder_pane.select_folder(folder_key(row))
    win._on_folder_selected(row)
    assert win.media_table.model().rowCount() == 1

    # Add a second file on disk, then Re-Scan Batch.
    scanned_dir = win.digester.media_path / row["os_folder_name"]
    make_tiff(scanned_dir / "Main_0002p0001-VE-OP55.tif", "RGB",
              compression="tiff_lzw")
    win._initialize_batch()
    qtbot.waitUntil(lambda: win.folder_pane.isEnabled(), timeout=10000)

    assert win.media_table.model().rowCount() == 2
    win.digester.close()


def test_rotate_actions_track_selection_rotatability(qtbot, make_batch):
    """Rotate CW/CCW enable only when the selection holds a rotatable file.

    Rotatability is resolved once per folder load and cached, so this also
    covers that _sync_actions intersects against that cache correctly rather
    than re-querying the DB per selection change.
    """
    root = make_batch("CDB260430-Test_batch")
    folder = root / "media" / "F1-Main-OF101"
    folder.mkdir(parents=True)
    make_tiff(folder / "Main_0001p0001-VE-OP55.tif", "RGB",
              compression="tiff_lzw")
    make_pdf(folder / "Main_0002p0001-RF-OP55.pdf")

    win = MainWindow()
    qtbot.addWidget(win)
    _open_batch_in_window(qtbot, root, win)

    row = win.digester.get_folders()[0]
    win.folder_pane.select_folder(folder_key(row))
    win._on_folder_selected(row)

    media = win.digester.get_media_for_folder(row["item_set_id"])
    by_suffix = {Path(m["filename"]).suffix.lower(): m["media_id"] for m in media}
    assert set(by_suffix) == {".tif", ".pdf"}

    # TIFF selected -> rotatable
    win.media_table.highlight_media_ids([by_suffix[".tif"]])
    win._sync_actions()
    assert win._act_rotate_cw.isEnabled() is True
    assert win._act_rotate_ccw.isEnabled() is True

    # PDF only -> not rotatable (rotation never applies to PDFs)
    win.media_table.highlight_media_ids([by_suffix[".pdf"]])
    win._sync_actions()
    assert win._act_rotate_cw.isEnabled() is False
    assert win._act_rotate_ccw.isEnabled() is False

    # Nothing selected -> not rotatable
    win.media_table.highlight_media_ids([])
    win._sync_actions()
    assert win._act_rotate_cw.isEnabled() is False
    win.digester.close()
