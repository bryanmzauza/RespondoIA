"""Find the WhatsApp window and capture its content, cropped to the open chat."""

import io
from collections.abc import Sequence

from PIL import Image

from respondoia.domain import Look, Rect, WindowInfo

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


AVATAR_COLOURS = 64  # fewer colours than this: a drawn default picture, not a photo


FLAT_COLOURS = 8  # this many colours or fewer: nothing drawn yet, not a picture


def dhash(crop: Image.Image) -> str:
    """A 64-bit difference hash, 16 hex digits. It cannot be turned back into the image."""
    gray = crop.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = gray.load()
    value = 0
    for y in range(8):
        for x in range(8):
            value = value << 1 | (pixels[x, y] > pixels[x + 1, y])
    return f"{value:016x}"


def _window_crop(image: Image.Image, window: Rect, rect: Rect) -> Image.Image:
    box = (
        rect.left - window.left,
        rect.top - window.top,
        rect.right - window.left,
        rect.bottom - window.top,
    )
    return image.crop(box).convert("RGB")


def _colours(crop: Image.Image) -> int:
    found = crop.resize((32, 32), Image.Resampling.BILINEAR).getcolors(maxcolors=32 * 32)
    return 32 * 32 + 1 if found is None else len(found)


def avatar_hash(image: Image.Image, window: Rect, avatar: Rect) -> str:
    """A 64-bit difference hash of the contact's photo, "" for a default picture. The
    pixels stay in memory; only the hash is kept."""
    crop = _window_crop(image, window, avatar)
    if crop.width < 8 or crop.height < 8 or _colours(crop) < AVATAR_COLOURS:
        return ""
    return dhash(crop)


def look_hash(image: Image.Image, window: Rect, rect: Rect) -> str:
    """The `look` of a picture in the chat, "" when it is too small or still blank."""
    crop = _window_crop(image, window, rect)
    if crop.width < 8 or crop.height < 8 or _colours(crop) <= FLAT_COLOURS:
        return ""
    return dhash(crop)


VISIBLE_SHARE = 0.6  # less of the picture's height on screen: it would mislead
PICTURE_MIN_SIDE = 48  # pixels


def encode_jpeg(image: Image.Image, quality: int = 85) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _intersect(a: Rect, b: Rect) -> Rect | None:
    found = Rect(max(a.left, b.left), max(a.top, b.top), min(a.right, b.right), min(a.bottom, b.bottom))
    return found if found.width > 0 and found.height > 0 else None


def picture_crop(
    image: Image.Image, window: Rect, bubble: Rect, area: Rect, max_side: int = 768
) -> bytes | None:
    """The part of a photo inside the message list, as JPEG. None when too little of it
    shows, when it is too small, or when it is still blank."""
    visible = _intersect(bubble, area)
    visible = _intersect(visible, window) if visible else None
    if visible is None or visible.height < VISIBLE_SHARE * bubble.height:
        return None
    if min(visible.width, visible.height) < PICTURE_MIN_SIDE:
        return None
    crop = _window_crop(image, window, visible)
    if _colours(crop) <= FLAT_COLOURS:
        return None
    return encode_jpeg(downscale(crop, max_side))


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


def find_whatsapp_window(allowed: Sequence[str]) -> WindowInfo | None:
    """The first visible top-level window that passes the allowed list, or None."""
    import win32gui

    found: list[WindowInfo] = []

    def visit(hwnd, _):
        if not found and win32gui.IsWindowVisible(hwnd):
            info = window_info(hwnd)
            if info is not None and info.title and is_allowed_window(info, allowed):
                found.append(info)
        return True

    win32gui.EnumWindows(visit, None)
    return found[0] if found else None


def grab_window(hwnd: int) -> Image.Image:
    """Render the window's own content to an in-memory image. Nothing touches the disk."""
    import ctypes
    from contextlib import suppress

    import pywintypes
    import win32gui
    import win32ui

    info = window_info(hwnd)
    if info is None or info.minimized or info.rect.width <= 0 or info.rect.height <= 0:
        raise CaptureError("window is gone, minimized or empty")
    width, height = info.rect.width, info.rect.height
    window_dc = source = memory = bitmap = None
    try:
        window_dc = win32gui.GetWindowDC(hwnd)
        source = win32ui.CreateDCFromHandle(window_dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        memory.SelectObject(bitmap)
        # PrintWindow asks the window to draw itself, so windows on top of it are left out.
        drawn = ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), PW_RENDERFULLCONTENT)
        bits = bitmap.GetBitmapBits(True)
    except (win32ui.error, pywintypes.error) as exc:  # the window went away, or GDI failed
        raise CaptureError("could not capture the window") from exc
    finally:
        # Release only what was created, whatever failed.
        with suppress(Exception):
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        with suppress(Exception):
            if memory is not None:
                memory.DeleteDC()
        with suppress(Exception):
            if source is not None:
                source.DeleteDC()
        with suppress(Exception):
            if window_dc is not None:
                win32gui.ReleaseDC(hwnd, window_dc)
    if not drawn:
        raise CaptureError("PrintWindow failed")
    try:
        image = Image.frombuffer("RGB", (width, height), bits, "raw", "BGRX", 0, 1)
    except ValueError as exc:  # a display mode other than 32 bits per pixel
        raise CaptureError("unexpected bitmap format") from exc
    if is_blank(image):
        raise CaptureError("capture came back blank")
    return image


def avatar_from_window(hwnd: int, avatar: Rect | None) -> str:
    """The photo hash of the open chat, "" without a photo or when the capture fails."""
    info = window_info(hwnd)
    if avatar is None or info is None:
        return ""
    try:
        return avatar_hash(grab_window(hwnd), info.rect, avatar)
    except CaptureError:
        return ""


def looks_from_window(hwnd: int, rects: Sequence[Rect]) -> tuple[str, ...]:
    """The `look` of each picture, from one capture; "" for all when the capture fails."""
    info = window_info(hwnd)
    if info is None or not rects:
        return ("",) * len(rects)
    try:
        image = grab_window(hwnd)
    except CaptureError:
        return ("",) * len(rects)
    return tuple(look_hash(image, info.rect, rect) for rect in rects)


def look_from_window(
    hwnd: int,
    avatar: Rect | None,
    crops: Sequence[Rect],
    looks: Sequence[Rect],
    area: Rect | None,
) -> Look:
    """One capture for the hotkey: the contact's photo hash, the photos to send and the
    hashes for the history. A failed capture gives an empty result, never an error."""
    empty = Look("", (None,) * len(crops), ("",) * len(looks))
    info = window_info(hwnd)
    if info is None:
        return empty
    try:
        image = grab_window(hwnd)
    except CaptureError:
        return empty
    return Look(
        avatar_hash(image, info.rect, avatar) if avatar is not None else "",
        tuple(
            picture_crop(image, info.rect, rect, area) if area is not None else None
            for rect in crops
        ),
        tuple(look_hash(image, info.rect, rect) for rect in looks),
    )


def capture_chat(hwnd: int, chat_bounds: Rect, max_side: int) -> bytes:
    info = window_info(hwnd)
    if info is None:
        raise CaptureError("window is gone")
    return prepare_image(grab_window(hwnd), info.rect, chat_bounds, max_side)
