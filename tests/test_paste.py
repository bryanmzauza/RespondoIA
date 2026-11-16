from pathlib import Path

import pytest

from respondoia import paste
from respondoia.paste import (
    copy_text,
    ctrl_v_events,
    prepare_text,
    restore_clipboard,
    snapshot_clipboard,
)


def test_prepare_text_trims_and_uses_crlf():
    assert prepare_text("  Oi\nTudo bem?\n\n") == "Oi\r\nTudo bem?"
    assert prepare_text("a\r\nb") == "a\r\nb"
    assert prepare_text("a\rb\n\nc") == "a\r\nb\r\n\r\nc"


def test_prepare_text_keeps_emoji():
    assert prepare_text("Combinado 😀👍") == "Combinado 😀👍"


def test_only_ctrl_v_is_ever_sent():
    assert ctrl_v_events() == [(0x11, False), (0x56, False), (0x56, True), (0x11, True)]


def test_enter_is_never_referenced():
    source = Path(paste.__file__).read_text(encoding="utf-8")
    assert "VK_RETURN" not in source and "0X0D" not in source.upper()


def read_clipboard_text():
    import win32clipboard

    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def has_format(name):
    import win32clipboard

    wanted = win32clipboard.RegisterClipboardFormat(name)
    win32clipboard.OpenClipboard()
    try:
        return bool(win32clipboard.IsClipboardFormatAvailable(wanted))
    finally:
        win32clipboard.CloseClipboard()


@pytest.mark.desktop
def test_clipboard_roundtrip_keeps_emoji_and_lines():
    before = snapshot_clipboard()
    try:
        copy_text(prepare_text("Oi 😀\nlinha 2"))
        assert read_clipboard_text() == "Oi 😀\r\nlinha 2"
        assert has_format("ExcludeClipboardContentFromMonitorProcessing")
        assert has_format("CanIncludeInClipboardHistory")
        assert has_format("CanUploadToCloudClipboard")
    finally:
        restore_clipboard(before)


@pytest.mark.desktop
def test_restore_brings_back_what_was_there():
    before = snapshot_clipboard()
    try:
        copy_text("texto anterior 🌻")
        saved = snapshot_clipboard()
        copy_text("sugestão")
        restore_clipboard(saved)
        assert read_clipboard_text() == "texto anterior 🌻"
    finally:
        restore_clipboard(before)
