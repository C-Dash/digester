"""First pytest-qt smoke tests — confirms the GUI test harness works headless.

These exercise individual widgets in isolation (no batch/DB/network). They are a
starting point for the Phase 5 threading work, where worker/signal tests will be
added on top of this same qtbot setup.
"""

from cdash_digester import __build_date__, __version__
from cdash_digester.models import Folder
from cdash_digester.gui.folder_info_pane import FolderInfoPane
from cdash_digester.gui.folder_pane import FolderPane
from cdash_digester.gui.media_table import MediaTablePane
from cdash_digester.gui.thumbnail_pane import ThumbnailPane
from cdash_digester.gui.main_window import _AboutDialog, _AssignDialog

from PySide6.QtWidgets import QLabel


def _dialog_labels(dlg):
    return [w.text() for w in dlg.findChildren(QLabel)]


def test_about_dialog_shows_version_and_build_date(qtbot):
    dlg = _AboutDialog()
    qtbot.addWidget(dlg)
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    assert any(__version__ in t for t in texts)
    assert any(__build_date__ in t for t in texts)


def test_assign_dialog_shows_selected_count(qtbot):
    dlg = _AssignDialog(3)
    qtbot.addWidget(dlg)
    assert any("3 media files selected" == t for t in _dialog_labels(dlg))


def test_assign_dialog_count_is_singular_for_one(qtbot):
    dlg = _AssignDialog(1)
    qtbot.addWidget(dlg)
    assert any("1 media file selected" == t for t in _dialog_labels(dlg))


def _media_row(media_id, filename, ready=True):
    return {"media_id": media_id, "filename": filename, "doc_type_code": "VE",
            "page_num": 1, "repair_issues": "", "ready": ready, "notes": ""}


def _thumb_row(media_id, filename, ready=True):
    return {"media_id": media_id, "filename": filename, "ready": ready,
            "filepath": f"media/{filename}"}   # file absent -> blank thumbnail


def _load_thumbs(pane, tmp_path, n):
    rows = [_thumb_row(i, f"f{i}.tif") for i in range(1, n + 1)]
    pane.load_media(rows, tmp_path)


def test_thumbnail_shift_click_selects_range(qtbot, tmp_path):
    pane = ThumbnailPane()
    qtbot.addWidget(pane)
    _load_thumbs(pane, tmp_path, 5)

    pane._on_thumb_clicked(2, ctrl=False, shift=False)   # anchor on #2
    pane._on_thumb_clicked(4, ctrl=False, shift=True)    # shift-click #4
    assert pane._selected == {2, 3, 4}
    for mid in (2, 3, 4):
        assert pane._widgets[mid]._selected is True
    for mid in (1, 5):
        assert pane._widgets[mid]._selected is False


def test_thumbnail_shift_click_range_is_order_independent(qtbot, tmp_path):
    pane = ThumbnailPane()
    qtbot.addWidget(pane)
    _load_thumbs(pane, tmp_path, 5)

    pane._on_thumb_clicked(4, ctrl=False, shift=False)   # anchor on #4
    pane._on_thumb_clicked(2, ctrl=False, shift=True)    # shift-click earlier #2
    assert pane._selected == {2, 3, 4}


def test_thumbnail_ctrl_shift_extends_selection(qtbot, tmp_path):
    pane = ThumbnailPane()
    qtbot.addWidget(pane)
    _load_thumbs(pane, tmp_path, 5)

    pane._on_thumb_clicked(1, ctrl=False, shift=False)   # select #1
    pane._on_thumb_clicked(3, ctrl=True, shift=False)    # add #3 (anchor -> 3)
    pane._on_thumb_clicked(5, ctrl=True, shift=True)     # extend range 3..5
    assert pane._selected == {1, 3, 4, 5}


def test_media_table_load_clears_prior_selection(qtbot):
    pane = MediaTablePane()
    qtbot.addWidget(pane)
    pane.load_media([_media_row(1, "a.tif"), _media_row(2, "b.tif")])
    pane.selectRow(0)
    assert pane.selected_media_ids() == [1]

    # Switching folders (new rows) must not carry the old selection over.
    pane.load_media([_media_row(3, "c.tif"), _media_row(4, "d.tif")])
    assert pane.selected_media_ids() == []


def test_media_table_has_cell_padding(qtbot):
    pane = MediaTablePane()
    qtbot.addWidget(pane)
    assert "padding-left" in pane.styleSheet()


