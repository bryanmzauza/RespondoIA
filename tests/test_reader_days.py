"""Days from the separators between messages, the bubble rectangle and the header picture.

Structure from the 0.4 probe: the message list holds groups of rows and, between them,
separator groups with one centered text; a floating separator above the list gives the
day of the topmost messages. Texts and names invented.
"""

from datetime import date

import pytest
from PIL import Image, ImageDraw

from respondoia import reader
from respondoia.capture import avatar_hash
from respondoia.domain import Rect
from respondoia.reader import find_open_chat, node_from_dict, parse_day, read_lines

TODAY = date(2026, 11, 17)  # a Tuesday


def node(kind, name="", rect=(0, 0, 0, 0), role="", children=()):
    return {
        "control_type": kind,
        "name": name,
        "automation_id": "",
        "aria_role": role,
        "rect": list(rect),
        "children": list(children),
    }


def row(top, text, author="contact"):
    left, right = (462, 700) if author == "contact" else (1600, 1853)
    bubble = node(
        "Group",
        "",
        (left, top, right, top + 35),
        "group",
        [
            node(
                "Group",
                "",
                (left + 9, top + 6, right - 40, top + 27),
                "group",
                [node("Text", text, (left + 9, top + 7, right - 60, top + 25), "description")],
            ),
            node("Text", "10:00", (right - 35, top + 18, right - 5, top + 32), "description"),
        ],
    )
    inner = node("Group", "", (444, top, 1910, top + 37), "group", [bubble])
    return node("DataItem", "", (444, top, 1910, top + 37), "row", [inner])


def separator(top, text):
    label = node("Text", text, (1145, top + 4, 1209, top + 19), "description")
    box = node("Group", "", (1133, top, 1221, top + 25), "group", [label])
    return node("Group", "", (444, top - 6, 1910, top + 31), "group", [box])


def panel(*children, floating=None):
    header = node(
        "Group",
        "",
        (444, 38, 1920, 103),
        "banner",
        [node("Text", "Lívia Prado", (520, 50, 640, 70), "description")],
    )
    items = list(children)
    top = [floating] if floating is not None else []
    body = node(
        "Group",
        "",
        (444, 103, 1920, 968),
        "group",
        [*top, node("Group", "", (454, 103, 1920, 968), "group", items)],
    )
    footer = node("Group", "", (444, 968, 1920, 1032), "contentinfo")
    main = node("Group", "", (444, 38, 1920, 1032), "group", [header, body, footer])
    main["automation_id"] = "main"
    return node_from_dict(main)


def days(*children, floating=None):
    return [
        (line.text, line.day) for line in read_lines(panel(*children, floating=floating), TODAY)
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hoje", "2026-11-17"),
        ("HOJE", "2026-11-17"),
        ("Today", "2026-11-17"),
        ("Ontem", "2026-11-16"),
        ("Yesterday", "2026-11-16"),
        ("ayer", "2026-11-16"),
        ("domingo", "2026-11-15"),
        ("Sexta-feira", "2026-11-13"),
        ("Thursday", "2026-11-12"),
        ("12/11/2026", "2026-11-12"),
        ("12.11.2026", "2026-11-12"),
        ("25/10/2026", "2026-10-25"),
        ("12 de novembro de 2026", "2026-11-12"),
        ("November 12, 2026", "2026-11-12"),
        ("12 November 2026", "2026-11-12"),
        ("Mensagens não lidas", None),
        ("31/02/2026", None),
        ("", None),
    ],
)
def test_parse_day(text, expected, monkeypatch):
    monkeypatch.setattr(reader, "system_month_first", lambda: False)
    assert parse_day(text, TODAY) == expected


def test_us_order_when_the_system_is_month_first(monkeypatch):
    monkeypatch.setattr(reader, "system_month_first", lambda: True)
    assert parse_day("11/12/2026", TODAY) == "2026-11-12"
    assert parse_day("25/10/2026", TODAY) == "2026-10-25"  # 25 cannot be a month


def test_day_separators_date_the_lines_below():
    found = days(
        row(130, "primeira"),
        separator(180, "Ontem"),
        row(220, "segunda"),
        row(260, "terceira", "user"),
        separator(310, "Hoje"),
        row(350, "quarta"),
    )
    assert found == [
        ("primeira", ""),
        ("segunda", "2026-11-16"),
        ("terceira", "2026-11-16"),
        ("quarta", "2026-11-17"),
    ]


def test_the_floating_date_dates_the_topmost_lines():
    floating = node("Group", "", (444, 103, 1920, 136), "group", [separator(110, "15/11/2026")])
    found = days(
        row(130, "primeira"), separator(180, "Hoje"), row(220, "segunda"), floating=floating
    )
    assert found == [("primeira", "2026-11-15"), ("segunda", "2026-11-17")]


def test_english_separators_give_the_same_days():
    pt = days(separator(100, "Ontem"), row(130, "a"), separator(180, "Hoje"), row(220, "b"))
    en = days(separator(100, "Yesterday"), row(130, "a"), separator(180, "Today"), row(220, "b"))
    assert pt == en


def test_a_text_that_is_not_a_day_changes_nothing():
    found = days(
        separator(100, "Hoje"),
        row(130, "a"),
        separator(180, "3 mensagens não lidas"),
        row(220, "b"),
    )
    assert found == [("a", "2026-11-17"), ("b", "2026-11-17")]


def test_bubble_rect_is_kept():
    (line,) = read_lines(panel(row(130, "a")), TODAY)
    assert line.rect == Rect(462, 130, 700, 165)


def test_header_picture_rect():
    tree = panel(row(130, "a"))
    assert find_open_chat(tree).avatar is None
    data = {
        "control_type": "Group",
        "name": "",
        "automation_id": "main",
        "aria_role": "group",
        "rect": [444, 38, 1920, 1032],
        "children": [
            node(
                "Group",
                "",
                (444, 38, 1920, 103),
                "banner",
                [
                    node("Image", "foto", (605, 59, 626, 80), "img"),
                    node("Text", "Lívia Prado", (640, 50, 760, 70), "description"),
                ],
            )
        ],
    }
    chat = find_open_chat(node_from_dict(data))
    assert chat.title == "Lívia Prado" and chat.avatar == Rect(605, 59, 626, 80)


def picture(seed: int) -> Image.Image:
    image = Image.new("RGB", (200, 200), "white")
    draw = ImageDraw.Draw(image)
    for i in range(40):
        colour = ((seed * 37 + i * 53) % 256, (seed * 11 + i * 29) % 256, (i * 71) % 256)
        x, y = (seed * 13 + i * 17) % 180, (seed * 7 + i * 23) % 180
        draw.ellipse((x, y, x + 25, y + 25), fill=colour)
    return image


WINDOW = Rect(0, 0, 200, 200)
AVATAR = Rect(10, 10, 31, 31)


def test_default_picture_has_no_hash():
    flat = Image.new("RGB", (200, 200), (200, 200, 200))
    ImageDraw.Draw(flat).ellipse((12, 12, 28, 28), fill=(120, 120, 120))
    assert avatar_hash(flat, WINDOW, AVATAR) == ""


def test_photo_hash_is_stable_and_differs():
    first = avatar_hash(picture(1), WINDOW, Rect(0, 0, 200, 200))
    assert len(first) == 16 and first == avatar_hash(picture(1), WINDOW, Rect(0, 0, 200, 200))
    assert first != avatar_hash(picture(2), WINDOW, Rect(0, 0, 200, 200))
