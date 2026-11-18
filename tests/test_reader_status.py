"""The user's own bubbles, as WhatsApp Desktop draws them: the time sits inside a button
with the read receipt. Geometry from the 0.4 probe; text and names invented."""

from respondoia.reader import node_from_dict, read_lines


def node(kind, name="", rect=(0, 0, 0, 0), role="", children=()):
    return {
        "control_type": kind,
        "name": name,
        "automation_id": "",
        "aria_role": role,
        "rect": list(rect),
        "children": list(children),
    }


def status_button(top):
    receipt = node("Image", "msg-dblcheck", (1830, top + 16, 1846, top + 32), "img")
    return node(
        "Button",
        "11:04 Lida",
        (1797, top + 16, 1846, top + 32),
        "button",
        [
            node("Text", "11:04", (1797, top + 17, 1827, top + 31), "description"),
            node("Group", "", (1830, top + 16, 1846, top + 32), "group", [receipt]),
        ],
    )


def user_row(top, *before):
    body = node(
        "Group",
        "",
        (1727, top + 6, 1792, top + 27),
        "group",
        [node("Text", "bom dia!", (1727, top + 7, 1792, top + 25), "description")],
    )
    clock = node("Group", "", (1797, top + 16, 1846, top + 32), "group", [status_button(top)])
    bubble = node("Group", "", (1718, top, 1853, top + 35), "group", [*before, body, clock])
    inner = node("Group", "", (444, top, 1910, top + 37), "group", [bubble])
    return node("DataItem", "", (444, top, 1910, top + 37), "row", [inner])


def lines(*rows):
    panel = node("Group", "", (444, 38, 1920, 1032), "group", rows)
    panel["automation_id"] = "main"
    return read_lines(node_from_dict(panel))


def test_the_time_inside_the_status_button_is_the_time_not_a_quote():
    (line,) = lines(user_row(471))
    assert (line.author, line.text, line.time, line.quote) == ("user", "bom dia!", "11:04", "")
    assert line.plain


def test_a_button_around_the_whole_message_is_not_a_quote():
    # Seen in the probe: a user bubble whose content and status button sit in one button.
    texts = [
        node("Text", "Ateliê Doce Lima", (1727, 480, 1840, 496), "description"),
        node("Text", "bolo de limão", (1727, 500, 1840, 516), "description"),
    ]
    wrapper = node(
        "Button", "cartão", (1718, 471, 1853, 540), "button", [*texts, status_button(505)]
    )
    bubble = node("Group", "", (1718, 471, 1853, 542), "group", [wrapper])
    inner = node("Group", "", (444, 471, 1910, 544), "group", [bubble])
    row = node("DataItem", "", (444, 471, 1910, 544), "row", [inner])
    (line,) = lines(row)
    assert line.time == "11:04" and line.quote == ""
    assert line.text == "Ateliê Doce Lima\nbolo de limão" and line.kind == "text"
    assert not line.plain


def test_a_real_quote_in_a_user_bubble_is_still_a_quote():
    quoted = node(
        "Button",
        "Marina: amanhã às nove?",
        (1727, 400, 1846, 440),
        "button",
        [
            node("Text", "Marina", (1730, 402, 1790, 418), "description"),
            node("Text", "amanhã às nove?", (1730, 420, 1840, 436), "description"),
        ],
    )
    row = user_row(471)
    bubble = row["children"][0]["children"][0]
    bubble["rect"] = [1718, 398, 1853, 506]
    bubble["children"].insert(0, quoted)
    row["rect"] = [444, 398, 1910, 508]
    row["children"][0]["rect"] = [444, 398, 1910, 508]
    (line,) = lines(row)
    assert line.quote == "Marina\namanhã às nove?" and line.time == "11:04"
    assert line.text == "bom dia!"
