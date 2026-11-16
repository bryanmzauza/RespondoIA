"""Find the WhatsApp window and capture its content, cropped to the open chat."""

import io
from collections.abc import Sequence

from PIL import Image

from respondoia.domain import Rect, WindowInfo

MIN_CHAT_SIDE = 200
PW_RENDERFULLCONTENT = 2


class CaptureError(RuntimeError):
    pass


def is_allowed_window(info: WindowInfo, allowed: Sequence[str]) -> bool:
    title, exe = info.title.casefold(), info.exe.casefold()
    return any(item.casefold() in title or item.casefold() in exe for item in allowed)


def crop_box(
    window: Rect, image_size: tuple[int, int], chat_bounds: Rect
) -> tuple[int, int, int, int]:
    """Chat bounds in screen pixels -> crop box in image pixels, clamped to the image."""
    width, height = image_size
    left = max(0, chat_bounds.left - window.left)
    top = max(0, chat_bounds.top - window.top)
    right = min(width, chat_bounds.right - window.left)
    bottom = min(height, chat_bounds.bottom - window.top)
    if right - left < MIN_CHAT_SIDE or bottom - top < MIN_CHAT_SIDE:
        raise CaptureError("chat area is too small or outside the window")
    return left, top, right, bottom


def downscale(image: Image.Image, max_side: int) -> Image.Image:
    longer = max(image.size)
    if longer <= max_side:
        return image
    ratio = max_side / longer
    size = (round(image.width * ratio), round(image.height * ratio))
    return image.resize(size, Image.Resampling.LANCZOS)


def encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def is_blank(image: Image.Image) -> bool:
    """True for a capture that came back as one flat colour, as protected windows do."""
    thumb = image.resize((64, 64), Image.Resampling.NEAREST)
    colors = thumb.getcolors(maxcolors=64 * 64)
    return len(colors) <= 8


def prepare_image(image: Image.Image, window: Rect, chat_bounds: Rect, max_side: int) -> bytes:
    cropped = image.crop(crop_box(window, image.size, chat_bounds))
    return encode_png(downscale(cropped, max_side))


# The functions below call Win32. pywin32 is imported inside them so that modules
# which only need the pure helpers above do not load it.


def _exe_name(hwnd: int) -> str:
    import ctypes
    import ctypes.wintypes as wt
    import os

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.LPWSTR,
        ctypes.POINTER(wt.DWORD),
    ]
    pid = wt.DWORD()
    ctypes.WinDLL("user32").GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # query limited information
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(process)


def window_info(hwnd: int) -> WindowInfo | None:
    """Describe a window, or return None if it no longer exists."""
    import win32gui

    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    return WindowInfo(
        hwnd=hwnd,
        title=win32gui.GetWindowText(hwnd),
        exe=_exe_name(hwnd),
        rect=Rect(*win32gui.GetWindowRect(hwnd)),
        minimized=bool(win32gui.IsIconic(hwnd)),
    )


def foreground_window() -> WindowInfo | None:
    import win32gui

    return window_info(win32gui.GetForegroundWindow())


def grab_window(hwnd: int) -> Image.Image:
    """Render the window's own content to an in-memory image. Nothing touches the disk."""
    import ctypes

    import win32gui
    import win32ui

    info = window_info(hwnd)
    if info is None or info.minimized or info.rect.width <= 0 or info.rect.height <= 0:
        raise CaptureError("window is gone, minimized or empty")
    width, height = info.rect.width, info.rect.height
    window_dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(window_dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(source, width, height)
        memory.SelectObject(bitmap)
        # PrintWindow asks the window to draw itself, so windows on top of it are left out.
        drawn = ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), PW_RENDERFULLCONTENT)
        bits = bitmap.GetBitmapBits(True)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
    if not drawn:
        raise CaptureError("PrintWindow failed")
    image = Image.frombuffer("RGB", (width, height), bits, "raw", "BGRX", 0, 1)
    if is_blank(image):
        raise CaptureError("capture came back blank")
    return image


def capture_chat(hwnd: int, chat_bounds: Rect, max_side: int) -> bytes:
    info = window_info(hwnd)
    if info is None:
        raise CaptureError("window is gone")
    return prepare_image(grab_window(hwnd), info.rect, chat_bounds, max_side)
