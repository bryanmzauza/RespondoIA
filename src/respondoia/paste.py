"""Give focus back to WhatsApp and insert the chosen text. Never presses Enter."""

import time
from contextlib import contextmanager

VK_CONTROL, VK_V = 0x11, 0x56
_VK_SHIFT, _VK_ALT, _VK_LWIN, _VK_RWIN = 0x10, 0x12, 0x5B, 0x5C
_CF_UNICODETEXT = 13
_KEYEVENTF_KEYUP = 0x2
_INPUT_KEYBOARD = 1
# Registered clipboard formats that keep the text out of Win+V and of cloud sync.
_EXCLUDE = "ExcludeClipboardContentFromMonitorProcessing"
_NO_HISTORY = "CanIncludeInClipboardHistory"
_NO_CLOUD = "CanUploadToCloudClipboard"


def prepare_text(text: str) -> str:
    lines = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    return lines.replace("\n", "\r\n")


def ctrl_v_events() -> list[tuple[int, bool]]:
    """(virtual key, is_key_up). The only key sequence this app ever sends."""
    return [(VK_CONTROL, False), (VK_V, False), (VK_V, True), (VK_CONTROL, True)]


@contextmanager
def _clipboard():
    import pywintypes
    import win32clipboard

    for attempt in range(10):
        try:
            win32clipboard.OpenClipboard()
            break
        except pywintypes.error:
            if attempt == 9:
                raise
            time.sleep(0.02)  # another program is holding it for a moment
    try:
        yield win32clipboard
    finally:
        win32clipboard.CloseClipboard()


def copy_text(text: str) -> None:
    """Put text on the clipboard, marked private. Does not paste."""
    zero = (0).to_bytes(4, "little")
    with _clipboard() as clip:
        clip.EmptyClipboard()
        clip.SetClipboardData(_CF_UNICODETEXT, text)
        clip.SetClipboardData(clip.RegisterClipboardFormat(_EXCLUDE), zero)
        clip.SetClipboardData(clip.RegisterClipboardFormat(_NO_HISTORY), zero)
        clip.SetClipboardData(clip.RegisterClipboardFormat(_NO_CLOUD), zero)


def snapshot_clipboard() -> list[tuple[int, bytes]]:
    """Every format that can be read as bytes or text. Handle-only formats are skipped."""
    import pywintypes

    saved = []
    with _clipboard() as clip:
        fmt = clip.EnumClipboardFormats(0)
        while fmt:
            try:
                data = clip.GetClipboardData(fmt)
            except (pywintypes.error, TypeError):
                data = None
            if isinstance(data, str):
                data = data.encode("utf-16-le")
            if isinstance(data, bytes):
                saved.append((fmt, data))
            fmt = clip.EnumClipboardFormats(fmt)
    return saved


def restore_clipboard(snapshot: list[tuple[int, bytes]]) -> None:
    import pywintypes

    with _clipboard() as clip:
        clip.EmptyClipboard()
        for fmt, data in snapshot:
            value = data.decode("utf-16-le") if fmt == _CF_UNICODETEXT else data
            try:
                clip.SetClipboardData(fmt, value)
            except (pywintypes.error, TypeError):
                continue


def focus_window(hwnd: int, timeout_ms: int = 500) -> bool:
    import pywintypes
    import win32gui

    if not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
        return False
    try:
        win32gui.SetForegroundWindow(hwnd)
    except pywintypes.error:
        return False
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.01)
    return False


def _foreground_window() -> int:
    import win32gui

    return win32gui.GetForegroundWindow()


def _modifiers_released(timeout_ms: int) -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        keys = (VK_CONTROL, _VK_SHIFT, _VK_ALT, _VK_LWIN, _VK_RWIN)
        if not any(user32.GetAsyncKeyState(key) & 0x8000 for key in keys):
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)


def _send_keys(events: list[tuple[int, bool]]) -> int:
    """Return how many events Windows accepted."""
    import ctypes
    import ctypes.wintypes as wt

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("wVk", wt.WORD),
            ("wScan", wt.WORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MouseInput(ctypes.Structure):  # only here to give the union its real size
        _fields_ = [
            ("dx", wt.LONG),
            ("dy", wt.LONG),
            ("mouseData", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [("ki", KeyboardInput), ("mi", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("union", InputUnion)]

    inputs = (Input * len(events))()
    for slot, (key, is_up) in zip(inputs, events):
        slot.type = _INPUT_KEYBOARD
        slot.union.ki = KeyboardInput(key, 0, _KEYEVENTF_KEYUP if is_up else 0, 0, 0)
    return ctypes.windll.user32.SendInput(len(events), inputs, ctypes.sizeof(Input))


def insert_text(hwnd: int, text: str) -> bool:
    """Paste text into the window. On False the text is left on the clipboard instead."""
    saved = snapshot_clipboard()
    copy_text(prepare_text(text))
    if not focus_window(hwnd) or not _modifiers_released(500):
        return False
    # Focus can move while the user is still releasing keys: check right before pasting.
    if _foreground_window() != hwnd:
        return False
    events = ctrl_v_events()
    if _send_keys(events) != len(events):
        return False
    time.sleep(0.4)  # let the target read the clipboard before it is put back
    restore_clipboard(saved)
    return True
