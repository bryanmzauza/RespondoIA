import json
from pathlib import Path

from respondoia.domain import Rect, family
from respondoia.reader import find_open_chat, node_from_dict, read_lines
from respondoia.style.sampler import own_lines

FIXTURES = Path(__file__).parent / "fixtures" / "uia"

# Rows of media-self.json, in order.
USER_TEXT, PHOTO, CAPTIONED, VIDEO, LINK, DOMAIN, AUDIO, STICKER, DOCUMENT = range(9)


def load(name):
    return node_from_dict(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def panel_of(name):
    return next(n for n in walk(load(name)) if n.automation_id == "main")


def lines_of(name):
    return read_lines(panel_of(name))


def line_at(index, name="media-self.json"):
    return lines_of(name)[index]


def test_every_row_is_one_line():
    assert [line.kind for line in lines_of("media-self.json")] == [
        "text", "image", "image", "video", "link", "text", "audio", "sticker", "document"
    ]


def test_photo_is_a_picture_line():
    line = line_at(PHOTO)
    assert (line.author, line.kind, line.text, line.time, line.shape) == (
        "contact", "image", "", "09:01", "1.3"
    )
    assert line.duration == "" and not line.plain


def test_photo_rect_is_the_picture_not_the_bubble():
    line = line_at(CAPTIONED)
    # The button that shows the picture, not the taller images inside it nor the caption.
    assert line.rect == Rect(485, 537, 815, 757)
    assert line.shape == "0.7"


def test_photo_with_caption_keeps_the_caption():
    line = line_at(CAPTIONED)
    assert (line.kind, line.text, line.time) == ("image", "Olha o bolo pronto", "09:02")


def test_video_has_length_and_shape():
    line = line_at(VIDEO)
    assert (line.kind, line.duration, line.shape, line.text) == ("video", "0:42", "0.6", "")


def test_video_length_is_not_the_time():
    assert line_at(VIDEO).time == "21:47"


def test_link_preview_has_site():
    line = line_at(LINK)
    assert (line.kind, line.site, line.time, line.plain) == (
        "link", "receitas.example", "09:04", False
    )
    assert "Bolo de cenoura fofinho" in line.text and line.shape == ""


def test_domain_in_plain_text_is_not_a_link():
    line = line_at(DOMAIN)
    assert (line.kind, line.site, line.text) == ("text", "", "a receita é do receitas.example")


def test_audio_has_no_length_or_transcript():
    # T0: WhatsApp shows neither in the tree.
    line = line_at(AUDIO)
    assert (line.kind, line.text, line.duration, line.transcript, line.time) == (
        "audio", "", "", False, "09:06"
    )


def test_sticker_is_not_a_photo():
    line = line_at(STICKER)
    assert (line.kind, line.text, line.time, line.shape) == ("sticker", "", "09:07", "")


def test_document_card():
    line = line_at(DOCUMENT)
    assert (line.author, line.kind, line.text, line.quote, line.time) == (
        "user", "document", "Orçamento cozinha\n2 páginas", "", "09:08"
    )


def test_album_is_one_picture_line():
    lines = lines_of("media-album.json")
    assert [(line.kind, line.time, line.shape) for line in lines] == [("image", "10:15", "1.0")]
    assert lines[0].rect == Rect(485, 83, 791, 389)


def test_group_photo_keeps_the_sender():
    first, second = lines_of("media-group.json")
    assert (first.sender, first.kind, first.text) == ("Bia Rocha", "image", "Rede nova")
    assert (second.sender, second.kind, second.time, second.shape) == ("", "image", "", "1.1")


def test_message_area_is_known():
    chat = find_open_chat(load("media-self.json"))
    assert chat.messages == Rect(420, 65, 1600, chat.bounds.bottom - 60)
    assert find_open_chat(load("msgs-pt.json")).messages.top == 65


def test_family():
    assert [family(k) for k in ("text", "link", "audio", "image", "video", "other")] == [
        "text", "text", "audio", "media", "media", "media"
    ]


def test_style_never_learns_media_lines():
    def own(line):
        return line.__class__(**{**line.__dict__, "author": "user"})

    media = [own(line) for line in lines_of("media-self.json") if line.kind != "text"]
    assert own_lines(media) == ()


OLD = {
    "msgs-pt.json": [
        ("contact", "", "09:10", "text", "Oi! Tudo bem?", "", True),
        ("user", "", "09:12", "text", "oi, tudo sim e vc?", "", True),
        ("contact", "", "09:13", "text", "Tudo ótimo.", "", True),
        ("contact", "", "09:13", "text", "Consegue me mandar o arquivo hoje?", "", True),
        ("user", "", "09:15", "text", "mando sim", "", True),
    ],
    "msgs-group.json": [
        ("contact", "Bia Rocha", "20:07", "text", "Quem leva a bola?", "", False),
        ("contact", "Leandro Pires", "20:08", "text", "A minha furou", "", False),
        ("user", "", "20:10", "text", "eu levo", "", True),
        ("contact", "Bia Rocha", "20:11", "text", "E a rede?", "", False),
    ],
    "msgs-media.json": [
        ("contact", "", "14:31", "image", "Olha como ficou", "", False),
        ("contact", "", "14:32", "audio", "", "", False),
        ("contact", "", "14:33", "text", "Ficou torta?", "", True),
    ],
    "msgs-quote.json": [
        ("contact", "", "16:40", "text", "Você passa aqui hoje pra pegar as caixas?", "", True),
        ("user", "", "16:42", "text", "passo sim, posso chegar aí umas 18h", "", True),
        (
            "contact", "", "17:05", "text", "Pode ser 19h? Vou me atrasar no trabalho",
            "Você\npasso sim, posso chegar aí umas 18h", True,
        ),
        ("contact", "", "17:06", "text", "ouve esse", "Paula Mendes\n0:12", True),
    ],
    "msgs-scrolled.json": [
        ("contact", "", "08:40", "text", "primeira inteira", "", True),
        ("user", "", "08:41", "text", "segunda inteira", "", True),
    ],
}


def test_old_fixtures_read_the_same():
    for name, expected in OLD.items():
        got = [
            (x.author, x.sender, x.time, x.kind, x.text, x.quote, x.plain) for x in lines_of(name)
        ]
        assert got == expected, name
    assert lines_of("msgs-en.json") == lines_of("msgs-pt.json")


def test_a_domain_linked_in_the_contact_text_is_not_a_preview():
    # Mid-review: WhatsApp links a bare domain inside the message; the site is only the
    # text outside the link, under a preview.
    data = json.loads((FIXTURES / "media-self.json").read_text(encoding="utf-8"))
    rows = [n for n in walk_dicts(data) if n.get("aria_role") == "row"]
    bubble = rows[DOMAIN]["children"][0]["children"][0]
    body = bubble["children"][0]
    l, t, _, b = body["children"][0]["rect"]
    body["children"].append({
        "control_type": "Hyperlink", "name": "loja.example", "automation_id": "",
        "aria_role": "link", "rect": [l, t, l + 80, b],
        "children": [{"control_type": "Text", "name": "loja.example", "automation_id": "",
                      "aria_role": "description", "rect": [l, t, l + 80, b], "children": []}],
    })
    panel = next(n for n in walk(node_from_dict(data)) if n.automation_id == "main")
    line = read_lines(panel)[DOMAIN]
    assert (line.kind, line.site) == ("text", "")


def walk_dicts(node):
    yield node
    for child in node.get("children", []):
        yield from walk_dicts(child)


def test_document_card_with_buttons_below_is_still_a_document():
    # Seen on a contact's PDF: "open" and "save as" buttons under the card make the bubble
    # taller than the card, which must still not be read as a quote.
    data = json.loads((FIXTURES / "media-self.json").read_text(encoding="utf-8"))
    rows = [n for n in walk_dicts(data) if n.get("aria_role") == "row"]
    bubble = rows[DOCUMENT]["children"][0]["children"][0]
    l, t, r, b = bubble["rect"]
    bubble["rect"] = [l, t, r, b + 40]
    rows[DOCUMENT]["rect"][3] += 42
    for name, left in (("Ver", l), ("Salvar como...", l + 168)):
        bubble["children"].append({
            "control_type": "Button", "name": name, "automation_id": "", "aria_role": "button",
            "rect": [left, b + 2, left + 168, b + 39], "children": [],
        })
    panel = next(n for n in walk(node_from_dict(data)) if n.automation_id == "main")
    line = read_lines(panel)[DOCUMENT]
    assert (line.kind, line.quote, line.time) == ("document", "", "09:08")
    assert line.text == "Orçamento cozinha\n2 páginas"
