import io

import pytest
from PIL import Image

from respondoia.capture import (
    CaptureError,
    crop_box,
    downscale,
    encode_png,
    is_allowed_window,
    is_blank,
    prepare_image,
)
from respondoia.domain import Rect, WindowInfo


def noise(size):
    return Image.effect_noise(size, 60).convert("RGB")


def test_allowed_by_title_or_process():
    w = WindowInfo(1, "WhatsApp", "WhatsApp.Root.exe", Rect(0, 0, 10, 10), False)
    assert is_allowed_window(w, ["WhatsApp"]) and is_allowed_window(w, ["whatsapp"])
    by_process = WindowInfo(3, "", "WhatsApp.Root.exe", Rect(0, 0, 10, 10), False)
    assert is_allowed_window(by_process, ["WhatsApp"])
    other = WindowInfo(2, "Notes", "notepad.exe", Rect(0, 0, 10, 10), False)
    assert not is_allowed_window(other, ["WhatsApp"])


def test_crop_box_translates_screen_to_image_pixels():
    box = crop_box(Rect(100, 50, 1700, 950), (1600, 900), Rect(600, 50, 1700, 950))
    assert box == (500, 0, 1600, 900)


def test_crop_box_clamps_to_the_image():
    box = crop_box(Rect(0, 0, 1000, 800), (1000, 800), Rect(400, -20, 1200, 900))
    assert box == (400, 0, 1000, 800)


def test_crop_box_rejects_a_tiny_or_outside_area():
    with pytest.raises(CaptureError):
        crop_box(Rect(0, 0, 1000, 800), (1000, 800), Rect(2000, 0, 2500, 800))
    with pytest.raises(CaptureError):
        crop_box(Rect(0, 0, 1000, 800), (1000, 800), Rect(400, 0, 550, 800))


def test_downscale_keeps_ratio_and_never_upscales():
    assert downscale(Image.new("RGB", (2600, 1300)), 1300).size == (1300, 650)
    assert downscale(Image.new("RGB", (800, 600)), 1300).size == (800, 600)


def test_encode_png_round_trips():
    image = noise((40, 30))
    assert Image.open(io.BytesIO(encode_png(image))).size == (40, 30)
    assert encode_png(image).startswith(b"\x89PNG")


def test_prepare_image_excludes_the_sidebar():
    img = Image.new("RGB", (1000, 800), "red")  # sidebar colour
    img.paste(noise((600, 800)), (400, 0))
    png = prepare_image(img, Rect(0, 0, 1000, 800), Rect(400, 0, 1000, 800), 1300)
    out = Image.open(io.BytesIO(png))
    colors = {color for _, color in out.getcolors(maxcolors=out.width * out.height)}
    assert out.size == (600, 800) and (255, 0, 0) not in colors


def test_prepare_image_limits_the_longer_side():
    img = noise((3000, 2000))
    png = prepare_image(img, Rect(0, 0, 3000, 2000), Rect(400, 0, 3000, 2000), 1300)
    assert Image.open(io.BytesIO(png)).size == (1300, 1000)


def test_blank_detection():
    assert is_blank(Image.new("RGB", (500, 500), "black"))
    assert not is_blank(noise((500, 500)))


def test_win32_errors_while_capturing_are_capture_errors(monkeypatch):
    import pywintypes
    import win32gui

    from respondoia import capture

    info = WindowInfo(5, "WhatsApp", "WhatsApp.Root.exe", Rect(0, 0, 800, 600), False)
    monkeypatch.setattr(capture, "window_info", lambda hwnd: info)

    def gone(hwnd):
        raise pywintypes.error(1400, "GetWindowDC", "Invalid window handle.")

    monkeypatch.setattr(win32gui, "GetWindowDC", gone)
    with pytest.raises(CaptureError):
        capture.grab_window(5)


def test_dhash_matches_the_avatar_hash():
    from respondoia.capture import avatar_hash, dhash

    image = noise((200, 200))
    expected = avatar_hash(image, Rect(0, 0, 200, 200), Rect(10, 10, 90, 90))
    assert expected and dhash(image.crop((10, 10, 90, 90))) == expected


def test_look_hash_crops_in_window_pixels():
    from respondoia.capture import dhash, look_hash

    image = noise((300, 300))
    window = Rect(100, 50, 400, 350)
    look = look_hash(image, window, Rect(150, 100, 250, 220))
    assert look == dhash(image.crop((50, 50, 150, 170))) and len(look) == 16


def test_look_hash_of_a_tiny_or_flat_area_is_empty():
    from respondoia.capture import look_hash

    image = Image.new("RGB", (300, 300), "white")
    assert look_hash(image, Rect(0, 0, 300, 300), Rect(10, 10, 15, 15)) == ""
    assert look_hash(image, Rect(0, 0, 300, 300), Rect(10, 10, 200, 200)) == ""


def test_looks_from_window_survive_a_failed_capture(monkeypatch):
    from respondoia import capture

    info = WindowInfo(5, "WhatsApp", "WhatsApp.Root.exe", Rect(0, 0, 800, 600), False)
    monkeypatch.setattr(capture, "window_info", lambda hwnd: info)

    def fail(hwnd):
        raise CaptureError("capture came back blank")

    monkeypatch.setattr(capture, "grab_window", fail)
    assert capture.looks_from_window(5, [Rect(0, 0, 100, 100), Rect(0, 0, 90, 90)]) == ("", "")
    calls = []
    monkeypatch.setattr(capture, "grab_window", lambda hwnd: calls.append(hwnd) or noise((800, 600)))
    looks = capture.looks_from_window(5, [Rect(0, 0, 100, 100), Rect(100, 0, 200, 100)])
    assert len(calls) == 1 and all(len(look) == 16 for look in looks)
