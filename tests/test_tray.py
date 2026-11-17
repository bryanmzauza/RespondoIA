import pytest

from respondoia import i18n
from respondoia.ui.tray import Tray, make_icon


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


def labels(tray):
    return [action.text() for action in tray.contextMenu().actions() if not action.isSeparator()]


def test_menu_has_settings_style_pause_and_quit(qapp):
    tray = Tray()
    assert labels(tray) == ["Configurações", "Aprender meu estilo", "Pausar atalho", "Sair"]
    assert tray.toolTip() == "RespondoIA"


def test_pause_action_toggles_label_and_signal(qtbot):
    tray = Tray()
    with qtbot.waitSignal(tray.pause_toggled) as sig:
        tray.pause_action.trigger()
    assert sig.args == [True]
    assert labels(tray)[2] == "Retomar atalho" and tray.toolTip() == "RespondoIA (pausado)"
    with qtbot.waitSignal(tray.pause_toggled) as sig:
        tray.pause_action.trigger()
    assert sig.args == [False]
    assert labels(tray)[2] == "Pausar atalho" and tray.toolTip() == "RespondoIA"


def test_set_paused_does_not_emit(qtbot):
    tray = Tray()
    with qtbot.assertNotEmitted(tray.pause_toggled):
        tray.set_paused(True)
    assert labels(tray)[2] == "Retomar atalho"


def test_quit_action_emits(qtbot):
    tray = Tray()
    with qtbot.waitSignal(tray.quit_requested):
        tray.quit_action.trigger()


def test_icons_differ_between_states(qapp):
    active = make_icon(False).pixmap(32, 32).toImage()
    paused = make_icon(True).pixmap(32, 32).toImage()
    assert not active.isNull() and active != paused


def test_notify_shows_the_translated_text(qapp, monkeypatch):
    tray = Tray()
    shown = []
    monkeypatch.setattr(tray, "showMessage", lambda *args: shown.append(args[:2]), raising=False)
    tray.notify("notice.started", hotkey="Ctrl+Shift+Space")
    assert shown == [("RespondoIA", "RespondoIA está ativo. Atalho: Ctrl+Shift+Space")]


def test_notify_accepts_a_placeholder_named_key(qapp, monkeypatch):
    i18n.install(qapp, "en")
    tray = Tray()
    shown = []
    monkeypatch.setattr(tray, "showMessage", lambda *args: shown.append(args[1]), raising=False)
    tray.notify("error.config", key="suggestions.count")
    assert shown == ["config.toml has an invalid value: suggestions.count"]


def test_settings_action_emits(qtbot):
    tray = Tray()
    with qtbot.waitSignal(tray.settings_requested):
        tray.settings_action.trigger()


def test_retranslate_follows_the_installed_language(qapp):
    tray = Tray()
    tray.set_paused(True)
    i18n.install(qapp, "en")
    tray.retranslate()
    assert labels(tray) == ["Settings", "Learn my style", "Resume hotkey", "Quit"]
    assert tray.toolTip() == "RespondoIA (paused)"


def test_style_item_emits(qtbot):
    tray = Tray()
    with qtbot.waitSignal(tray.style_requested):
        tray.style_action.trigger()
