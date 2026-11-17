"""Keep only the user's own messages and sample them across chats and periods.

Everything here is local and free: the filter that keeps other people's messages out,
the sample sent for extraction, and the counts that describe the style without a model.
"""

import hashlib
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from respondoia.domain import ChatLine
from respondoia.privacy import mask_text, normalize_name
from respondoia.style.language import chat_language, detect
from respondoia.style.parser import ExportChat

MAX_LENGTH = 1000


@dataclass(frozen=True)
class OwnMessage:
    text: str  # masked
    language: str
    chat_key: str
    sent_on: str
    time: str
    run: int  # position of the user's turn in this batch, from 0
    label: str | None  # import label; None for screen messages (the contact's label applies)


class StyleStats(BaseModel):
    messages: int = 0
    median_length: int = 0
    burst_share: float = 0.0  # turns sent as 2 or more messages / turns
    lowercase_start: float = 0.0  # share of messages whose first letter is lowercase
    final_period: float = 0.0  # share ending in a single "."
    emoji_share: float = 0.0
    top_emoji: list[str] = []  # up to 5, most used first
    emoji_position: str | None = None  # "start", "middle", "end" or None
    abbreviations: list[str] = []  # known abbreviations in 3% or more of messages
    laughter: str | None = None  # most used laugh, shortened


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


# Content analysis vocabulary, not interface text.
_ABBREVIATIONS = _words(
    "abs bjs blz brb btw cmg dps flw gonna hj idk imo msm mt mto nd np obg omg pls pq "
    "q qnd tb tbh tbm td tds thx tmj ty u ur vc vcs vlw wanna xq"
)
_LAUGHS = [
    (re.compile(r"^k{3,}$"), "kkk"),
    (re.compile(r"^(?:rs){2,}$"), "rsrs"),
    (re.compile(r"^(?:ha){2,}h?$"), "haha"),
    (re.compile(r"^(?:he){2,}$"), "hehe"),
    (re.compile(r"^(?:lol|lmao)$"), "lol"),
]
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_MARKER = re.compile(r"\[[A-Z]+\]")
_DIGIT = re.compile(r"\d")
_CURRENCY = re.compile(r"[$€£¥]")
_WORD = re.compile(r"[^\W\d_]+")
_NUMBER = re.compile(r"\d+")


def text_hash(text: str) -> str:
    """The same message with other punctuation, case or emoji has the same hash."""
    words = _WORD.findall(normalize_name(text)) + _NUMBER.findall(text)
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


def _is_emoji(char: str) -> bool:
    code = ord(char)
    return code >= 0x1F000 or 0x2600 <= code <= 0x27BF


def is_learnable(text: str) -> bool:
    """Not only a link, has a letter, at most MAX_LENGTH characters."""
    if len(text) > MAX_LENGTH:
        return False
    return any(c.isalpha() for c in _URL.sub("", text))


def _is_own_text(line: ChatLine) -> bool:
    return (
        line.author == "user"
        and line.kind == "text"
        and line.plain
        and bool(line.text.strip())
    )


def own_lines(lines: Sequence[ChatLine]) -> tuple[ChatLine, ...]:
    """The user's text lines. The quote, which is someone else's text, is never used."""
    return tuple(line for line in lines if _is_own_text(line))


def _own(text, language, chat_key, sent_on, time, run, label, pasted) -> OwnMessage | None:
    if not is_learnable(text) or text_hash(text) in pasted:
        return None
    language = detect(text) or language
    if language is None:
        return None
    return OwnMessage(mask_text(text.strip()).text, language, chat_key, sent_on, time, run, label)


def from_screen(
    lines: Sequence[ChatLine], chat_key: str, chat_lang: str | None, pasted: set[str]
) -> tuple[OwnMessage, ...]:
    learned, run, others_since = [], 0, False
    seen_user = False
    for line in lines:
        if line.author != "user":
            others_since = True
            continue
        if seen_user and others_since:
            run += 1
        seen_user, others_since = True, False
        if not _is_own_text(line):
            continue
        own = _own(line.text, chat_lang, chat_key, "", line.time, run, None, pasted)
        if own is not None:
            learned.append(own)
    return tuple(learned)


def import_key(other_authors: Iterable[str]) -> str:
    """Where an imported chat's messages are kept: named after the other people in it."""
    names = sorted(normalize_name(name) for name in other_authors)
    return "import:" + hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()


def from_export(
    chat: ExportChat, author: str, label: str | None, pasted: set[str]
) -> tuple[OwnMessage, ...]:
    lang = chat_language([m.text for m in chat.messages if m.kind == "text"])
    chat_key = import_key({m.author for m in chat.messages if m.author and m.author != author})
    learned, run, others_since, seen_user = [], 0, False, False
    for message in chat.messages:
        if message.kind == "system":
            continue
        if message.author != author:
            others_since = True
            continue
        if seen_user and others_since:
            run += 1
        seen_user, others_since = True, False
        if message.kind != "text":
            continue
        own = _own(message.text, lang, chat_key, message.sent_on, message.time, run, label, pasted)
        if own is not None:
            learned.append(own)
    return tuple(learned)


