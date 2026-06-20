"""Phase 5 threading-model tests.

These verify the busy-gating that serializes access to the single shared SQLite
connection: while a worker runs, the folder pane and DB-touching menu actions
are disabled, and they re-enable once the worker's ``done`` signal fires.
"""

from cdash_digester.digester import Digester
from cdash_digester.gui.main_window import MainWindow


# The DB-touching actions that _set_busy blanket-gates, alongside the folder
# pane.  _act_csv is intentionally excluded: it is gated on batch ready, not
# blanket-enabled with the rest (see test_csv_action_gated_on_batch_ready).
def _gated_actions(win):
    return [win._act_choose, win._act_init, win._act_status,
            win._act_purge_cache, win._act_val_folder, win._act_assign,
            win._act_repair]


def test_set_busy_toggles_db_touching_ui(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    win._set_busy(True)
    assert win.folder_pane.isEnabled() is False
    assert all(not a.isEnabled() for a in _gated_actions(win))
    assert win._act_csv.isEnabled() is False   # always dim while busy

    win._set_busy(False)
    assert win.folder_pane.isEnabled() is True
    assert all(a.isEnabled() for a in _gated_actions(win))
    # No open/ready batch → CSV stays dim even when idle.
    assert win._act_csv.isEnabled() is False


def test_csv_action_gated_on_batch_ready(qtbot, make_batch):
    """Produce CSV Files is enabled only when the batch ready status is True."""
    root = make_batch("CDB260430-Test_batch")
    win = MainWindow()
    qtbot.addWidget(win)
    win.digester = Digester(root, log=lambda *a, **k: None)
    win.digester.load_or_initialize()

    win.digester.db.set_batch_ready(False)
    win._sync_csv_enabled()
    assert win._act_csv.isEnabled() is False

    win.digester.db.set_batch_ready(True)
    win._sync_csv_enabled()
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
    win._run("status_summary")
    assert win.folder_pane.isEnabled() is False
    assert all(not a.isEnabled() for a in _gated_actions(win))

    # Once the worker finishes, the gate lifts (poll to avoid racing a fast op
    # that could emit done before a waitSignal connects).
    qtbot.waitUntil(lambda: win.folder_pane.isEnabled(), timeout=2000)
    assert all(a.isEnabled() for a in _gated_actions(win))
    win.digester.close()
