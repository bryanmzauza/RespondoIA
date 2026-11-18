"""Private contacts and history deletion from the settings window."""

from dataclasses import replace

from test_app import Rig
from test_app_history import HistoryRig
from test_history_service import FakeClient, lines, observe

from respondoia import app as app_module
from respondoia.config import Settings
from respondoia.memory.service import HistoryService

ON = Settings(history_enabled=True)


def test_private_added_in_settings_is_forgotten_at_once(tmp_path):
    rig = Rig(tmp_path, ON)
    rig.controller.apply_settings(replace(ON, private_contacts=("Ana",)))
    assert ("forget", "Ana") in rig.worker.calls
    rig.worker.calls.clear()
    rig.controller.apply_settings(replace(ON, private_contacts=("Ana", "Bia")))
    assert rig.worker.calls.count(("forget", "Bia")) == 1
    assert ("forget", "Ana") not in rig.worker.calls


def test_no_memory_call_for_a_private_contact(db):
    client = FakeClient()
    current = [ON]
    history = HistoryService(db, lambda: client, lambda: current[0])
    first = observe(history, db, "Ana", lines("pedido", 25))
    current[0] = replace(ON, private_contacts=("ana",))
    assert not history.update_memory_if_due(first.contact_id) and client.texts == []


def test_history_is_deleted_only_after_the_save_worked(qtbot, tmp_path, monkeypatch):
    rig = HistoryRig(tmp_path)
    rig.controller.open_settings()
    window = rig.controller._settings_window
    window.delete_history_on_save = True

    def broken(path, settings):
        raise OSError("disk full")

    monkeypatch.setattr(app_module.config, "save_settings", broken)
    window.saved.emit(replace(ON, history_enabled=False))
    assert ("forget_all",) not in rig.history.calls
    monkeypatch.undo()
    monkeypatch.setattr(app_module.config, "get_api_key", lambda: "sk-test-not-a-real-key")
    window.saved.emit(replace(ON, history_enabled=False))
    assert ("forget_all",) in rig.history.calls
    window.close()
