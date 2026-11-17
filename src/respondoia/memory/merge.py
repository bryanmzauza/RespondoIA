"""Merge what is read on screen into the stored history without duplicates, and detect gaps.

Messages have no stable id, only what the screen shows. Two runs of messages are joined
where a sequence of them is equal on both sides: same author, sender, time, kind, text and
quote. Days come from the day separators and are passed down to the messages below them.
"""

import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from respondoia.domain import ChatLine
from respondoia.privacy import mask_text

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s?([AaPp])?\.?\s?[Mm]?\.?$")


@dataclass(frozen=True)
class Stored:
    """A message as the history keeps it."""

    sent_on: str  # ISO date or ""
    time: str  # "HH:MM", 24-hour, or ""
    author: str  # "user" or "contact"
    sender: str
    kind: str
    text: str  # masked
    quote: str  # masked


@dataclass(frozen=True)
class Overlap:
    older_start: int
    newer_start: int
    length: int


@dataclass(frozen=True)
class Merge:
    result: tuple[Stored, ...]  # the continuous run after merging
    replaces: tuple[int, ...]  # indexes of the stored segments it absorbs; () = a new segment
    added: int  # messages not stored before
    changed: int  # stored messages updated in place (edited or deleted)
    gap: bool  # nothing stored was touched, though there is stored history


def normalize_time(value: str) -> str:
    """Turn "9:47 PM" into "21:47". Anything that is not a time is returned trimmed."""
    match = _TIME_RE.match(value.strip())
    if not match:
        return value.strip()
    hour, minute, half = int(match.group(1)), match.group(2), match.group(3)
    if half:
        hour = hour % 12 + (12 if half.lower() == "p" else 0)
    return f"{hour:02d}:{minute}"


def _plain(text: str) -> str:
    return " ".join(text.casefold().split())


def fingerprint(message: Stored) -> str:
    parts = (
        message.author,
        _plain(message.sender),
        message.time,
        message.kind,
        _plain(message.text),
        _plain(message.quote),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def from_lines(lines: Sequence[ChatLine], mask: bool = True) -> tuple[Stored, ...]:
    def clean(text: str) -> str:
        return mask_text(text).text if mask else text

    return tuple(
        Stored(
            sent_on=line.day,
            time=normalize_time(line.time),
            author=line.author,
            sender=clean(line.sender),
            kind=line.kind,
            text=clean(line.text),
            quote=clean(line.quote),
        )
        for line in lines
    )


def _same(a: Stored, b: Stored, fa: str, fb: str) -> bool:
    return fa == fb and not (a.sent_on and b.sent_on and a.sent_on != b.sent_on)


def _edited(old: Stored, new: Stored) -> bool:
    """Same message, text changed (edited or deleted): same author, time and kind."""
    return (old.author, old.time, old.kind) == (new.author, new.time, new.kind)


def find_overlap(older: Sequence[Stored], newer: Sequence[Stored]) -> Overlap | None:
    """Where the two runs line up, or None.

    The longest run of equal messages proposes an alignment; it holds only if every other
    pair that lines up with it is the same message too (or that message edited). Two equal
    broadcast messages among different ones are not the same chat. A single equal message
    counts only when the newer run is that one message and it is the last of the older,
    or the older run is that one message.
    """
    if not older or not newer:
        return None
    fo = [fingerprint(m) for m in older]
    fn = [fingerprint(m) for m in newer]
    where = defaultdict(list)
    for i, f in enumerate(fo):
        where[f].append(i)

    def same(i: int, j: int) -> bool:
        return _same(older[i], newer[j], fo[i], fn[j])

    runs: list[Overlap] = []
    for j, f in enumerate(fn):
        for i in where.get(f, ()):
            if not same(i, j) or (i > 0 and j > 0 and same(i - 1, j - 1)):
                continue  # not equal, or not the start of a run
            length = 1
            while i + length < len(fo) and j + length < len(fn) and same(i + length, j + length):
                length += 1
            runs.append(Overlap(i, j, length))

    def consistent(run: Overlap) -> bool:
        shift = run.older_start - run.newer_start  # older index of newer[0]
        for i in range(max(0, shift), min(len(older), len(newer) + shift)):
            j = i - shift
            if not same(i, j) and not _edited(older[i], newer[j]):
                return False
        return True

    def enough(run: Overlap) -> bool:
        if run.length >= 2:
            return True
        last = len(newer) == 1 and run.older_start == len(older) - 1
        return last or len(older) == 1

    # Longer first; on a tie, the one closer to the end of the older run.
    for run in sorted(runs, key=lambda r: (r.length, r.older_start), reverse=True):
        if enough(run) and consistent(run):
            return run
    return None


def _combine(
    older: Sequence[Stored], newer: Sequence[Stored], overlap: Overlap
) -> tuple[list[Stored], int]:
    """Lay newer over older at the overlap. Returns the run and how many were edited."""
    shift = overlap.older_start - overlap.newer_start  # older index of newer[0]
    out, changed = [], 0
    for position in range(min(0, shift), max(len(older), len(newer) + shift)):
        old = older[position] if 0 <= position < len(older) else None
        new = newer[position - shift] if 0 <= position - shift < len(newer) else None
        if old is None or new is None:
            out.append(old or new)
        elif _same(old, new, fingerprint(old), fingerprint(new)):
            out.append(old if old.sent_on or not new.sent_on else new)
        elif _edited(old, new):
            out.append(replace(new, sent_on=old.sent_on or new.sent_on))
            changed += 1
        else:
            out.append(old)  # they disagree beyond an edit: what is stored stays
    return out, changed


def stitch(older: Sequence[Stored], newer: Sequence[Stored]) -> tuple[Stored, ...] | None:
    """Join two runs that overlap, in either direction. None when they do not."""
    overlap = find_overlap(older, newer)
    if overlap is None:
        return None
    return tuple(_combine(older, newer, overlap)[0])


def fill_days(messages: Sequence[Stored]) -> tuple[Stored, ...]:
    """A message without a day is on the day of the one before it."""
    out, day = [], ""
    for message in messages:
        if message.sent_on:
            day = message.sent_on
        elif day:
            message = replace(message, sent_on=day)
        out.append(message)
    return tuple(out)


def merge(segments: Sequence[Sequence[Stored]], incoming: Sequence[Stored]) -> Merge:
    if not incoming:
        return Merge((), (), 0, 0, False)
    touched = [k for k, segment in enumerate(segments) if find_overlap(segment, incoming)]
    if not touched:
        return Merge(fill_days(incoming), (), len(incoming), 0, bool(segments))
    result, changed = list(incoming), 0
    for k in touched:
        overlap = find_overlap(segments[k], result)
        if overlap is None:  # cannot happen: it overlapped incoming, which result holds
            continue
        result, edited = _combine(segments[k], result, overlap)
        changed += edited
    added = len(result) - sum(len(segments[k]) for k in touched)
    return Merge(fill_days(result), tuple(touched), added, changed, False)
