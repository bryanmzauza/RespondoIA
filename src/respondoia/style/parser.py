"""Parse exported chat .txt files in both export formats.

The file is read in memory and never copied. The media and deleted-message markers
below are part of the export file format, so matching them is not reading the
WhatsApp interface by its labels.
"""

import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

MAX_EXPORT_BYTES = 50_000_000
_HEADER_LINES = 50  # non-empty lines searched for a first header


@dataclass(frozen=True)
class ExportMessage:
    sent_on: str  # ISO date "2026-11-16", "" if the date could not be read
    time: str  # "09:12", 24-hour
    author: str  # as written in the file, "" for system lines
    text: str
    kind: str  # "text", "media", "deleted" or "system"


@dataclass(frozen=True)
class ExportChat:
    messages: tuple[ExportMessage, ...]
    authors: tuple[tuple[str, int], ...]  # (author, message count), most messages first


class ExportError(ValueError):
    """code: "format", "empty", "too_large" or "zip"."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_DATE = r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})"
_TIME = r"(\d{1,2}):(\d{2})(?::\d{2})?(?:\s?([AaPp])\.?\s?[Mm]\.?)?"
_BRACKET = re.compile(rf"^\[{_DATE},? {_TIME}\] (.*)$")
_DASH = re.compile(rf"^{_DATE},? {_TIME} - (.*)$")
# Direction marks the apps put around names and media notes.
_INVISIBLE = dict.fromkeys([0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0xFEFF])
_SPACES = {0x202F: " ", 0x00A0: " "}

_MEDIA = {
    "mídia oculta",
    "media omitted",
    "multimedia omitido",
    "imagem ocultada",
    "image omitted",
    "áudio ocultado",
    "audio omitted",
    "vídeo omitido",
    "video omitted",
    "figurinha omitida",
    "sticker omitted",
    "gif omitido",
    "gif omitted",
    "documento omitido",
    "document omitted",
    "cartão de contato omitido",
    "contact card omitted",
    "localização em tempo real compartilhada",
    "live location shared",
}
# Attachments, contact cards and locations: someone's name or a place, never style.
_ATTACHMENTS = [
    re.compile(r"^attached: .+"),
    re.compile(r".+\((?:arquivo anexado|file attached)\)$"),
    re.compile(r"^(?:location|localização|localizacao): \S+"),
]
_DELETED = {
    "mensagem apagada",
    "esta mensagem foi apagada",
    "você apagou esta mensagem",
    "this message was deleted",
    "you deleted this message",
}
_EDITED = re.compile(r"\s*<(?:mensagem editada|this message was edited)>\s*$", re.IGNORECASE)


def _chat_text(data: bytes) -> str:
    if len(data) > MAX_EXPORT_BYTES:
        raise ExportError("too_large")
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                texts = [i for i in archive.infolist() if i.filename.lower().endswith(".txt")]
                named = [i for i in texts if PurePosixPath(i.filename).name == "_chat.txt"]
                chosen = named or texts
                if len(chosen) != 1:
                    raise ExportError("zip")
                if chosen[0].file_size > MAX_EXPORT_BYTES:
                    raise ExportError("too_large")
                data = archive.read(chosen[0])
        except zipfile.BadZipFile as exc:
            raise ExportError("zip") from exc
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExportError("format") from exc


def _clean(line: str) -> str:
    return line.translate(_INVISIBLE).translate(_SPACES).rstrip("\r")


def _iso(first: int, second: int, year: int, day_first: bool) -> str:
    day, month = (first, second) if day_first else (second, first)
    if year < 100:
        year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _clock(hour: int, minute: int, half: str | None) -> str:
    if half:
        hour = hour % 12 + (12 if half.lower() == "p" else 0)
    return f"{hour:02d}:{minute:02d}"


def _kind(text: str) -> str:
    folded = text.strip().strip("<>").strip().casefold()
    if folded in _MEDIA or any(p.match(folded) for p in _ATTACHMENTS):
        return "media"
    if folded in _DELETED:
        return "deleted"
    return "text"


def parse_export(data: bytes, filename: str) -> ExportChat:
    """filename is only used to tell the caller what was read; the content decides the format."""
    lines = [_clean(line) for line in _chat_text(data).split("\n")]
    pattern = None
    for line in [line for line in lines if line.strip()][:_HEADER_LINES]:
        pattern = next((p for p in (_BRACKET, _DASH) if p.match(line)), None)
        if pattern:
            break
    if pattern is None:
        raise ExportError("format")

    # One pass to find the headers, then the date order is decided for the whole file.
    entries: list[list] = []  # [match, text lines]
    for line in lines:
        match = pattern.match(line)
        if match:
            entries.append([match, [match.group(7)]])
        elif entries:
            entries[-1][1].append(line)
    firsts = [int(m.group(1)) for m, _ in entries]
    seconds = [int(m.group(2)) for m, _ in entries]
    twelve_hour = any(m.group(6) for m, _ in entries)
    if any(n > 12 for n in firsts):
        day_first = True
    elif any(n > 12 for n in seconds):
        day_first = False
    else:
        day_first = not twelve_hour

    messages = []
    for match, body in entries:
        sent_on = _iso(int(match.group(1)), int(match.group(2)), int(match.group(3)), day_first)
        time = _clock(int(match.group(4)), int(match.group(5)), match.group(6))
        rest = "\n".join(body).strip("\n")
        author, separator, text = rest.partition(": ")
        if not separator:
            messages.append(ExportMessage(sent_on, time, "", rest.strip(), "system"))
            continue
        text = _EDITED.sub("", text).strip()
        messages.append(ExportMessage(sent_on, time, author.strip(), text, _kind(text)))

    if not any(m.kind == "text" and m.text for m in messages):
        raise ExportError("empty")
    counts = Counter(m.author for m in messages if m.kind != "system")
    authors = tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return ExportChat(tuple(messages), authors)