def _by_chat(messages: Sequence[OwnMessage]) -> dict[str, list[OwnMessage]]:
    chats: dict[str, list[OwnMessage]] = {}
    for message in messages:
        chats.setdefault(message.chat_key, []).append(message)
    return chats


def sample(messages: Sequence[OwnMessage], limit: int = 300) -> list[OwnMessage]:
    """Round robin between chats; inside a chat, evenly spaced in file order."""
    chats = _by_chat(messages)
    quota = dict.fromkeys(chats, 0)
    left = min(limit, len(messages))
    while left:
        for key, items in chats.items():
            if left and quota[key] < len(items):
                quota[key] += 1
                left -= 1
    picked = []
    for key, items in chats.items():
        n, q = len(items), quota[key]
        picked.extend(items[i * n // q] for i in range(q))
    return picked


def _share(count: int, total: int) -> float:
    return round(count / total, 2) if total else 0.0


def _laugh(word: str) -> str | None:
    return next((name for pattern, name in _LAUGHS if pattern.match(word)), None)


def stats(messages: Sequence[OwnMessage]) -> StyleStats:
    total = len(messages)
    if not total:
        return StyleStats()
    texts = [m.text for m in messages]
    runs = Counter((m.chat_key, m.run) for m in messages)
    letters = [next((c for c in t if c.isalpha()), "") for t in texts]
    starts = [c for c in letters if c]
    emoji_texts = [t for t in texts if any(_is_emoji(c) for c in t)]
    emoji = Counter(c for t in texts for c in t if _is_emoji(c))
    positions = Counter()
    for text in emoji_texts:
        stripped = text.strip()
        if _is_emoji(stripped[-1]):
            positions["end"] += 1
        elif _is_emoji(stripped[0]):
            positions["start"] += 1
        else:
            positions["middle"] += 1
    words = [set(_WORD.findall(t.casefold())) for t in texts]
    abbreviations = Counter(w for ws in words for w in ws if w in _ABBREVIATIONS)
    laughs = Counter(name for ws in words for w in ws if (name := _laugh(w)))

    def ends_with_period(text: str) -> bool:
        core = "".join(c for c in text if not _is_emoji(c)).rstrip()
        return core.endswith(".") and not core.endswith("..")

    return StyleStats(
        messages=total,
        median_length=int(statistics.median(len(t) for t in texts)),
        burst_share=_share(sum(1 for n in runs.values() if n > 1), len(runs)),
        lowercase_start=_share(sum(c.islower() for c in starts), total),
        final_period=_share(sum(ends_with_period(t) for t in texts), total),
        emoji_share=_share(len(emoji_texts), total),
        top_emoji=[c for c, _ in emoji.most_common(5)],
        emoji_position=positions.most_common(1)[0][0] if positions else None,
        abbreviations=[
            w
            for w, n in sorted(abbreviations.items(), key=lambda item: (-item[1], item[0]))
            if n / total >= 0.03
        ],
        laughter=laughs.most_common(1)[0][0] if laughs else None,
    )


def _has_inner_capital(text: str) -> bool:
    """A capitalised word that does not start a sentence: likely a name or a place."""
    for match in _WORD.finditer(text):
        word = match.group(0)
        if not word[0].isupper() or word == "I":
            continue
        before = text[: match.start()].rstrip()
        if before and before[-1] not in ".!?":
            return True
    return False


def _example_ok(text: str) -> bool:
    """Examples go with every request, to every contact: no number, price, name or link."""
    return (
        8 <= len(text) <= 160
        and not _MARKER.search(text)
        and not _DIGIT.search(text)
        and not _CURRENCY.search(text)
        and not _URL.search(text)
        and "@" not in text
        and not _has_inner_capital(text)
    )


def pick_examples(
    messages: Sequence[OwnMessage], count: int = 12, excluded: set[str] = frozenset()
) -> list[str]:
    """Clean examples, spread across chats. They go to other contacts, so no data."""
    seen = {normalize_name(e) for e in excluded}
    queues = []
    for items in _by_chat(messages).values():
        queue = []
        for message in items:
            key = normalize_name(message.text)
            if key not in seen and _example_ok(message.text):
                seen.add(key)
                queue.append(message.text)
        queues.append(queue)
    picked = []
    while len(picked) < count and any(queues):
        for queue in queues:
            if queue and len(picked) < count:
                picked.append(queue.pop(0))
    return picked
