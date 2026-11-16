"""Plain data shared by the modules. No Qt and no Win32 here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    rect: Rect
    minimized: bool


@dataclass(frozen=True)
class OpenChat:
    title: str  # name at the top of the open chat, as displayed
    bounds: Rect  # open chat area in screen pixels


@dataclass(frozen=True)
class ChatRef:
    hwnd: int
    title: str
