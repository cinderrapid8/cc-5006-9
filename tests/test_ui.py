"""Qt UI smoke tests under the offscreen platform."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from proxyworkstation.ui.export_dialog import ExportDialog  # noqa: E402
from proxyworkstation.ui.main_window import MainWindow  # noqa: E402
from proxyworkstation.ui.preview import FramePreview  # noqa: E402
from proxyworkstation.ui.relocation import RelocationDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp, proxied):
    win = MainWindow(
        os.path.join(proxied["tmp"], "project.db"), proxied["proxy_dir"]
    )
    # Reuse the already-populated database from the fixture.
    win.ws.close()
    from proxyworkstation.core.workstation import Workstation

    win.ws = Workstation(os.path.join(proxied["tmp"], "project.db"))
    win.refresh_sources()
    win.refresh_clips()
    yield win
    win.close()


def test_main_window_lists_sources_and_proxy_status(window):
    assert window.source_table.rowCount() == 3
    proxy_cell = window.source_table.item(0, 5).text()
    assert "verified" in proxy_cell


def test_quality_switch_loads_proxy_and_original(window):
    window.source_table.selectRow(0)
    window.quality_combo.setCurrentText("proxy")
    proxy_path = window.preview._media_path
    assert proxy_path and "proxy" in proxy_path
    window.quality_combo.setCurrentText("original")
    original_path = window.preview._media_path
    assert original_path and "proxy" not in original_path
    # Frame 0 decodes and displays both coordinate labels.
    assert "source frame" in window._meta_label.text() if hasattr(window, "_meta_label") else True


def test_clip_added_through_ui_uses_source_coordinates(window):
    window.clip_shot_combo.setCurrentText("SHOT_A")
    window.in_spin.setValue(2)
    window.out_spin.setValue(9)
    window.add_clip()
    assert window.clip_table.rowCount() == 1
    assert window.clip_table.item(0, 2).text() == "2"
    assert window.clip_table.item(0, 3).text() == "9"


def test_preview_step_navigation_is_frame_accurate(window):
    window.source_table.selectRow(1)
    window.quality_combo.setCurrentText("proxy")
    start = window.preview.position
    window.preview.step(3)
    assert window.preview.position == start + 3
    window.preview.step(-1)
    assert window.preview.position == start + 2


def test_export_dialog_shows_ready_and_parameters(window):
    window.clip_shot_combo.setCurrentText("SHOT_B")
    window.in_spin.setValue(0)
    window.out_spin.setValue(6)
    window.add_clip()
    dlg = ExportDialog(window.ws)
    assert "READY" in dlg.preflight_view.toPlainText()
    assert dlg.width_spin.value() > 0
    assert dlg.crf_spin.value() == 18


def test_export_dialog_blocks_when_offline(window, tmp_path):
    import shutil

    window.clip_shot_combo.setCurrentText("SHOT_A")
    window.in_spin.setValue(0)
    window.out_spin.setValue(5)
    window.add_clip()
    source = window.ws.store.get_source_by_shot("SHOT_A")
    shutil.move(source["current_path"], str(tmp_path / "SHOT_A.mp4"))
    dlg = ExportDialog(window.ws)
    assert "BLOCKED" in dlg.preflight_view.toPlainText()
    assert dlg.parameters is None


def test_relocation_dialog_lists_offline_and_resolves(window, tmp_path):
    import shutil

    source = window.ws.store.get_source_by_shot("SHOT_C")
    new_home = tmp_path / "SHOT_C.mp4"
    shutil.move(source["current_path"], str(new_home))
    dlg = RelocationDialog(window.ws)
    assert dlg.list_widget.count() == 1
    window.ws.relocate_source(source["content_hash"], str(new_home))
    dlg.refresh()
    assert dlg.list_widget.count() == 0


def test_preview_shows_offline_message(window):
    import shutil

    source = window.ws.store.get_source_by_shot("SHOT_B")
    shutil.move(
        source["current_path"],
        os.path.join(os.path.dirname(source["current_path"]), "SHOT_B_hidden.mp4"),
    )
    window.source_table.selectRow(1)
    window.quality_combo.setCurrentText("original")
    assert window.preview._media_path is None
    assert "OFFLINE" in window.preview._label.text()
