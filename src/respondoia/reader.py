"""Accessibility reader: contact name, message text and chat bounds."""

import ctypes
import re
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache

from respondoia.domain import PICTURE_KINDS, ChatLine, ChatView, OpenChat, Rect

PANEL_ID = "main"
# The chat header sits in this many pixels at the top of the panel.
HEADER_BAND = 90
# The time under each message. A data format, not a translated label.
TIME_RE = re.compile(r"^\d{1,2}:\d{2}(\s?[AaPp]\.?\s?[Mm]\.?)?$")
# A bubble is narrower than its row, which spans the whole chat. Long messages in a
# narrow window come close to the full width.
_BUBBLE_SHARE = 0.97
# A group sender's name sits this close to the top of the bubble.
_SENDER_BAND = 30
# Smaller pictures inside a bubble are avatars, not photos.
_PHOTO_SIDE = 80
_EMOJI_SIDE = 32  # images smaller than this are emoji drawn inline
_AVATAR_SIDE = 16  # a picture in the header at least this big is the contact's photo
# The site under a link preview. A data format, like the time.
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")

# Day separators show a day as text: a value to read, like a date in an exported file.
# The structure finds them (a centered text between the rows); these tables read them.
_TODAY = {"hoje", "today", "hoy"}
_YESTERDAY = {"ontem", "yesterday", "ayer"}
_WEEKDAYS = {
    "segunda": 0,
    "terça": 1,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sábado": 5,
    "sabado": 5,
    "domingo": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
}
_MONTHS = {
    name: number
    for names in (
        "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro",
        "january february march april may june july august september october november december",
        "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre",
    )
    for number, name in enumerate(names.split(), 1)
}
_NUMERIC_DAY = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})$")
_DAY_MONTH = re.compile(r"^(\d{1,2})(?: de)? ([^\d\s,]+)(?:,? (?:de )?(\d{4}))?$")
_MONTH_DAY = re.compile(r"^([^\d\s,]+) (\d{1,2})(?:,? (\d{4}))?$")

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


@lru_cache(maxsize=1)
def system_month_first() -> bool:
    """True when the Windows short date starts with the month, as in the US."""
    try:
        buffer = ctypes.create_unicode_buffer(80)
        if not ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x1F, buffer, 80):  # SSHORTDATE
            return False
        pattern = buffer.value.casefold()
        return pattern.find("m") < pattern.find("d")
    except (AttributeError, OSError):
        return False


def _month(name: str) -> int | None:
    name = name.strip(".")
    if name in _MONTHS:
        return _MONTHS[name]
    return next(
        (n for full, n in _MONTHS.items() if len(name) >= 3 and full.startswith(name)), None
    )


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_day(text: str, today: date) -> str | None:
    """The ISO date a day separator stands for, or None if it is not one."""
    plain = " ".join(text.casefold().split()).strip(" ,.")
    if not plain:
        return None
    if plain in _TODAY:
        return today.isoformat()
    if plain in _YESTERDAY:
        return (today - timedelta(days=1)).isoformat()
    weekday = _WEEKDAYS.get(plain.replace("-feira", "").replace(" feira", ""))
    if weekday is not None:
        for back in range(1, 7):
            day = today - timedelta(days=back)
            if day.weekday() == weekday:
                return day.isoformat()
    if match := _NUMERIC_DAY.match(plain):
        first, second, year = (int(part) for part in match.groups())
        year += 2000 if year < 100 else 0
        month_first = second > 12 or (first <= 12 and system_month_first())
        month, day = (first, second) if month_first else (second, first)
        return _iso(year, month, day)
    for pattern, order in ((_DAY_MONTH, "dmy"), (_MONTH_DAY, "mdy")):
        if match := pattern.match(plain):
            parts = dict(zip(order, match.groups(), strict=True))
            month = _month(parts["m"])
            if month is None:
                continue
            year = int(parts["y"]) if parts["y"] else today.year
            found = _iso(year, month, int(parts["d"]))
            if found and not parts["y"] and found > today.isoformat():
                found = _iso(year - 1, month, int(parts["d"]))  # a day of last year
            return found
    return None


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

    def in_band(node: UiNode) -> bool:
        return node.rect.top >= panel.rect.top and node.rect.bottom <= band_bottom

    avatar = next(
        (
            n.rect
            for n in _walk(panel)
            if n.control_type == "Image"
            and in_band(n)
            and min(n.rect.width, n.rect.height) >= _AVATAR_SIDE
        ),
        None,
    )
    for node in _walk(panel):
        if node is panel or node.control_type != "Text":
            continue
        if in_band(node):
            # The title is the first text in the header. Status lines come after it.
            if not node.name.strip():
                return None
            top, bottom = _message_area(panel)
            messages = Rect(panel.rect.left, top, panel.rect.right, bottom)
            return OpenChat(node.name, panel.rect, avatar, messages)
    return None


