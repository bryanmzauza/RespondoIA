import json
from pathlib import Path

from respondoia.domain import ChatLine
from respondoia.reader import TIME_RE, find_chat_view, node_from_dict, read_lines

FIXTURES = Path(__file__).parent / "fixtures" / "uia"


def load(name):
    return node_from_dict(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def panel_of(name):
    return next(n for n in walk(load(name)) if n.automation_id == "main")


def test_reads_sides_text_and_time():
    lines = read_lines(panel_of("msgs-pt.json"))
    assert [(line.author, line.text, line.time) for line in lines] == [
        ("contact", "Oi! Tudo bem?", "09:10"),
        ("user", "oi, tudo sim e vc?", "09:12"),
        ("contact", "Tudo ótimo.", "09:13"),
        ("contact", "Consegue me mandar o arquivo hoje?", "09:13"),
        ("user", "mando sim", "09:15"),
    ]
    assert all(line.kind == "text" and line.sender == "" and line.quote == "" for line in lines)


def test_interface_language_does_not_matter():
    assert read_lines(panel_of("msgs-en.json")) == read_lines(panel_of("msgs-pt.json"))


def test_group_lines_carry_the_sender():
    lines = read_lines(panel_of("msgs-group.json"))
    assert [(line.author, line.sender, line.text) for line in lines] == [
        ("contact", "Bia Rocha", "Quem leva a bola?"),
        ("contact", "Leandro Pires", "A minha furou"),
        ("user", "", "eu levo"),
        ("contact", "Bia Rocha", "E a rede?"),
    ]


def test_quote_is_kept_apart_from_the_text():
    lines = read_lines(panel_of("msgs-quote.json"))
    third = lines[2]
    assert third.quote.endswith("passo sim, posso chegar aí umas 18h")
    assert third.text == "Pode ser 19h? Vou me atrasar no trabalho" and third.time == "17:05"


def test_a_quoted_audio_length_is_not_the_time():
    last = read_lines(panel_of("msgs-quote.json"))[-1]
    assert last.time == "17:06" and "0:12" in last.quote and last.text == "ouve esse"


def test_media_kinds():
    lines = read_lines(panel_of("msgs-media.json"))
    assert [line.kind for line in lines] == ["image", "audio", "text"]
    assert lines[0].text == "Olha como ficou"
    assert lines[1].text == "" and lines[1].time == "14:32"


def test_rows_outside_the_message_area_are_skipped():
    lines = read_lines(panel_of("msgs-scrolled.json"))
    assert [line.text for line in lines] == ["primeira inteira", "segunda inteira"]


def test_no_rows_is_an_empty_tuple():
    assert read_lines(panel_of("msgs-empty.json")) == ()


def test_chat_view_combines_title_and_lines():
    view = find_chat_view(load("msgs-pt.json"))
    assert view.chat.title == "Marina Duarte" and len(view.lines) == 5
    assert view.lines[0] == ChatLine("contact", "", "09:10", "text", "Oi! Tudo bem?", "")


def test_chat_view_without_a_title_is_none():
    assert find_chat_view(load("no-chat-open.json")) is None


def test_time_pattern():
    for text in ("9:47", "21:47", "9:47 PM", "9:47 p.m."):
        assert TIME_RE.match(text), text
    for text in ("18h", "21:47:00", "R$ 21,47", "Oi"):
        assert not TIME_RE.match(text), text


def widened(name, share):
    """The fixture with its first bubble stretched to a share of the row width."""
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    rows = [n for n in walk_dicts(data) if n.get("aria_role") == "row"]
    row = rows[0]
    left, right = row["rect"][0], row["rect"][2]
    bubble_left = left + 62
    new_right = bubble_left + int(share * (right - left))
    for node in walk_dicts(row):
        if node["control_type"] == "Group" and node["rect"][0] == bubble_left:
            node["rect"][2] = new_right
    return node_from_dict(data)


def walk_dicts(node):
    yield node
    for child in node.get("children", []):
        yield from walk_dicts(child)


def test_a_wide_bubble_keeps_its_side_and_text():
    root = widened("msgs-pt.json", 0.85)
    panel = next(n for n in walk(root) if n.automation_id == "main")
    first = read_lines(panel)[0]
    assert (first.author, first.text, first.time) == ("contact", "Oi! Tudo bem?", "09:10")


def test_the_play_button_is_not_a_sender():
    audio = read_lines(panel_of("msgs-media.json"))[1]
    assert audio.kind == "audio" and audio.sender == ""


def user_row(top, *extras):
    """A user-side row: one body text, the time, and any extra nodes inside the bubble."""

    def node(kind, name="", rect=(0, 0, 0, 0), role=""):
        return {"control_type": kind, "name": name, "automation_id": "", "aria_role": role,
                "rect": list(rect), "children": []}

    bubble = node("Group", rect=(1298, top, 1528, top + 58), role="group")
    body = node("Group", rect=(1307, top + 6, 1488, top + 27), role="group")
    body["children"] = [node("Text", "bora sim", (1307, top + 7, 1468, top + 25), "description")]
    clock = node("Group", rect=(1488, top + 42, 1519, top + 58), role="group")
    clock["children"] = [node("Text", "09:12", (1488, top + 43, 1519, top + 57), "description")]
    bubble["children"] = [*extras, body, clock]
    row = node("DataItem", rect=(420, top, 1590, top + 60), role="row")
    inner = node("Group", rect=(420, top, 1590, top + 60), role="group")
    inner["children"] = [bubble]
    row["children"] = [inner]
    return row


def plain_flags(*rows):
    panel = {"control_type": "Group", "name": "", "automation_id": "main", "aria_role": "group",
             "rect": [420, 0, 1600, 900], "children": list(rows)}
    return [line.plain for line in read_lines(node_from_dict(panel))]


def extra(kind, name, rect):
    return {"control_type": kind, "name": name, "automation_id": "", "aria_role": "",
            "rect": list(rect), "children": []}


def test_only_a_single_text_makes_a_plain_line():
    assert plain_flags(user_row(100)) == [True]
    assert plain_flags(user_row(100, extra("Text", "Encaminhada", (1307, 101, 1400, 105)))) == [False]
    assert plain_flags(user_row(100, extra("Hyperlink", "exemplo.com", (1307, 101, 1400, 160)))) == [False]
    assert plain_flags(user_row(100, extra("Button", "Conversar", (1307, 101, 1400, 130)))) == [False]
    assert plain_flags(user_row(100, extra("Image", "", (1307, 101, 1500, 300)))) == [False]
    # Emoji drawn as small images stay plain.
    assert plain_flags(user_row(100, extra("Image", "", (1307, 101, 1327, 121)))) == [True]


def test_fixture_lines_are_plain():
    assert all(line.plain for line in read_lines(panel_of("msgs-pt.json")))
