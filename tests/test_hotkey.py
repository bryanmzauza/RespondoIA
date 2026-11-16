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
