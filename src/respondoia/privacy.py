"""Masking, private contacts, and the checks that run before sending and before pasting."""

import unicodedata
from collections.abc import Iterable
from enum import Enum

from respondoia.domain import ChatRef, OpenChat


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


def check_before_paste(captured: ChatRef, current: ChatRef | None, insert_mode: str) -> PasteDecision:
    if insert_mode != "paste" or current is None or current.hwnd != captured.hwnd:
        return PasteDecision.COPY_ONLY
    if normalize_name(current.title) != normalize_name(captured.title):
        return PasteDecision.COPY_ONLY
    return PasteDecision.PASTE
