import pytest
from PySide6.QtWidgets import QMessageBox
from test_settings_window import make

from respondoia import i18n
from respondoia.config import Settings
from respondoia.i18n import t
from respondoia.ui import settings as settings_module


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


def answer(monkeypatch, button):
    monkeypatch.setattr(
        settings_module.QMessageBox,
        "question",
        lambda *a, **k: getattr(QMessageBox.StandardButton, button),
    )


def test_history_group_round_trip(qtbot):
    window = make(qtbot, Settings(history_enabled=False, retention_months=12))
    assert not window.history_check.isChecked()
    assert window.history_hint.text() == t("settings.history_hint")
    window.history_check.setChecked(True)
    window.retention_box.setValue(6)
    with qtbot.waitSignal(window.saved) as signal:
        window.save_button.click()
    saved = signal.args[0]
    assert saved.history_enabled and saved.retention_months == 6


@pytest.mark.parametrize("button,deleted", [("No", False), ("Yes", True)])
def test_turning_history_off_asks_to_delete(qtbot, monkeypatch, button, deleted):
    window = make(qtbot, Settings(history_enabled=True))
    answer(monkeypatch, button)
    asked = []
    window.delete_all_requested.connect(lambda: asked.append(True))
    window.history_check.setChecked(False)
    with qtbot.waitSignal(window.saved) as signal:
        window.save_button.click()
    # The deletion waits for the save: the controller runs it only if the save works.
    assert not signal.args[0].history_enabled and not asked
    assert window.delete_history_on_save is deleted


def test_delete_all_asks_first(qtbot, monkeypatch):
    window = make(qtbot, Settings(history_enabled=True))
    answer(monkeypatch, "No")
    asked = []
    window.delete_all_requested.connect(lambda: asked.append(True))
    window.history_delete_button.click()
    assert not asked
    answer(monkeypatch, "Yes")
    window.history_delete_button.click()
    assert asked
