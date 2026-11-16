import json
from pathlib import Path

import pytest

from respondoia.domain import OpenChat, Rect
from respondoia.reader import find_open_chat, node_from_dict

FIXTURES = Path(__file__).parent / "fixtures" / "uia"


def load(name):
    return node_from_dict(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def test_finds_title_and_bounds():
    chat = find_open_chat(load("chat-open-pt.json"))
    assert chat == OpenChat("Marina Duarte", Rect(420, 0, 1600, 900))


def test_interface_language_does_not_matter():
    assert find_open_chat(load("chat-open-en.json")) == find_open_chat(load("chat-open-pt.json"))


def test_group_title_without_member_list():
    assert find_open_chat(load("group-open.json")).title == "Turma do Vôlei"


def test_title_keeps_emoji():
    assert find_open_chat(load("title-with-emoji.json")).title == "Bia 🌻"


def test_status_line_is_not_part_of_the_title():
    assert find_open_chat(load("status-online.json")).title == "Caio Lima"


@pytest.mark.parametrize("name", ["no-chat-open.json", "main-without-title.json"])
def test_returns_none_when_unsure(name):
    assert find_open_chat(load(name)) is None


def test_works_on_the_panel_subtree():
    root = load("chat-open-pt.json")
    panel = next(n for n in walk(root) if n.automation_id == "main")
    assert find_open_chat(panel) == find_open_chat(root)


def test_names_in_the_chat_list_are_never_the_title():
    # The sidebar holds other contacts' names near the top of the window.
    root = load("chat-open-pt.json")
    sidebar_names = {n.name for n in walk(root) if n.rect.right <= 420 and n.name}
    assert "Caio Lima" in sidebar_names
    assert find_open_chat(root).title not in sidebar_names


def test_a_title_of_spaces_is_not_a_title():
    panel = {
        "control_type": "Group",
        "name": "",
        "automation_id": "main",
        "aria_role": "group",
        "rect": [420, 0, 1600, 900],
        "children": [
            {"control_type": "Text", "name": "   ", "rect": [491, 12, 640, 32]},
            {"control_type": "Text", "name": "online", "rect": [491, 34, 640, 50]},
        ],
    }
    assert find_open_chat(node_from_dict(panel)) is None


def test_text_below_the_header_band_is_ignored():
    panel = {
        "control_type": "Group",
        "automation_id": "main",
        "rect": [420, 38, 1600, 900],
        "children": [
            {"control_type": "Text", "name": "Oi! Tudo bem?", "rect": [480, 200, 700, 220]},
        ],
    }
    assert find_open_chat(node_from_dict(panel)) is None


@pytest.mark.desktop
def test_snapshot_of_a_window_that_does_not_exist_is_none():
    from respondoia.reader import read_open_chat, snapshot

    assert snapshot(0x7FFFFFF0) is None
    assert read_open_chat(0x7FFFFFF0) is None
