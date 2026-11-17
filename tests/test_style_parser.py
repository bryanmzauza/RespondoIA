import io
import zipfile
from pathlib import Path

import pytest

from respondoia.style import parser
from respondoia.style.parser import ExportError, parse_export

EXPORTS = Path(__file__).parent / "exports"


def read(name: str) -> bytes:
    return (EXPORTS / name).read_bytes()


def first_message(chat):
    return next(m for m in chat.messages if m.kind != "system")


@pytest.mark.parametrize("name", ["android-pt.txt", "ios-pt.txt"])
def test_brazilian_formats(name):
    first = first_message(parse_export(read(name), name))
    assert (first.sent_on, first.time, first.author) == ("2026-11-16", "09:12", "Marina Duarte")
    assert first.text == "Bom dia! Tudo certo pra sexta?"


@pytest.mark.parametrize("name", ["android-en-us.txt", "ios-en-us.txt"])
def test_us_formats_read_month_first_and_pm(name):
    chat = parse_export(read(name), name)
    first = first_message(chat)
    assert (first.sent_on, first.time) == ("2026-11-16", "21:12")
    assert chat.messages[-1].time == "08:06"


def test_multi_line_message_stays_one_message():
    texts = [m.text for m in parse_export(read("android-pt.txt"), "a.txt").messages]
    assert "primeira linha\nsegunda linha\nterceira linha" in texts


def test_line_that_looks_like_a_header_continues_the_message():
    texts = [m.text for m in parse_export(read("edge-cases.txt"), "e.txt").messages]
    assert "olha isso:\n16/11/2026 10:04 não é cabeçalho porque não tem traço" in texts


def test_name_with_emoji_and_marks():
    first = parse_export(read("edge-cases.txt"), "e.txt").messages[0]
    assert first.author == "Tia Cida 🌻" and first.text == "Oi querida"


def test_media_deleted_and_system_kinds():
    pt = parse_export(read("android-pt.txt"), "a.txt")
    assert [m.kind for m in pt.messages].count("system") == 1
    assert {m.kind for m in pt.messages} == {"system", "text", "media", "deleted"}
    ios = parse_export(read("ios-pt.txt"), "i.txt")
    assert [m.kind for m in ios.messages if m.author == "Marina Duarte"] == ["text", "media"]
    en = parse_export(read("android-en-us.txt"), "a.txt")
    assert [m.kind for m in en.messages] == ["text", "text", "media", "deleted", "text"]
    edge = parse_export(read("edge-cases.txt"), "e.txt")
    assert edge.messages[2].kind == "deleted" and edge.messages[-1].kind == "system"


def test_edited_suffix_is_removed():
    edge = parse_export(read("edge-cases.txt"), "e.txt")
    assert edge.messages[1].text == "oi tia, tudo bem?"


def test_authors_are_counted():
    chat = parse_export(read("android-pt.txt"), "a.txt")
    assert chat.authors == (("Rafa Lima", 4), ("Marina Duarte", 3))


def test_zip_is_read_in_memory(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("_chat.txt", read("ios-pt.txt"))
        archive.writestr("photo.jpg", b"\xff\xd8")
    chat = parse_export(buffer.getvalue(), "WhatsApp Chat - Marina.zip")
    assert chat == parse_export(read("ios-pt.txt"), "i.txt")
    assert list(tmp_path.iterdir()) == []


def test_zip_without_a_chat_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", read("ios-pt.txt"))
        archive.writestr("b.txt", read("ios-pt.txt"))
    with pytest.raises(ExportError) as err:
        parse_export(buffer.getvalue(), "x.zip")
    assert err.value.code == "zip"


def test_unknown_format_is_rejected():
    with pytest.raises(ExportError) as err:
        parse_export(b"# Notes\n\nJust some text, not a chat.\n", "notes.txt")
    assert err.value.code == "format"


def test_chat_without_text_messages_is_empty():
    data = "16/11/2026 09:15 - Marina Duarte: <Mídia oculta>\n".encode()
    with pytest.raises(ExportError) as err:
        parse_export(data, "a.txt")
    assert err.value.code == "empty"


def test_too_large_is_rejected(monkeypatch):
    monkeypatch.setattr(parser, "MAX_EXPORT_BYTES", 10)
    with pytest.raises(ExportError) as err:
        parse_export(read("android-pt.txt"), "a.txt")
    assert err.value.code == "too_large"


def test_bom_is_ignored():
    data = read("android-pt.txt")
    assert parse_export(b"\xef\xbb\xbf" + data, "a.txt") == parse_export(data, "a.txt")


def test_attachments_cards_and_locations_are_media():
    notes = [
        "<attached: 00000015-Ana Souza.vcf>",
        "Ana Souza.vcf (arquivo anexado)",
        "IMG-20261116-WA0001.jpg (file attached)",
        "Location: https://maps.google.com/?q=-23.5,-46.6",
        "Localização: https://maps.google.com/?q=1,2",
        "localização em tempo real compartilhada",
        "live location shared",
        "Cartão de contato omitido",
        "Contact card omitted",
    ]
    data = "\n".join(f"16/11/2026 09:{n:02d} - Rafa Lima: {note}" for n, note in enumerate(notes))
    data += "\n16/11/2026 10:00 - Rafa Lima: bora sim\n"
    chat = parse_export(data.encode(), "a.txt")
    assert [m.kind for m in chat.messages] == ["media"] * len(notes) + ["text"]
