"""Which stored contact is the open chat. The title alone cannot tell two people with the
same name apart, so memory is only used when something local decides it: the screen
continues that contact's stored messages, a real photo matches only that contact, the user
said so, or nothing is stored under that name, so nothing could leak.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from respondoia.memory.merge import Stored, find_overlap


@dataclass(frozen=True)
class Candidate:
    contact_id: int
    first_seen: str
    avatar: str  # "" when unknown or the default picture
    recent: tuple[Stored, ...]  # last stored messages; () when nothing is stored
    has_memory: bool
    confirmed: bool  # the user said "same person" for this one


@dataclass(frozen=True)
class Identity:
    contact_id: int | None
    reason: str  # "new", "only", "overlap", "photo", "user", "ambiguous" or "different"
    candidates: tuple[int, ...]  # for the question in the panel


def identify(candidates: Sequence[Candidate], screen: Sequence[Stored], avatar: str) -> Identity:
    ids = tuple(c.contact_id for c in candidates)

    def decided(candidate: Candidate, reason: str) -> Identity:
        return Identity(candidate.contact_id, reason, ids)

    def undecided(reason: str = "ambiguous") -> Identity:
        return Identity(None, reason, ids)

    if not candidates:
        return Identity(None, "new", ())
    confirmed = [c for c in candidates if c.confirmed]
    if len(confirmed) == 1:
        return decided(confirmed[0], "user")
    continuing = [c for c in candidates if c.recent and find_overlap(c.recent, screen)]
    if len(continuing) == 1:
        return decided(continuing[0], "overlap")
    if continuing:
        return undecided()
    holding = [c for c in candidates if c.recent or c.has_memory]
    if holding:
        if avatar:
            same_photo = [c for c in candidates if c.avatar == avatar]
            if len(same_photo) == 1 and same_photo[0] in holding:
                return decided(same_photo[0], "photo")
            if len(candidates) == 1 and candidates[0].avatar:
                return undecided("different")
        return undecided()
    if len(candidates) == 1:
        return decided(candidates[0], "only")
    return undecided()
