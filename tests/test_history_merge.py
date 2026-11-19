import hashlib
import random
from dataclasses import replace

from respondoia.domain import ChatLine, family
from respondoia.memory.merge import (
    LOOK_DISTANCE,
    Stored,
    fill_days,
    find_overlap,
    fingerprint,
    from_lines,
    look_distance,
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


def photo(look="", time="", author="contact", shape="1.4"):
    return Stored("", time, author, "", "image", "", "", shape=shape, look=look)


def looks(n):
    """Fictional looks 16 bits apart from each other."""
    return [format(0xFF << (8 * i), "016x") for i in range(n)]


def test_fingerprint_uses_the_family():
    text = Stored("", "10:00", "contact", "", "text", "olha", "")
    link = Stored("", "10:00", "contact", "", "link", "olha", "")
    assert fingerprint(text) == fingerprint(link)
    assert family("image") == family("video") == "media"


def test_text_fingerprint_is_the_same_as_before():
    # Rows stored by 0.4 keep their fingerprint: no shape, the same parts.
    message = Stored("", "10:00", "contact", "", "text", "olha", "")
    old = "contact\x1f\x1f10:00\x1ftext\x1folha\x1f"  # the parts 0.4 hashed
    assert fingerprint(message) == hashlib.sha256(old.encode("utf-8")).hexdigest()


def test_shape_tells_pictures_apart():
    def run(middle):
        return [Stored("", "10:00", "contact", "", "text", "olha", ""), middle,
                Stored("", "10:02", "contact", "", "text", "gostou?", "")]

    assert find_overlap(run(photo(shape="1.4")), run(photo(shape="0.8"))) is None
    assert find_overlap(run(photo(shape="1.4")), run(photo(shape="1.4"))).length == 3


def test_photos_stored_by_0_4_join_their_reread():
    # Mid-review: 0.4 stored photos without a shape; 0.5 reads them with one.
    def text(value, time):
        return Stored("", time, "contact", "", "text", value, "")

    old = [text("tem azul?", "10:01"), photo(time="10:02", shape=""), text("e esse", "10:03"),
           photo(time="10:04", shape=""), text("quanto?", "10:05")]
    new = [text("tem azul?", "10:01"), photo(time="10:02"), text("e esse", "10:03"),
           photo(time="10:04"), text("quanto?", "10:05"), text("oi", "10:06")]
    result = merge([old], new)
    assert (result.gap, result.replaces, result.added) == (False, (0,), 1)


def test_same_shape_pictures_align_by_look():
    marks = looks(8)
    older = [Stored("", "09:00", "user", "", "text", "manda as fotos", "")]
    older += [photo(marks[i]) for i in range(6)]
    newer = [photo(marks[i]) for i in range(3, 8)]
    result = merge([older], newer)
    assert result.replaces == (0,) and result.added == 2
    assert [m.look for m in result.result[1:]] == marks


def test_blurred_and_sharp_reads_are_the_same_picture():
    sharp = "00000000000000ff"
    blurred = format(0xFF ^ 0b1111111, "016x")  # 7 bits off
    assert look_distance(sharp, blurred) == 7 <= LOOK_DISTANCE
    older = [Stored("", "09:00", "user", "", "text", "olha", ""), photo(sharp)]
    newer = [Stored("", "09:00", "user", "", "text", "olha", ""), photo(blurred)]
    result = merge([older], newer)
    assert result.added == 0 and len(result.result) == 2


def test_pictures_far_apart_in_look_are_different():
    a, b = "0000000000000000", format((1 << 30) - 1, "016x")
    assert look_distance(a, b) == 30
    older = [photo(a), photo(looks(3)[1])]
    newer = [photo(b), photo(looks(3)[1])]
    assert find_overlap(older, newer) is None


def test_media_only_overlap_without_look_does_not_count():
    pictures = [photo() for _ in range(5)]
    assert find_overlap(pictures, pictures[2:] + [photo(), photo()]) is None
    # A text among them makes it count again.
    anchored = pictures[:3] + [Stored("", "09:00", "user", "", "text", "lindas", "")]
    assert find_overlap(anchored, anchored[1:] + [photo()]) is not None


def test_video_stored_as_text_is_updated_in_place():
    ask = Stored("", "21:39", "user", "", "text", "gostou?", "")
    hello = Stored("", "21:40", "user", "", "text", "me manda o vídeo", "")
    old = Stored("", "21:47", "contact", "", "text", "0:42", "")
    new = Stored("", "21:47", "contact", "", "video", "", "", shape="0.6")
    result = merge([[ask, hello, old]], [ask, hello, new])
    assert result.changed == 1 and result.added == 0
    assert (result.result[2].kind, result.result[2].text, result.result[2].shape) == (
        "video",
        "",
        "0.6",
    )


def test_reread_needs_a_time():
    ask = Stored("", "21:39", "user", "", "text", "gostou?", "")
    hello = Stored("", "21:40", "user", "", "text", "me manda o vídeo", "")
    old = Stored("", "", "contact", "", "text", "0:42", "")
    new = Stored("", "", "contact", "", "video", "", "", shape="0.6")
    result = merge([[ask, hello, old]], [ask, hello, new])
    assert result.gap or result.changed == 0
    assert find_overlap([ask, hello, old], [ask, hello, new]) is None


def test_from_lines_keeps_shape_and_looks():
    lines = [
        ChatLine("contact", "", "", "image", "", "", shape="1.4"),
        ChatLine("contact", "", "10:00", "text", "oi", ""),
    ]
    stored = from_lines(lines, looks=("00000000000000ff", ""))
    assert (stored[0].shape, stored[0].look) == ("1.4", "00000000000000ff")
    assert (stored[1].shape, stored[1].look) == ("", "")
    assert from_lines(lines)[0].look == ""


LONG = "Bolo de cenoura fofinho em 40 minutos receitas.example https://receitas.example/bolo"


def tall(text, time="09:04", author="contact"):
    return Stored("", time, author, "", "link", text, "")


def test_one_long_line_at_the_seam_joins_two_tall_screens():
    # Link previews so tall that a screen holds two: the next screen shares only one.
    older = [tall("Outra receita longa de torta de limão receitas.example/torta", "09:01"), tall(LONG)]
    newer = [tall(LONG), tall("Mais uma receita longa de pudim de leite receitas.example/pudim", "09:07")]
    assert find_overlap(older, newer) is None
    assert find_overlap(older, newer, seam=True).length == 1


def test_the_seam_needs_a_long_timed_line_on_a_short_screen():
    newer_tail = tall("Mais uma receita longa de pudim de leite receitas.example/pudim", "09:07")
    short = [tall("x", "09:01"), tall("ok")], [tall("ok"), newer_tail]
    assert find_overlap(*short, seam=True) is None
    untimed = [tall("a", "09:01"), tall(LONG, "")], [tall(LONG, ""), newer_tail]
    assert find_overlap(*untimed, seam=True) is None
    three = [tall("um texto qualquer", "08:59"), tall("x", "09:01"), tall(LONG)]
    assert find_overlap(three, [tall(LONG), newer_tail], seam=True) is None


def test_one_hashed_picture_at_the_seam_joins_two_tall_screens():
    def photo(look, time="10:00", shape="1.4"):
        return Stored("", time, "contact", "", "image", "", "", shape, look)

    older = [photo("", shape="1.3"), photo("0f0f0f0f0f0f0f0f")]  # the top one is cut: no look
    newer = [photo("0f0f0f0f0f0f0f0e"), tall(LONG)]
    assert find_overlap(older, newer, seam=True).length == 1
    unhashed = [photo("", shape="1.3"), photo("")], [photo("0f0f0f0f0f0f0f0f"), tall(LONG)]
    assert find_overlap(*unhashed, seam=True) is None


def test_reread_photos_keep_their_shape_and_look():
    # Final review: dated 0.4 photo rows re-read by 0.5 gain the shape and the hash, so a
    # later screen of photos alone still joins; a stored hash is never wiped.
    def text(value, time, day="2026-11-10"):
        return Stored(day, time, "contact", "", "text", value, "")

    def pic(time, shape="", look="", day="2026-11-10"):
        return Stored(day, time, "contact", "", "image", "", "", shape, look)

    a, b = "00000000000000ff", "ff00000000000000"
    old = [text("olha", "10:00"), pic("10:01"), pic("10:01"), text("gostou?", "10:02")]
    new = [text("olha", "10:00", ""), pic("10:01", "1.4", a, ""), pic("10:01", "1.4", b, ""),
           text("gostou?", "10:02", "")]
    first = merge([old], new)
    assert [(m.shape, m.look) for m in first.result[1:3]] == [("1.4", a), ("1.4", b)]
    again = merge([first.result], [pic("10:01", "1.4", a, ""), pic("10:01", "1.4", b, "")])
    assert (again.gap, again.added) == (False, 0)
    stored = [text("olha", "10:00", ""), pic("10:01", "1.4", a, ""), text("gostou?", "10:02", "")]
    reread = [text("olha", "10:00"), pic("10:01", "1.4", ""), text("gostou?", "10:02")]
    assert merge([stored], reread).result[1].look == a
