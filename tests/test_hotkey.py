import pytest

from respondoia.hotkey import (
    MOD_CONTROL,
    MOD_SHIFT,
    WM_HOTKEY,
    Hotkey,
    HotkeyError,
    display_hotkey,
    is_hotkey_message,
    parse_hotkey,
)


def test_default_hotkey():
    assert parse_hotkey("ctrl+shift+space") == Hotkey(MOD_CONTROL | MOD_SHIFT, 0x20)


def test_order_case_and_spaces_do_not_matter():
    assert parse_hotkey(" Space + SHIFT+Ctrl ") == parse_hotkey("ctrl+shift+space")


@pytest.mark.parametrize(
    "text,code",
    [
        ("", "empty"),
        ("space", "no_modifier"),
        ("ctrl+shift", "unknown_key"),
        ("ctrl+enter", "unknown_key"),
        ("ctrl+a+b", "unknown_key"),
    ],
)
def test_invalid_hotkeys(text, code):
    with pytest.raises(HotkeyError) as err:
        parse_hotkey(text)
    assert err.value.code == code


def test_function_keys_and_letters():
    assert parse_hotkey("alt+f1").vk == 0x70 and parse_hotkey("win+r").vk == ord("R")
    assert parse_hotkey("ctrl+f12").vk == 0x7B and parse_hotkey("ctrl+7").vk == ord("7")


def test_display():
    assert display_hotkey("ctrl+shift+space") == "Ctrl+Shift+Space"
    assert display_hotkey(" f5 + ALT ") == "Alt+F5"


def test_message_filter():
    assert is_hotkey_message(WM_HOTKEY, 7, 7)
    assert not is_hotkey_message(WM_HOTKEY, 8, 7) and not is_hotkey_message(0x0100, 7, 7)


@pytest.mark.desktop
def test_registered_hotkey_emits_and_blocks_a_second_registration(qapp):
    import ctypes

    from respondoia.hotkey import GlobalHotkey, HotkeyRegistrationError

    hotkey = parse_hotkey("ctrl+alt+shift+f11")  # unlikely to be taken on a dev machine
    first, second = GlobalHotkey(qapp), GlobalHotkey(qapp)
    hits = []
    first.triggered.connect(lambda: hits.append(1))
    first.register(hotkey)
    try:
        assert first.registered
        with pytest.raises(HotkeyRegistrationError):
            second.register(hotkey)
        assert not second.registered
        # What Windows posts when the keys are pressed.
        thread = ctypes.windll.kernel32.GetCurrentThreadId()
        ctypes.windll.user32.PostThreadMessageW(thread, WM_HOTKEY, 1, 0)
        qapp.processEvents()
        assert hits == [1]
    finally:
        first.unregister()
    assert not first.registered
    second.register(hotkey)  # free again after unregister
    second.unregister()


def test_text_from_keys():
    from PySide6.QtCore import Qt

    from respondoia.hotkey import text_from_keys

    ctrl, alt, shift, meta = (
        Qt.KeyboardModifier.ControlModifier,
        Qt.KeyboardModifier.AltModifier,
        Qt.KeyboardModifier.ShiftModifier,
        Qt.KeyboardModifier.MetaModifier,
    )
    assert text_from_keys(ctrl | shift, Qt.Key.Key_Space) == "ctrl+shift+space"
    assert text_from_keys(alt, Qt.Key.Key_F5) == "alt+f5"
    assert text_from_keys(meta, Qt.Key.Key_R) == "win+r"
    assert text_from_keys(ctrl | alt, Qt.Key.Key_7) == "ctrl+alt+7"
    assert text_from_keys(ctrl, Qt.Key.Key_Control) is None  # still holding modifiers
    assert text_from_keys(Qt.KeyboardModifier.NoModifier, Qt.Key.Key_A) is None
    assert text_from_keys(ctrl, Qt.Key.Key_Return) is None  # not an accepted key


@pytest.mark.desktop
def test_failed_change_keeps_the_old_hotkey(qapp):
    import subprocess
    import sys

    from respondoia.hotkey import GlobalHotkey, HotkeyRegistrationError

    # Another program holds ctrl+alt+shift+f10 (0x79) while the test runs.
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import ctypes, time; "
                "ok = ctypes.windll.user32.RegisterHotKey(None, 1, 0x4007, 0x79); "
                "print('held' if ok else 'failed', flush=True); "
                "time.sleep(30)"
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    first = GlobalHotkey(qapp)
    old, taken = parse_hotkey("ctrl+alt+shift+f11"), parse_hotkey("ctrl+alt+shift+f10")
    try:
        assert holder.stdout.readline().strip() == "held"
        first.register(old)
        with pytest.raises(HotkeyRegistrationError):
            first.replace(taken)
        assert first.registered and first.current == old
        holder.kill()
        holder.wait(5)
        first.replace(taken)
        assert first.registered and first.current == taken
    finally:
        first.unregister()
        holder.kill()
