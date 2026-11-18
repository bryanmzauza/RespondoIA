"""Win32 side of the history loader: scrolling the message list through UI Automation
(no input events at all), mouse wheel events as the fallback, the cursor, and the guard
that tells the app's own wheel events apart from the user's hand.

The guard listens to raw input from every mouse and keyboard while a load runs. Windows
reports input made by SendInput with no device handle; anything with a device is a person.
"""

import ctypes
import ctypes.wintypes as wt
import threading
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject
from PySide6.QtWidgets import QWidget

MARK = 0x52494F41  # dwExtraInfo of the app's own events
WHEEL_DELTA = 120
_INPUT_MOUSE = 0
_MOUSEEVENTF_WHEEL = 0x0800
WM_INPUT = 0x00FF
_RID_HEADER = 0x10000005
_RIDEV_REMOVE = 0x00000001
_RIDEV_INPUTSINK = 0x00000100
_USAGE_PAGE_GENERIC = 0x01
_USAGE_MOUSE = 0x02
_USAGE_KEYBOARD = 0x06


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardInput(ctypes.Structure):  # only here to give the union its real size
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", _InputUnion)]


class _RawInputDevice(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wt.USHORT),
        ("usUsage", wt.USHORT),
        ("dwFlags", wt.DWORD),
        ("hwndTarget", wt.HWND),
    ]


class _RawInputHeader(ctypes.Structure):
    _fields_ = [
        ("dwType", wt.DWORD),
        ("dwSize", wt.DWORD),
        ("hDevice", wt.HANDLE),
        ("wParam", wt.WPARAM),
    ]


class AccessibleScroll:
    """Scrolls the message list through UI Automation, as screen readers do: no mouse or
    keyboard event, no cursor, and WhatsApp need not be in front (only not minimized)."""

    def __init__(self, pattern, sleep=time.sleep) -> None:
        self._pattern = pattern  # IUIAutomationScrollPattern of the message list
        self._sleep = sleep

    @classmethod
    def find(cls, hwnd: int) -> "AccessibleScroll | None":
        """The tallest vertically scrollable element in the open chat, or None."""
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx()
        except OSError:
            pass
        try:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia_types

            uia = comtypes.client.CreateObject(
                uia_types.CUIAutomation, interface=uia_types.IUIAutomation
            )
            cache = uia.CreateCacheRequest()
            cache.TreeFilter = uia.RawViewCondition
            panel = uia.ElementFromHandle(hwnd).FindFirstBuildCache(
                4, uia.CreatePropertyCondition(30011, "main"), cache  # descendants, AutomationId
            )
            if not panel:
                return None
            found = panel.FindAll(4, uia.CreatePropertyCondition(30034, True))  # scrollable
            best, height = None, 0
            for index in range(found.Length if found else 0):
                element = found.GetElement(index)
                pattern = element.GetCurrentPattern(10004).QueryInterface(  # ScrollPattern
                    uia_types.IUIAutomationScrollPattern
                )
                rect = element.CurrentBoundingRectangle
                if pattern.CurrentVerticallyScrollable and rect.bottom - rect.top > height:
                    best, height = pattern, rect.bottom - rect.top
            return cls(best) if best is not None else None
        except (comtypes.COMError, OSError, ValueError):
            return None

    def up(self, screens: float) -> None:
        """Towards older messages by this many screens (fractions too)."""
        view = self._pattern.CurrentVerticalViewSize
        here = self._pattern.CurrentVerticalScrollPercent
        step = 100.0 if view >= 100 else 100.0 * screens * view / (100.0 - view)
        self._pattern.SetScrollPercent(-1, max(0.0, here - step))

    def bottom(self, tries: int = 20) -> None:
        """Back to the newest messages. The list grows while it loads, so check and repeat."""
        for _ in range(tries):
            self._pattern.SetScrollPercent(-1, 100)
            if self._pattern.CurrentVerticalScrollPercent >= 99.9:
                return
            self._sleep(0.15)


def wheel_input(notches: int) -> _Input:
    """One wheel event. Positive notches scroll up, towards older messages."""
    event = _Input()
    event.type = _INPUT_MOUSE
    event.union.mi = _MouseInput(
        0, 0, (WHEEL_DELTA * notches) & 0xFFFFFFFF, _MOUSEEVENTF_WHEEL, 0, MARK
    )
    return event


