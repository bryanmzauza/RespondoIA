import random
from dataclasses import replace

from respondoia.domain import ChatLine
from respondoia.memory.merge import (
    Stored,
    fill_days,
    find_overlap,
    fingerprint,
    from_lines,
    merge,
    normalize_time,
    stitch,
)

WORDS = ["bolo", "entrega", "sexta", "valor", "sabor", "festa", "cobertura", "pedido", "amanhã"]


def chat(n: int = 200) -> list[Stored]:
    """A fictional chat over five days, with some repeated short messages."""
    rng = random.Random(4)
    out = []
    for i in range(n):
        day = f"2026-11-{10 + i * 5 // n:02d}"
        minute = 8 * 60 + i * 3
        time = f"{minute // 60 % 24:02d}:{minute % 60:02d}"
        author = "user" if i % 3 == 0 else "contact"
        text = "ok" if i % 17 == 5 else f"{rng.choice(WORDS)} {rng.choice(WORDS)} {i}"
        out.append(Stored(day, time, author, "", "text", text, ""))
    return out


def screen(messages, start, stop, keep_days=False):
    """What one screen shows: no days unless a separator was visible."""
    part = messages[start:stop]
    return part if keep_days else [replace(m, sent_on="") for m in part]


def test_fingerprint_ignores_case_spaces_and_time_format():
    a = Stored("", normalize_time("9:47 PM"), "contact", "", "text", "Bom  dia", "")
    b = Stored("2026-11-12", "21:47", "contact", "", "text", "bom dia", "")
    assert fingerprint(a) == fingerprint(b)
    assert normalize_time("12:05 AM") == "00:05" and normalize_time("9:47") == "09:47"


def test_same_screen_twice_adds_nothing():
    msgs = chat()
    stored = [msgs[100:130]]
    result = merge(stored, screen(msgs, 100, 130))
    assert result.added == 0 and result.replaces == (0,) and not result.gap
    assert len(result.result) == 30


def test_screen_after_the_tail_appends():
    msgs = chat()
    result = merge([msgs[100:130]], screen(msgs, 115, 135))
    assert result.added == 5 and result.replaces == (0,)
    assert [m.text for m in result.result] == [m.text for m in msgs[100:135]]


def test_older_screen_prepends():
    msgs = chat()
    result = merge([msgs[100:130]], screen(msgs, 90, 105))
    assert result.added == 10
    assert [m.text for m in result.result] == [m.text for m in msgs[90:130]]


def test_repeated_short_messages_do_not_misalign():
    base = [Stored("2026-11-12", "10:00", "contact", "", "text", t, "") for t in ("a b", "c d")]
    oks = [Stored("2026-11-12", "10:01", "contact", "", "text", "ok", "") for _ in range(3)]
    new = [Stored("", "10:02", "contact", "", "text", "e f", "")]
    stored = base + oks
    incoming = [replace(m, sent_on="") for m in oks[1:]] + new
    result = merge([stored], incoming)
    assert result.added == 1
    assert [m.text for m in result.result] == ["a b", "c d", "ok", "ok", "ok", "e f"]


def test_edited_message_updates_in_place():
    msgs = chat()
    edited = list(screen(msgs, 110, 130))
    edited[5] = replace(edited[5], text="texto corrigido")
    result = merge([msgs[100:130]], edited)
    assert result.changed == 1 and result.added == 0
    assert len(result.result) == 30 and result.result[15].text == "texto corrigido"


def test_single_message_screen():
    msgs = chat()
    result = merge([msgs[100:130]], screen(msgs, 129, 130))
    assert result.added == 0 and result.replaces == (0,)


def test_single_unrelated_message_is_a_gap():
    msgs = chat()
    result = merge([msgs[100:130]], screen(msgs, 160, 161))
    assert result.gap and result.replaces == ()


def test_no_overlap_is_a_gap():
    msgs = chat()
    result = merge([msgs[100:130]], screen(msgs, 150, 170))
    assert result.gap and result.replaces == () and result.added == 20


def test_first_screen_is_not_a_gap():
    msgs = chat()
    result = merge([], screen(msgs, 0, 20))
    assert not result.gap and result.replaces == () and result.added == 20


def test_a_long_load_closes_the_gap():
    msgs = chat()
    result = merge([msgs[50:80], msgs[120:150]], screen(msgs, 70, 130))
    assert result.replaces == (0, 1) and not result.gap
    assert [m.text for m in result.result] == [m.text for m in msgs[50:150]]
    assert result.added == 40


def test_screens_stitch_in_any_order():
    msgs = chat()
    screens = [screen(msgs, start, start + 20) for start in range(0, 200, 16)]
    order = list(range(len(screens)))
    random.Random(7).shuffle(order)
    block = screens[order[0]]
    pending = order[1:]
    while pending:
        for index in list(pending):
            joined = stitch(block, screens[index])
            if joined is not None:
                block = joined
                pending.remove(index)
    assert [m.text for m in block] == [m.text for m in msgs]


def test_days_fill_down_from_each_separator():
    rows = [
        Stored("", "08:00", "contact", "", "text", "a", ""),
        Stored("2026-11-12", "09:00", "contact", "", "text", "b", ""),
        Stored("", "10:00", "user", "", "text", "c", ""),
        Stored("2026-11-13", "08:00", "contact", "", "text", "d", ""),
        Stored("", "08:30", "contact", "", "text", "e", ""),
    ]
    assert [m.sent_on for m in fill_days(rows)] == [
        "",
        "2026-11-12",
        "2026-11-12",
        "2026-11-13",
        "2026-11-13",
    ]


def test_known_different_days_never_match():
    a = [Stored("2026-11-10", "10:00", "contact", "", "text", t, "") for t in ("ok", "bom dia")]
    b = [replace(m, sent_on="2026-11-11") for m in a]
    assert find_overlap(a, b) is None


def test_stored_days_fill_the_screen():
    msgs = chat()
    result = merge([msgs[100:130]], screen(msgs, 115, 135))
    assert all(m.sent_on for m in result.result)


def test_one_message_in_the_middle_is_not_an_overlap():
    msgs = chat()
    assert find_overlap(msgs[100:130], screen(msgs, 110, 111)) is None
    assert find_overlap(msgs[100:130], screen(msgs, 129, 130)) is not None


def test_shared_messages_among_different_ones_are_not_an_overlap():
    # Another chat that received the same two broadcast messages at the same minute.
    msgs = chat()
    other = [Stored("", "07:00", "contact", "", "text", "outra conversa", "")]
    other += screen(msgs, 110, 112)
    other += [Stored("", "07:30", "user", "", "text", "nada a ver", "")]
    assert find_overlap(msgs[100:130], other) is None
    result = merge([msgs[100:130]], other)
    assert result.gap and result.replaces == ()


def test_a_screen_inside_the_stored_run_still_overlaps():
    msgs = chat()
    assert find_overlap(msgs[100:130], screen(msgs, 110, 120)) is not None


def test_masking_happens_in_from_lines():
    line = ChatLine("contact", "", "9:15 PM", "text", "meu cpf é 529.982.247-25", "", day="2026-11-12")
    (stored,) = from_lines([line])
    assert stored.text == "meu cpf é [CPF]"
    assert (stored.time, stored.sent_on) == ("21:15", "2026-11-12")
    assert from_lines([line], mask=False)[0].text.endswith("247-25")