def _message_area(panel: UiNode) -> tuple[int, int]:
    """Top and bottom of the message list: below the header, above the message box."""
    banner = next((c for c in panel.children if c.aria_role == "banner"), None)
    footer = next((c for c in panel.children if c.aria_role == "contentinfo"), None)
    top = banner.rect.bottom if banner else panel.rect.top
    bottom = footer.rect.top if footer else panel.rect.bottom
    return top, bottom


def _has_content(node: UiNode) -> bool:
    return any(
        (n.control_type == "Text" and n.name.strip())
        or n.control_type in ("Slider", "ProgressBar")
        or (n.control_type == "Image" and min(n.rect.width, n.rect.height) >= _PHOTO_SIDE)
        for n in _walk(node)
    )


def _bubble(row: UiNode) -> UiNode | None:
    """The outermost group narrower than the row that holds the message."""
    level, best = [row], None
    while level and best is None:
        level = [c for n in level for c in n.children]
        candidates = [
            n
            for n in level
            if n.control_type == "Group"
            and 0 < n.rect.width < _BUBBLE_SHARE * row.rect.width
            and _has_content(n)
        ]
        if candidates:
            best = max(candidates, key=lambda g: g.rect.width * g.rect.height)
    return best


def _read_row(row: UiNode) -> ChatLine | None:
    bubble = _bubble(row)
    if bubble is None:
        return None  # a placeholder row the list has not drawn yet
    # The side with the smaller margin is where the bubble sits, however wide it is.
    is_user = row.rect.right - bubble.rect.right < bubble.rect.left - row.rect.left
    nodes = [n for n in _walk(bubble) if n is not bubble]

    def texts_in(node: UiNode) -> list[UiNode]:
        return [n for n in _walk(node) if n.control_type == "Text" and n.name.strip()]

    def is_status(node: UiNode) -> bool:
        # In the user's own bubbles the time sits in a button with the read receipt.
        texts = texts_in(node)
        return bool(texts) and all(TIME_RE.match(t.name.strip()) for t in texts)

    buttons = [n for n in nodes if n.control_type == "Button" and texts_in(n)]
    status = [n for n in buttons if is_status(n)]
    in_status = {id(m) for n in status for m in _walk(n)}
    # A document card is a button that holds the message's own time, the lowest one; a
    # quote sits above it. One that also holds the read receipt wraps a message the reader
    # reads as text (seen in the 0.4 probe).
    clocks = [n for n in texts_in(bubble) if TIME_RE.match(n.name.strip())]
    lowest = max(clocks, key=lambda n: (n.rect.bottom, n.rect.right), default=None)
    card = next(
        (
            n
            for n in buttons
            if lowest is not None
            and any(m is lowest for m in _walk(n))
            and not any(id(m) in in_status for m in _walk(n))
        ),
        None,
    )
    # A button holding the status button wraps the whole message: it is not a quote.
    quote = next(
        (
            n
            for n in buttons
            if id(n) not in in_status
            and n is not card
            and not any(id(m) in in_status for m in _walk(n))
        ),
        None,
    )
    quoted = {id(n) for n in _walk(quote)} if quote else set()
    texts = [n for n in texts_in(bubble) if id(n) not in quoted]
    times = [n for n in texts if TIME_RE.match(n.name.strip())]
    # The time is the lowest one; a photo shows it over the picture, a video its length too.
    time_node = max(times, key=lambda n: (n.rect.bottom, n.rect.right), default=None)

    pictures = [
        n
        for n in nodes
        if n.control_type == "Image"
        and min(n.rect.width, n.rect.height) >= _PHOTO_SIDE
        and id(n) not in quoted
    ]
    area = _picture_area(bubble, pictures)
    length = next(
        (n for n in times if n is not time_node and area and _inside(n.rect, area)), None
    )
    body = [n for n in texts if n is not time_node and n is not length]

    sender = ""
    if not is_user:
        first_text_top = min((n.rect.top for n in body), default=bubble.rect.bottom)
        for n in nodes:
            near_top = n.rect.top - bubble.rect.top <= _SENDER_BAND
            # A name is a wide, short button; a play button is square, a picture tall.
            wide = n.rect.width >= 1.5 * n.rect.height and n.rect.height <= _SENDER_BAND
            is_name = n.control_type == "Button" and n is not quote and n.name.strip()
            if is_name and near_top and wide:
                if n.rect.top < first_text_top:
                    sender = n.name.strip()
                break

    others = [n for n in nodes if id(n) not in quoted | in_status and n is not quote]
    site = ""
    links = [n for n in others if n.control_type == "Hyperlink"]
    if links:
        # The site sits under the preview, outside the link. A domain WhatsApp links inside
        # the contact's own text is not a preview.
        linked = {id(m) for n in links for m in _walk(n)}
        site = next(
            (
                n.name.strip()
                for n in body
                if id(n) not in linked and _DOMAIN_RE.match(n.name.strip())
            ),
            "",
        )
    if any(n.control_type in ("Slider", "ProgressBar") for n in nodes):
        kind = "audio"
    elif card is not None and not pictures:
        kind = "document"
    elif pictures:
        # A sticker is one named picture alone; a photo has two unnamed ones, or a caption.
        sticker = not body and len(pictures) == 1 and pictures[0].name.strip()
        kind = "sticker" if sticker else "video" if length else "image"
    elif site:
        kind = "link"
    elif body:
        kind = "text"
    else:
        kind = "other"
    is_picture = kind in PICTURE_KINDS and area is not None
    plain = (
        len(body) == 1
        and not any(n.control_type in ("Hyperlink", "Button") for n in others)
        and not any(
            n.control_type == "Image" and max(n.rect.width, n.rect.height) >= _EMOJI_SIDE
            for n in others
        )
    )
    return ChatLine(
        author="user" if is_user else "contact",
        sender=sender,
        time=time_node.name.strip() if time_node else "",
        kind=kind,
        text="\n".join(n.name.strip() for n in body),
        quote="\n".join(n.name.strip() for n in texts_in(quote)) if quote else "",
        plain=plain,
        rect=area if is_picture else bubble.rect,
        duration=length.name.strip() if length else "",
        site=site if kind == "link" else "",
        shape=f"{area.height / area.width:.1f}" if is_picture else "",
    )


