"""Accessibility reader: contact name, message text and chat bounds."""

import re
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from respondoia.domain import ChatLine, ChatView, OpenChat, Rect

PANEL_ID = "main"
# The chat header sits in this many pixels at the top of the panel.
HEADER_BAND = 90
# The time under each message. A data format, not a translated label.
TIME_RE = re.compile(r"^\d{1,2}:\d{2}(\s?[AaPp]\.?\s?[Mm]\.?)?$")
# A bubble is narrower than this share of its row; the row spans the whole chat.
_BUBBLE_SHARE = 0.75
# A group sender's name sits this close to the top of the bubble.
_SENDER_BAND = 30
# Smaller pictures inside a bubble are avatars, not photos.
_PHOTO_SIDE = 80

# UI Automation ids: properties, tree scopes and the control types the tree uses.
_P_RECT, _P_TYPE, _P_NAME, _P_AUTOMATION_ID, _P_ARIA_ROLE = 30001, 30003, 30005, 30011, 30101
_SCOPE_ELEMENT, _SCOPE_DESCENDANTS, _SCOPE_SUBTREE = 1, 4, 7
_CONTROL_TYPES = {
    50000: "Button",
    50012: "ProgressBar",
    50015: "Slider",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50025: "Custom",
    50026: "Group",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50032: "Window",
    50033: "Pane",
}


@dataclass(frozen=True)
class UiNode:
    control_type: str  # "Text", "Button", "Group", "Edit", "Document", ...
    name: str
    automation_id: str
    aria_role: str
    rect: Rect
    children: tuple["UiNode", ...]


def node_from_dict(data: Mapping) -> UiNode:
    return UiNode(
        control_type=data["control_type"],
        name=data.get("name", ""),
        automation_id=data.get("automation_id", ""),
        aria_role=data.get("aria_role", ""),
        rect=Rect(*data["rect"]),
        children=tuple(node_from_dict(child) for child in data.get("children", ())),
    )


def _walk(node: UiNode) -> Iterator[UiNode]:
    """Document order: a node, then its children left to right."""
    yield node
    for child in node.children:
        yield from _walk(child)


def find_open_chat(root: UiNode) -> OpenChat | None:
    """Locate the open chat by structure and geometry, never by label text.

    Labels change with the interface language; ids, control types and positions do not.
    """
    panel = next((node for node in _walk(root) if node.automation_id == PANEL_ID), None)
    if panel is None:
        return None
    band_bottom = panel.rect.top + HEADER_BAND
    for node in _walk(panel):
        if node is panel or node.control_type != "Text":
            continue
        if node.rect.top >= panel.rect.top and node.rect.bottom <= band_bottom:
            # The title is the first text in the header. Status lines come after it.
            return OpenChat(node.name, panel.rect) if node.name.strip() else None
    return None


def _message_area(panel: UiNode) -> tuple[int, int]:
    """Top and bottom of the message list: below the header, above the message box."""
    banner = next((c for c in panel.children if c.aria_role == "banner"), None)
    footer = next((c for c in panel.children if c.aria_role == "contentinfo"), None)
    top = banner.rect.bottom if banner else panel.rect.top
    bottom = footer.rect.top if footer else panel.rect.bottom
    return top, bottom


def _read_row(row: UiNode) -> ChatLine | None:
    groups = [n for n in _walk(row) if n is not row and n.control_type == "Group"]
    narrow = [g for g in groups if 0 < g.rect.width < _BUBBLE_SHARE * row.rect.width]
    if not narrow:
        return None  # a placeholder row the list has not drawn yet
    bubble = max(narrow, key=lambda g: g.rect.width * g.rect.height)
    is_user = (bubble.rect.left + bubble.rect.right) / 2 > (row.rect.left + row.rect.right) / 2
    nodes = [n for n in _walk(bubble) if n is not bubble]

    def texts_in(node: UiNode) -> list[UiNode]:
        return [n for n in _walk(node) if n.control_type == "Text" and n.name.strip()]

    quote = next((n for n in nodes if n.control_type == "Button" and texts_in(n)), None)
    quoted = {id(n) for n in _walk(quote)} if quote else set()
    texts = [n for n in texts_in(bubble) if id(n) not in quoted]
    times = [n for n in texts if TIME_RE.match(n.name.strip())]
    time_node = times[-1] if times else None
    body = [n for n in texts if n is not time_node]

    sender = ""
    if not is_user:
        first_text_top = min((n.rect.top for n in body), default=bubble.rect.bottom)
        for n in nodes:
            near_top = n.rect.top - bubble.rect.top <= _SENDER_BAND
            if n.control_type == "Button" and n is not quote and n.name.strip() and near_top:
                if n.rect.top < first_text_top:
                    sender = n.name.strip()
                break

    if any(n.control_type in ("Slider", "ProgressBar") for n in nodes):
        kind = "audio"
    elif any(
        n.control_type == "Image" and min(n.rect.width, n.rect.height) >= _PHOTO_SIDE
        for n in nodes
    ):
        kind = "image"
    elif body:
        kind = "text"
    else:
        kind = "other"
    return ChatLine(
        author="user" if is_user else "contact",
        sender=sender,
        time=time_node.name.strip() if time_node else "",
        kind=kind,
        text="\n".join(n.name.strip() for n in body),
        quote="\n".join(n.name.strip() for n in texts_in(quote)) if quote else "",
    )


