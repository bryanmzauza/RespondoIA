"""Masking, private contacts, and the checks that run before sending and before pasting."""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import Enum

from respondoia.domain import ChatLine, ChatRef, OpenChat


def normalize_name(name: str) -> str:
    """Fold case and whitespace so the same displayed name always compares equal."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def is_private(title: str, private_contacts: Iterable[str]) -> bool:
    # Whole-name equality only: "Ana" must not match "Ana Paula".
    name = normalize_name(title)
    return any(name == normalize_name(contact) for contact in private_contacts)


class SendDecision(Enum):
    ALLOW = "allow"
    PRIVATE = "private"
    UNKNOWN_CHAT = "unknown_chat"


def check_before_send(chat: OpenChat | None, private_contacts: Iterable[str]) -> SendDecision:
    if chat is None or not normalize_name(chat.title):
        return SendDecision.UNKNOWN_CHAT
    if is_private(chat.title, private_contacts):
        return SendDecision.PRIVATE
    return SendDecision.ALLOW


class PasteDecision(Enum):
    PASTE = "paste"
    COPY_ONLY = "copy_only"


def check_before_paste(
    captured: ChatRef, current: ChatRef | None, insert_mode: str
) -> PasteDecision:
    if insert_mode != "paste" or current is None or current.hwnd != captured.hwnd:
        return PasteDecision.COPY_ONLY
    if normalize_name(current.title) != normalize_name(captured.title):
        return PasteDecision.COPY_ONLY
    return PasteDecision.PASTE


# Masking. Recognisable private data is replaced by a marker before any text leaves
# the machine. Numbers with a check digit are masked only when the check digit is
# right, so prices, dates and order numbers stay readable.


@dataclass(frozen=True)
class Masked:
    text: str
    found: tuple[str, ...]  # marker names in order of appearance, e.g. ("CPF", "CARD")


def _digits(text: str) -> list[int]:
    return [int(c) for c in text if c.isdigit()]


def _cpf_ok(raw: str) -> bool:
    d = _digits(raw)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        total = sum(v * w for v, w in zip(d[:n], range(n + 1, 1, -1)))
        if total * 10 % 11 % 10 != d[n]:
            return False
    return True


def _cnpj_ok(raw: str) -> bool:
    d = _digits(raw)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for n in (12, 13):
        weights = [*range(n - 7, 1, -1), *range(9, 1, -1)]
        rest = sum(v * w for v, w in zip(d[:n], weights)) % 11
        if (0 if rest < 2 else 11 - rest) != d[n]:
            return False
    return True


def _luhn_ok(raw: str) -> bool:
    d = _digits(raw)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, v in enumerate(reversed(d)):
        if i % 2:
            v = v * 2 - 9 if v * 2 > 9 else v * 2
        total += v
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _phone_ok(raw: str) -> bool:
    return 10 <= len(_digits(raw)) <= 15


def _marker(kind: str) -> str:
    return f"\x00{kind}\x00"


def _checked(kind: str, valid):
    return lambda m: _marker(kind) if valid(m.group(0)) else m.group(0)


def _card(m: re.Match) -> str:
    # The pattern can take in an expiry or a security code written next to the
    # number: mask the longest run of whole digit groups that passes Luhn.
    text = m.group(0)
    groups = [(g.start(), g.end()) for g in re.finditer(r"\d+", text)]
    best = None
    for i in range(len(groups)):
        for j in range(i + 1, len(groups) + 1):
            start, end = groups[i][0], groups[j - 1][1]
            if _luhn_ok(text[start:end]):
                size = len(_digits(text[start:end]))
                if best is None or size > best[0]:
                    best = (size, start, end)
    if best is None:
        return text
    return text[: best[1]] + _marker("CARD") + text[best[2] :]


def _iban(m: re.Match) -> str:
    # The pattern can swallow a following word ("... 32 OK"): drop trailing groups
    # until what is left is a valid IBAN.
    tokens = m.group(0).split(" ")
    for keep in range(len(tokens), 0, -1):
        if _iban_ok(" ".join(tokens[:keep])):
            rest = " ".join(tokens[keep:])
            return _marker("IBAN") + (" " + rest if rest else "")
    return m.group(0)


_RULES = [
    (
        re.compile(
            r"(?i)\b(senha|password|pwd|pin|token|c[oó]digo)"
            r"(\s*(?:[:=]|\bé\b\s*[:=]?)\s*)(\S+)"
        ),
        lambda m: m.group(1) + m.group(2) + _marker("PASSWORD"),
    ),
    # Bounded parts and a start that cannot sit inside a run of word characters keep
    # this linear on long text without spaces.
    (
        re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+"),
        lambda m: _marker("EMAIL"),
    ),
    (
        re.compile(
            r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
            r"(?![0-9a-f])"
        ),
        lambda m: _marker("PIX"),
    ),
    (re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}"), _iban),
    (
        re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)"),
        _checked("CNPJ", _cnpj_ok),
    ),
    (re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"), _checked("CPF", _cpf_ok)),
    (re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)"), _card),
    # International: one separator at most between digits, so ". 2" after a
    # number is left alone.
    (
        re.compile(r"(?<![\w+])\+\d(?:(?:\s?\(|\)\s?|[\s.-])?\d){7,14}(?!\d)"),
        _checked("PHONE", _phone_ok),
    ),
    # Brazilian: area code in parentheses or followed by a separator, or a mobile
    # number written solid. A bare run of 10 digits is not taken as a phone.
    (
        re.compile(r"(?<!\d)(?:\(\d{2}\)\s?|\d{2}[\s.-])9?\d{4}[\s.-]?\d{4}(?!\d)"),
        _checked("PHONE", _phone_ok),
    ),
    (re.compile(r"(?<!\d)\d{2}9\d{8}(?!\d)"), lambda m: _marker("PHONE")),
]
_SENTINEL = re.compile("\x00([A-Z]+)\x00")


def mask_text(text: str) -> Masked:
    out = text.replace("\x00", "")
    for pattern, substitute in _RULES:
        out = pattern.sub(substitute, out)
    found = tuple(_SENTINEL.findall(out))
    return Masked(_SENTINEL.sub(r"[\1]", out), found)


def mask_lines(lines: Sequence[ChatLine]) -> tuple[tuple[ChatLine, ...], tuple[str, ...]]:
    """Mask sender, text and quote of every line. Returns the lines and every marker found."""
    masked, found = [], []
    for line in lines:
        fields = {}
        for name in ("sender", "text", "quote"):
            result = mask_text(getattr(line, name))
            fields[name] = result.text
            found.extend(result.found)
        masked.append(replace(line, **fields))
    return tuple(masked), tuple(found)
