from datetime import date

import pytest
from PySide6.QtWidgets import QMessageBox
from test_panel import make_panel, make_reply

from respondoia import i18n
from respondoia.memory.service import HistoryContext
from respondoia.ui import panel as panel_module


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


@pytest.fixture
def panel(qtbot):
    return make_panel(qtbot, make_reply(3))


def actions(panel):
    return {a.data(): a for a in panel.history_button.menu().actions() if a.data()}


def answer(monkeypatch, button):
    monkeypatch.setattr(
        panel_module.QMessageBox,
        "question",
        lambda *a, **k: getattr(QMessageBox.StandardButton, button),
    )


def test_history_line_shows_count_and_gap(panel):
    today = date.today().isoformat()
    panel.set_history(HistoryContext("ready", 7, "Marina", messages=1240, last_day=today))
    assert panel.history_label.isVisible()
    assert panel.history_label.text() == "1240 mensagens · atualizado hoje"
    panel.set_history(
        HistoryContext("ready", 7, "Marina", messages=5, last_day=today, gap_since="2026-11-03")
    )
    assert panel.history_label.text().startswith("Lacuna desde ")
    assert "load_gap" in actions(panel)


def test_history_line_names_the_owner_with_namesakes(panel):
    panel.set_history(
        HistoryContext(
            "ready", 7, "Ana", owner_since="2026-10-01", messages=3, last_day="2026-11-10"
        )
    )
    assert panel.history_label.text().startswith("Memória de Ana, desde ")


def test_empty_history_offers_a_quick_load(panel):
    panel.set_history(HistoryContext("ready", 7, "Ana"))
    assert panel.history_label.text() == "Ainda sem histórico"
    assert set(actions(panel)) >= {
        "load_quick",
        "load_full",
        "view_memory",
        "contact_off",
        "contact_delete",
    }
    assert "load_gap" not in actions(panel)


def test_identity_question_emits_the_choice(qtbot, panel):
    asked = HistoryContext("ask", owner="Ana", candidates=((3, "2026-10-01"),))
    panel.set_history(asked)
    assert panel.ask_label.isVisible() and "Ana" in panel.ask_label.text()
    assert len(panel.ask_buttons) == 2 and not panel.history_button.isVisible()
    with qtbot.waitSignal(panel.identity_answered) as signal:
        panel.ask_buttons[0].click()
    assert signal.args == [3]
    panel.set_history(asked)
    with qtbot.waitSignal(panel.identity_answered) as signal:
        panel.ask_buttons[-1].click()
    assert signal.args == [None]


def test_menu_emits_load_purposes(qtbot, panel):
    panel.set_history(HistoryContext("ready", 7, "Ana", messages=3, gap_since="2026-11-01"))
    for key, purpose in (("load_quick", "quick"), ("load_full", "full"), ("load_gap", "gap")):
        with qtbot.waitSignal(panel.load_requested) as signal:
            actions(panel)[key].trigger()
        assert signal.args == [purpose]
    with qtbot.waitSignal(panel.memory_requested):
        actions(panel)["view_memory"].trigger()


@pytest.mark.parametrize("button,emitted", [("No", False), ("Yes", True)])
def test_delete_asks_first(panel, monkeypatch, button, emitted):
    panel.set_history(HistoryContext("ready", 7, "Ana", messages=3))
    answer(monkeypatch, button)
    deleted = []
    panel.contact_history_deleted.connect(lambda: deleted.append(True))
    actions(panel)["contact_delete"].trigger()
    assert bool(deleted) is emitted


def test_turning_a_contact_off_asks_and_on_does_not(qtbot, panel, monkeypatch):
    panel.set_history(HistoryContext("ready", 7, "Ana", messages=3))
    answer(monkeypatch, "Yes")
    with qtbot.waitSignal(panel.contact_history_toggled) as signal:
        actions(panel)["contact_off"].trigger()
    assert signal.args == [False]
    panel.set_history(HistoryContext("out_of_scope", 7, "Ana"))
    assert set(actions(panel)) == {"contact_on"}
    with qtbot.waitSignal(panel.contact_history_toggled) as signal:
        actions(panel)["contact_on"].trigger()
    assert signal.args == [True]


@pytest.mark.parametrize("state", [None, "off", "group", "unavailable"])
def test_nothing_shows_without_history(panel, state):
    panel.set_history(None if state is None else HistoryContext(state))
    assert not panel.history_label.isVisible() and not panel.history_button.isVisible()
    assert not panel.ask_label.isVisible()


def test_history_text_is_plain(panel):
    panel.set_history(HistoryContext("ready", 7, "<b>Ana</b>", owner_since="2026-10-01"))
    assert "<b>Ana</b>" in panel.history_label.text()
    assert panel.history_label.textFormat() == panel_module.Qt.TextFormat.PlainText