def test_media_table_status_text_colored_no_row_tint(qtbot):
    from PySide6.QtCore import Qt
    from cdash_digester.gui.media_table import _COLUMNS
    from cdash_digester.gui.status_colors import status_color

    pane = MediaTablePane()
    qtbot.addWidget(pane)
    pane.load_media([_media_row(1, "a.tif", ready=True),
                     _media_row(2, "b.tif", ready=False),
                     _media_row(3, "c.tif", ready=None)])
    model = pane.model()
    status_col = _COLUMNS.index("ready")

    for r, ready in enumerate((True, False, None)):
        status_idx = model.index(r, status_col)
        # Status text is colored with the strong palette...
        brush = model.data(status_idx, Qt.ForegroundRole)
        assert brush.color().name() == status_color(ready)
        # ...a non-status column has no foreground override...
        other_idx = model.index(r, _COLUMNS.index("filename"))
        assert model.data(other_idx, Qt.ForegroundRole) is None
        # ...and no row carries a background tint (selection highlight only).
        assert model.data(status_idx, Qt.BackgroundRole) is None
        assert model.data(other_idx, Qt.BackgroundRole) is None


def test_media_table_highlight_selects_all_ids(qtbot):
    pane = MediaTablePane()
    qtbot.addWidget(pane)
    pane.load_media([_media_row(i, f"f{i}.tif") for i in range(1, 6)])

    # Full contiguous range (the regression: a row used to be dropped).
    pane.highlight_media_ids([1, 2, 3, 4])
    assert sorted(pane.selected_media_ids()) == [1, 2, 3, 4]

    # Non-contiguous set round-trips fully.
    pane.highlight_media_ids([1, 3, 5])
    assert sorted(pane.selected_media_ids()) == [1, 3, 5]

    # Empty clears the selection.
    pane.highlight_media_ids([])
    assert pane.selected_media_ids() == []


def test_media_table_copy_cell_to_clipboard(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from cdash_digester.gui.media_table import _COLUMNS

    pane = MediaTablePane()
    qtbot.addWidget(pane)
    assert pane.contextMenuPolicy() == Qt.CustomContextMenu
    pane.load_media([_media_row(1, "alpha.tif", ready=False)])
    model = pane.model()

    pane._copy_cell(model.index(0, _COLUMNS.index("filename")))
    assert QApplication.clipboard().text() == "alpha.tif"

    pane._copy_cell(model.index(0, _COLUMNS.index("ready")))
    assert QApplication.clipboard().text() == "Not Ready"   # display text


def test_media_table_load_does_not_emit_selection(qtbot):
    pane = MediaTablePane()
    qtbot.addWidget(pane)
    pane.load_media([_media_row(1, "a.tif")])
    pane.selectRow(0)

    emitted = []
    pane.selection_changed.connect(emitted.append)
    pane.load_media([_media_row(2, "b.tif")])   # folder switch, suppressed
    assert emitted == []


def test_folder_info_pane_shows_folder(qtbot):
    pane = FolderInfoPane()
    qtbot.addWidget(pane)
    folder = Folder.from_row({
        "cdash_folder_name": "Main St Folder", "os_folder_name": "F1-Main-OF101",
        "name_ready": True, "media_ready": False, "notes": "two-page doc",
    })
    pane.show_folder(folder)
    assert "F1-Main-OF101" in pane._name.text()   # shows the os folder name
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
    assert blocker.args[0]["item_set_id"] == 101


def test_folder_pane_emits_for_unparseable_folder(qtbot):
    pane = FolderPane()
    qtbot.addWidget(pane)
    pane.load_folders([
        Folder.from_row({"os_folder_name": "Nonsense", "item_set_id": None,
                         "name_ready": False, "media_ready": False,
                         "notes": "Folder name not in CDASH format"}),
    ])
    item = pane.topLevelItem(0)
    with qtbot.waitSignal(pane.folder_selected, timeout=1000) as blocker:
        pane._on_clicked(item, 0)
    row = blocker.args[0]
    assert row["item_set_id"] is None
    assert row["notes"] == "Folder name not in CDASH format"


def test_folder_info_pane_shows_unparseable_folder(qtbot):
    pane = FolderInfoPane()
    qtbot.addWidget(pane)
    pane.show_folder(Folder.from_row({
        "cdash_folder_name": None, "os_folder_name": "Nonsense",
        "item_set_id": None, "name_ready": False, "media_ready": False,
        "notes": "Folder name not in CDASH format",
    }))
    assert "Nonsense" in pane._name.text()
    assert pane._name_status.text() == "NO"
    assert pane._note.text() == "Folder name not in CDASH format"


def test_folder_pane_select_folder_by_key(qtbot):
    from cdash_digester.gui.folder_pane import folder_key
    pane = FolderPane()
    qtbot.addWidget(pane)
    normal = Folder.from_row({"os_folder_name": "F1-Main-OF101", "item_set_id": 101,
                              "name_ready": True, "media_ready": True})
    bad = Folder.from_row({"os_folder_name": "Nonsense", "item_set_id": None,
                           "name_ready": False, "media_ready": False})
    pane.load_folders([normal, bad])

    pane.select_folder(folder_key(normal))
    assert pane.current_row()["item_set_id"] == 101
    pane.select_folder(folder_key(bad))
    assert pane.current_row()["os_folder_name"] == "Nonsense"