def wheel(notches: int) -> None:
    event = wheel_input(notches)
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))


def cursor_pos() -> tuple[int, int]:
    point = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def put_cursor(x: int, y: int) -> None:
    """Physical pixels: the process is DPI aware per monitor."""
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def minimized(hwnd: int) -> bool:
    return bool(ctypes.windll.user32.IsIconic(hwnd))


def foreground_window() -> int:
    return ctypes.windll.user32.GetForegroundWindow() or 0


def window_under_cursor(hwnd: int) -> bool:
    """True when the window under the cursor belongs to hwnd, so the wheel reaches it."""
    x, y = cursor_pos()
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.restype = wt.HWND
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    found = user32.WindowFromPoint(wt.POINT(x, y))
    root = user32.GetAncestor(found, 2) if found else None  # GA_ROOT
    return bool(root) and int(root) == int(hwnd)


def raw_device(lparam: int) -> int | None:
    """Device handle of a WM_INPUT message: 0 for SendInput, None if it cannot be read."""
    header = _RawInputHeader()
    size = wt.UINT(ctypes.sizeof(_RawInputHeader))
    got = ctypes.windll.user32.GetRawInputData(
        wt.HANDLE(lparam),
        _RID_HEADER,
        ctypes.byref(header),
        ctypes.byref(size),
        ctypes.sizeof(_RawInputHeader),
    )
    if got in (0, 0xFFFFFFFF, -1):
        return None
    return header.hDevice or 0


def _register(hwnd: int | None, remove: bool) -> bool:
    flags = _RIDEV_REMOVE if remove else _RIDEV_INPUTSINK
    target = None if remove else hwnd
    devices = (_RawInputDevice * 2)(
        _RawInputDevice(_USAGE_PAGE_GENERIC, _USAGE_MOUSE, flags, target),
        _RawInputDevice(_USAGE_PAGE_GENERIC, _USAGE_KEYBOARD, flags, target),
    )
    return bool(
        ctypes.windll.user32.RegisterRawInputDevices(
            devices, 2, ctypes.sizeof(_RawInputDevice)
        )
    )


class _RawFilter(QAbstractNativeEventFilter):
    def __init__(self, guard: "InputGuard") -> None:
        super().__init__()
        self._guard = guard

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == WM_INPUT and msg.hWnd == self._guard.hwnd:
                self._guard.on_device(raw_device(msg.lParam))
        return False, 0


class InputGuard(QObject):
    """Set when a person touches the mouse or keyboard while armed. Arm and disarm on the
    interface thread; stopped() can be read from any thread."""

    def __init__(self, app: QCoreApplication | None = None) -> None:
        super().__init__()
        self._widget = QWidget()
        self.hwnd = int(self._widget.winId())  # a native window to receive raw input
        self._stop = threading.Event()
        self._paused = threading.Event()  # the tray's pause, apart from a touch
        self._armed = False
        self._from = 0.0
        self.injected = 0  # counts, for the probe and the log
        self.real = 0
        self._filter = _RawFilter(self)
        (app or QCoreApplication.instance()).installNativeEventFilter(self._filter)

    def arm(self, delay_s: float = 0.3) -> bool:
        """Start listening. The delay lets the click that started the load settle."""
        self._stop.clear()
        self._paused.clear()
        self.injected = self.real = 0
        self._from = time.monotonic() + delay_s
        self._armed = _register(self.hwnd, remove=False)
        return self._armed

    def disarm(self) -> None:
        if self._armed:
            _register(None, remove=True)
        self._armed = False

    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._paused.set()
        self._stop.set()

    def paused(self) -> bool:
        """Only the tray's pause: a background load ignores the user's mouse and keys."""
        return self._paused.is_set()

    def on_device(self, device: int | None) -> None:
        if not self._armed:
            return
        if device == 0:
            self.injected += 1
            return
        self.real += 1
        # An unreadable message counts as a person: stopping is the safe side.
        if time.monotonic() >= self._from:
            self._stop.set()
