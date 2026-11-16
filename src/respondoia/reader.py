"""Accessibility reader: contact name, message text and chat bounds."""

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from respondoia.domain import OpenChat, Rect

PANEL_ID = "main"
# The chat header sits in this many pixels at the top of the panel.
HEADER_BAND = 90

# UI Automation ids: properties, tree scopes and the control types the tree uses.
_P_RECT, _P_TYPE, _P_NAME, _P_AUTOMATION_ID, _P_ARIA_ROLE = 30001, 30003, 30005, 30011, 30101
_SCOPE_ELEMENT, _SCOPE_DESCENDANTS, _SCOPE_SUBTREE = 1, 4, 7
_CONTROL_TYPES = {
    50000: "Button",
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
        if node is panel or node.control_type != "Text" or not node.name:
            continue
        if node.rect.top >= panel.rect.top and node.rect.bottom <= band_bottom:
            # The title is the first text in the header. Status lines come after it.
            return OpenChat(node.name, panel.rect) if node.name.strip() else None
    return None


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