def _picture_area(bubble: UiNode, pictures: list[UiNode]) -> Rect | None:
    """What shows the pictures: the button around each one, which can be smaller than the
    image it clips. Several pictures (an album) give the box around them all."""
    if not pictures:
        return None
    parent = {id(c): n for n in _walk(bubble) for c in n.children}

    def shown(image: UiNode) -> Rect:
        node = image
        while id(node) in parent and parent[id(node)] is not bubble:
            node = parent[id(node)]
            if node.control_type == "Button":
                return node.rect
        return image.rect

    rects = [shown(p) for p in pictures]
    return Rect(
        min(r.left for r in rects),
        min(r.top for r in rects),
        max(r.right for r in rects),
        max(r.bottom for r in rects),
    )


def _inside(inner: Rect, outer: Rect) -> bool:
    return (
        outer.left <= inner.left
        and inner.right <= outer.right
        and outer.top <= inner.top
        and inner.bottom <= outer.bottom
    )


def _rows_and_texts(node: UiNode) -> Iterator[UiNode]:
    """Rows, and the texts outside rows, in document order."""
    if node.aria_role == "row":
        yield node
        return
    if node.control_type == "Text" and node.name.strip():
        yield node
    for child in node.children:
        yield from _rows_and_texts(child)


def read_lines(panel: UiNode, today: date | None = None) -> tuple[ChatLine, ...]:
    """Visible messages of the open chat, oldest first, by structure and position only.

    A centered text between rows that reads as a day dates the rows after it. The floating
    one above the list comes first in document order and dates the topmost rows.
    """
    today = today or date.today()
    top, bottom = _message_area(panel)
    middle = (panel.rect.left + panel.rect.right) / 2
    lines, day = [], ""
    for item in _rows_and_texts(panel):
        if item.aria_role != "row":
            centered = (
                abs((item.rect.left + item.rect.right) / 2 - middle) <= 0.05 * panel.rect.width
            )
            narrow = item.rect.width <= 0.3 * panel.rect.width
            found = parse_day(item.name, today) if centered and narrow else None
            day = found or day
            continue
        if not top <= (item.rect.top + item.rect.bottom) / 2 <= bottom:
            continue  # scrolled out, or hidden under the header
        line = _read_row(item)
        if line is not None:
            lines.append(replace(line, day=day))
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
