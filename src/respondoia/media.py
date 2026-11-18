"""Which photos of the open chat may go with a request. Pure functions: no capture here."""

from collections.abc import Sequence

from respondoia.config import Settings
from respondoia.domain import PICTURE_KINDS, ChatLine, OpenChat, Rect

MAX_PICTURES = 2


def pending_pictures(lines: Sequence[ChatLine], limit: int = MAX_PICTURES) -> tuple[int, ...]:
    """Indexes of the contact's photos and videos after the user's last line, the newest
    `limit` of them, oldest first. Stickers and documents never go as pictures."""
    last_user = max((i for i, line in enumerate(lines) if line.author == "user"), default=-1)
    found = [
        i
        for i, line in enumerate(lines)
        if i > last_user
        and line.author == "contact"
        and line.kind in PICTURE_KINDS
        and line.rect is not None
    ]
    return tuple(found[-limit:]) if limit > 0 else ()


def hashable(lines: Sequence[ChatLine], area: Rect | None) -> tuple[int, ...]:
    """Indexes of the pictures without text whose hash helps the history: whole inside the
    message list, since a picture cut by the header would hash differently."""
    return tuple(
        i
        for i, line in enumerate(lines)
        if line.shape
        and not line.text
        and line.rect is not None
        and area is not None
        and area.left <= line.rect.left
        and line.rect.right <= area.right
        and area.top <= line.rect.top
        and line.rect.bottom <= area.bottom
    )


def may_send_pictures(
    settings: Settings, chat: OpenChat, lines: Sequence[ChatLine], known_group: bool
) -> bool:
    """Only in a one-to-one chat, in text mode, with the switch on. A group is known from
    the stored contact or from a sender's name on screen: a screen of photos alone in a
    group shows no name."""
    return (
        settings.send_pictures
        and settings.input == "text"
        and chat.messages is not None
        and not known_group
        and not any(line.sender for line in lines)
    )
