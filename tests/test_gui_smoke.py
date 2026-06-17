"""First pytest-qt smoke tests — confirms the GUI test harness works headless.

These exercise individual widgets in isolation (no batch/DB/network). They are a
starting point for the Phase 5 threading work, where worker/signal tests will be
added on top of this same qtbot setup.
"""

from cdash_digester.models import Folder
from cdash_digester.gui.folder_info_pane import FolderInfoPane
from cdash_digester.gui.folder_pane import FolderPane


def test_folder_info_pane_shows_folder(qtbot):
    pane = FolderInfoPane()
    qtbot.addWidget(pane)
    folder = Folder.from_row({
        "cdash_folder_name": "Main St Folder", "os_folder_name": "F1-Main-OF101",
        "name_ready": True, "media_ready": False, "notes": "two-page doc",
    })
    pane.show_folder(folder)
    assert "Main St Folder" in pane._name.text()
    assert pane._name_status.text() == "ok"   # name_ready True
    assert pane._media_status.text() == "NO"   # media_ready False
    assert pane._note.text() == "two-page doc"


def test_folder_info_pane_clear(qtbot):
    pane = FolderInfoPane()
    qtbot.addWidget(pane)
    pane.show_folder(Folder.from_row({"cdash_folder_name": "X", "name_ready": True,
                                      "media_ready": True, "notes": "n"}))
    pane.clear()
    assert pane._note.text() == ""
    assert pane._name.text() == "—"


def test_folder_pane_emits_selection(qtbot):
    pane = FolderPane()
    qtbot.addWidget(pane)
    pane.load_folders([
        Folder.from_row({"os_folder_name": "F1-Main-OF101", "item_set_id": 101,
                         "name_ready": True, "media_ready": True}),
    ])
    item = pane.topLevelItem(0)
    with qtbot.waitSignal(pane.folder_selected, timeout=1000) as blocker:
        pane._on_clicked(item, 0)
    assert blocker.args == [101]
