"""Global hotkey: register, change and validate."""

import ctypes
import ctypes.wintypes
from dataclasses import dataclass

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312

_HOTKEY_ID = 1
# In display order.
_MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {
    "space": 0x20,
    **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(digit): ord(str(digit)) for digit in range(10)},
    **{f"f{n}": 0x70 + n - 1 for n in range(1, 13)},
}


@dataclass(frozen=True)
class Hotkey:
    modifiers: int  # MOD_* flags, without MOD_NOREPEAT
    vk: int


class HotkeyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # "empty", "no_modifier" or "unknown_key"


def _split(text: str) -> tuple[list[str], str]:
    """Return the modifier names and the single final key of a hotkey text."""
    if not text.strip():
        raise HotkeyError("empty")
    tokens = [token.strip().lower() for token in text.split("+")]
    modifiers = [name for name in _MODIFIERS if name in tokens]
    keys = [token for token in tokens if token not in _MODIFIERS]
    if len(keys) != 1 or keys[0] not in _KEYS:
        raise HotkeyError("unknown_key")
    if not modifiers:
        raise HotkeyError("no_modifier")
    return modifiers, keys[0]


def parse_hotkey(text: str) -> Hotkey:
    modifiers, key = _split(text)
    flags = 0
    for name in modifiers:
        flags |= _MODIFIERS[name]
    return Hotkey(flags, _KEYS[key])


def display_hotkey(text: str) -> str:
    modifiers, key = _split(text)
    return "+".join(name.capitalize() for name in [*modifiers, key])


def is_hotkey_message(message: int, wparam: int, hotkey_id: int) -> bool:
    return message == WM_HOTKEY and wparam == hotkey_id


class HotkeyRegistrationError(RuntimeError):
    pass


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if is_hotkey_message(msg.message, msg.wParam, _HOTKEY_ID) and self._on_hotkey():
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """One system-wide hotkey. Register and unregister on the interface thread."""

    triggered = Signal()

    def __init__(self, app: QCoreApplication) -> None:
        super().__init__()
        self._registered = False
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._filter = _Filter(self._on_hotkey)
        app.installNativeEventFilter(self._filter)

    def _on_hotkey(self) -> bool:
        # An object with nothing registered leaves the message for other filters.
        if self._registered:
            self.triggered.emit()
        return self._registered

    def register(self, hotkey: Hotkey) -> None:
        self.unregister()
        flags = hotkey.modifiers | MOD_NOREPEAT
        # No window handle: the message goes to the queue of the calling thread.
        if not self._user32.RegisterHotKey(None, _HOTKEY_ID, flags, hotkey.vk):
            raise HotkeyRegistrationError(f"hotkey in use (error {ctypes.get_last_error()})")
        self._registered = True

    def unregister(self) -> None:
        if self._registered:
            self._user32.UnregisterHotKey(None, _HOTKEY_ID)
            self._registered = False

    @property
    def registered(self) -> bool:
        return self._registered
