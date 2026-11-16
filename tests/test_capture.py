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