def read_lines(panel: UiNode) -> tuple[ChatLine, ...]:
    """Visible messages of the open chat, oldest first, by structure and position only."""
    top, bottom = _message_area(panel)
    lines = []
    for row in _walk(panel):
        if row.aria_role != "row":
            continue
        if not top <= (row.rect.top + row.rect.bottom) / 2 <= bottom:
            continue  # scrolled out, or hidden under the header
        line = _read_row(row)
        if line is not None:
            lines.append(line)
    return tuple(lines)


def find_chat_view(root: UiNode) -> ChatView | None:
    chat = find_open_chat(root)
    if chat is None:
        return None
    panel = next(node for node in _walk(root) if node.automation_id == PANEL_ID)
    return ChatView(chat, read_lines(panel))


def _copy(element, budget: list[int], deadline: float) -> UiNode:
    budget[0] -= 1
    children = []
    cached = element.GetCachedChildren()
    for index in range(cached.Length if cached else 0):
        if budget[0] <= 0 or time.monotonic() > deadline:
            break
        children.append(_copy(cached.GetElement(index), budget, deadline))
    rect = element.CachedBoundingRectangle
    return UiNode(
        control_type=_CONTROL_TYPES.get(element.CachedControlType, str(element.CachedControlType)),
        name=element.CachedName or "",
        automation_id=element.CachedAutomationId or "",
        aria_role=element.GetCachedPropertyValue(_P_ARIA_ROLE) or "",
        rect=Rect(rect.left, rect.top, rect.right, rect.bottom),
        children=tuple(children),
    )


def snapshot(hwnd: int, max_nodes: int = 3000, timeout_s: float = 2.0) -> UiNode | None:
    """Copy the chat panel of a window into plain data. None if it cannot be read."""
    import comtypes
    import comtypes.client

    try:
        comtypes.CoInitializeEx()
    except OSError:
        pass  # this thread already has COM in another mode, as the Qt thread does
    deadline = time.monotonic() + timeout_s
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia_types

        uia = comtypes.client.CreateObject(
            uia_types.CUIAutomation, interface=uia_types.IUIAutomation
        )

        def request(scope: int):
            cache = uia.CreateCacheRequest()
            for prop in (_P_RECT, _P_TYPE, _P_NAME, _P_AUTOMATION_ID, _P_ARIA_ROLE):
                cache.AddProperty(prop)
            cache.TreeScope = scope
            # The panel is a plain group, absent from the default control view.
            cache.TreeFilter = uia.RawViewCondition
            return cache

        is_panel = uia.CreatePropertyCondition(_P_AUTOMATION_ID, PANEL_ID)
        panel = None
        for attempt in range(2):
            window = uia.ElementFromHandle(hwnd)
            panel = window.FindFirstBuildCache(
                _SCOPE_DESCENDANTS, is_panel, request(_SCOPE_ELEMENT)
            )
            if panel or attempt:
                break
            time.sleep(0.3)  # the web view builds its accessibility tree on first contact
        if not panel or time.monotonic() > deadline:
            return None
        tree = panel.BuildUpdatedCache(request(_SCOPE_SUBTREE))
        return _copy(tree, [max_nodes], deadline)
    except (comtypes.COMError, OSError):
        return None


def read_open_chat(hwnd: int) -> OpenChat | None:
    tree = snapshot(hwnd)
    return find_open_chat(tree) if tree is not None else None


def read_chat_view(hwnd: int) -> ChatView | None:
    tree = snapshot(hwnd)
    return find_chat_view(tree) if tree is not None else None
