"""Scroll the open chat up and read each screen. No Win32 and no Qt here: the scroll, the
reading and the stop signal come in as ports.

The loader only scrolls. Before every event it checks that the user has not touched the
mouse or keyboard and that WhatsApp is still in front; if either fails it stops at once
and sends nothing more. Every screen must overlap the one before, so nothing is skipped.
"""

import calendar
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date

from respondoia.domain import ChatLine, ChatView
from respondoia.memory.merge import Overlap, Stored, find_overlap, fingerprint, from_lines
from respondoia.privacy import normalize_name

SAME_TOP_READS = 4  # the top did not move this many times: the start of the chat
MIN_NOTCHES, MAX_NOTCHES = 2, 6  # per step; more while screens overlap a lot
NOTCH_PAUSE = (0.06, 0.15)  # seconds between wheel notches
SCREEN_PAUSE = (0.35, 0.6)  # before reading a screen
PATIENCE = (1.0, 1.5)  # when the top did not move: WhatsApp may still be loading
BACK_PAUSE = (0.02, 0.05)  # scrolling back to the newest messages, nothing to read


@dataclass(frozen=True)
class LoadLimits:
    screens: int  # quick_load_screens or max_load_screens
    months: int  # max_load_months; 0 = no limit by date
    notches: int = 3  # to start with; the step adapts to the overlap
    stop_on_join: bool = False  # gap mode: stop once the block reaches stored history


@dataclass(frozen=True)
class LoaderPorts:
    read: Callable[[], ChatView | None]
    scroll: Callable[[int], None]  # notches, positive = up (older)
    foreground: Callable[[], int]
    stopped: Callable[[], bool]  # real user input or pause since the guard was armed
    sleep: Callable[[float], None]
    random: Callable[[float, float], float]
    today: Callable[[], date]
    # Wheel events go to the window under the cursor: it must be the chat's own window.
    under_cursor: Callable[[], bool] = lambda: True


@dataclass(frozen=True)
class LoadReport:
    # "limit", "months", "start", "joined", "user", "focus", "covered", "chat_changed",
    # "private", "lost" or "unreadable"
    reason: str
    screens: int
    lines: tuple[ChatLine, ...]  # everything read, stitched, oldest first; never another chat
    reached_start: bool
    ms: int


def months_before(today: date, months: int) -> str:
    index = today.year * 12 + today.month - 1 - months
    year, month = divmod(index, 12)
    day = min(today.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day).isoformat()


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _stitch(
    screen: Sequence[ChatLine], block: Sequence[ChatLine], overlap: Overlap
) -> list[ChatLine]:
    """Lay the block over the screen read above it. A day seen on either side is kept."""
    shift = overlap.older_start - overlap.newer_start  # screen index of block[0]
    out = []
    for position in range(min(0, shift), max(len(screen), len(block) + shift)):
        upper = screen[position] if 0 <= position < len(screen) else None
        lower = block[position - shift] if 0 <= position - shift < len(block) else None
        line = lower or upper
        if lower and upper and not lower.day and upper.day:
            line = replace(lower, day=upper.day)
        out.append(line)
    return out


def load(
    hwnd: int,
    title: str,
    limits: LoadLimits,
    ports: LoaderPorts,
    joins: Callable[[tuple[ChatLine, ...]], bool] = lambda block: False,
    private: Callable[[str], bool] = lambda title: False,
) -> LoadReport:
    started = time.perf_counter()
    block: list[ChatLine] = []
    screens = 0
    scrolled = 0

    def check() -> None:
        if ports.stopped():
            raise _Stop("user")
        if ports.foreground() != hwnd:
            raise _Stop("focus")
        if not ports.under_cursor():
            raise _Stop("covered")

    def read() -> ChatView:
        view = ports.read()
        if view is None:
            raise _Stop("unreadable")
        if normalize_name(view.chat.title) != normalize_name(title):
            raise _Stop("chat_changed")
        if private(view.chat.title):
            raise _Stop("private")
        return view

    def scroll(notches: int, low: float, high: float) -> None:
        nonlocal scrolled
        for _ in range(abs(notches)):
            check()
            ports.scroll(1 if notches > 0 else -1)
            scrolled += 1 if notches > 0 else -1
            ports.sleep(ports.random(low, high))

    def report(reason: str) -> LoadReport:
        lines = () if reason == "private" else tuple(block)
        ms = round((time.perf_counter() - started) * 1000)
        return LoadReport(reason, screens, lines, reason == "start", ms)

    cutoff = months_before(ports.today(), limits.months) if limits.months else ""
    try:
        check()
        block = list(read().lines)
        screens = 1
        same_top = 0
        notches = limits.notches
        while True:
            if screens >= limits.screens:
                raise _Stop("limit")
            days = [line.day for line in block if line.day]
            if cutoff and days and min(days) < cutoff:
                raise _Stop("months")
            if limits.stop_on_join and joins(tuple(block)):
                raise _Stop("joined")
            scroll(notches, *NOTCH_PAUSE)
            ports.sleep(ports.random(*SCREEN_PAUSE))
            check()
            screen = read().lines
            stored: Sequence[Stored] = from_lines(screen, mask=False)
            # A new screen overlaps the top of the block: no need to compare the rest.
            known = from_lines(block[: max(len(screen) * 3, 60)], mask=False)
            if stored and known and fingerprint(stored[0]) == fingerprint(known[0]):
                same_top += 1
                if same_top >= SAME_TOP_READS:
                    raise _Stop("start")
                ports.sleep(ports.random(*PATIENCE))
                continue
            same_top = 0
            overlap = find_overlap(stored, known)
            if overlap is None:
                notches = max(MIN_NOTCHES, notches - 1)
                scroll(-1, *NOTCH_PAUSE)  # one notch back, in case the list jumped
                check()
                screen = read().lines
                stored = from_lines(screen, mask=False)
                overlap = find_overlap(stored, known)
                if overlap is None:
                    raise _Stop("lost")
            block = _stitch(screen, block, overlap)
            screens += 1
            # Many lines in common: the step can be longer. Few: shorter, so none is skipped.
            if overlap.length >= 0.6 * len(screen):
                notches = min(MAX_NOTCHES, notches + 1)
            elif overlap.length <= 2:
                notches = max(MIN_NOTCHES, notches - 1)
    except _Stop as stop:
        reason = stop.reason
    if reason in ("limit", "months", "start", "joined", "lost") and scrolled > 0:
        try:
            scroll(-scrolled, *BACK_PAUSE)  # back to the newest messages, still interruptible
        except _Stop:
            pass
    return report(reason)
