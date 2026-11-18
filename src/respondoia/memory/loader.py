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
MIN_NOTCHES, MAX_NOTCHES = 1, 6  # per step; more while screens overlap a lot
FEW_LINES = 8  # a screen with fewer readable lines (photos fill it) gets short steps
STUCK_LIMIT = 3  # steps in a row that gained nothing after stepping back
PATIENCE = (1.0, 1.5)  # when the top did not move: WhatsApp may still be loading
POLL_S = 0.03  # between reads while waiting for a screen to change
POLL_LIMIT_S = 1.5  # a screen that has not changed by then counts as not moved


@dataclass(frozen=True)
class Pace:
    notch: tuple[float, float]  # seconds between notches; (0, 0): none
    screen: tuple[float, float]  # before reading a screen
    back: tuple[float, float]  # between notches on the way back to the newest messages
    poll: bool  # read again until the screen changes, instead of one read after a pause


# Mouse wheel: the events go through the input queue and WhatsApp must keep up.
WHEEL_PACE = Pace(notch=(0.06, 0.15), screen=(0.35, 0.6), back=(0.02, 0.05), poll=False)
# Accessibility scrolling: no input events; the screen is read as soon as it changes.
BACKGROUND_PACE = Pace(notch=(0.0, 0.0), screen=(0.0, 0.0), back=(0.0, 0.0), poll=True)


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
    pace: Pace = WHEEL_PACE
    # A minimized window stops drawing: scrolls do nothing and the screen stays frozen.
    visible: Callable[[], bool] = lambda: True
    scroll_bottom: Callable[[], None] | None = None  # one call back to the newest messages


@dataclass(frozen=True)
class LoadReport:
    # "limit", "months", "start", "joined", "user", "focus", "covered", "minimized",
    # "chat_changed", "private", "lost" or "unreadable"
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


def _signature(lines: Sequence[ChatLine]) -> tuple:
    """What is on screen and where. It changes when the list moves, even when the readable
    lines stay the same (photos the reader cannot see can fill the top)."""
    return tuple(
        (fingerprint(stored), line.rect)
        for stored, line in zip(from_lines(lines, mask=False), lines)
    )


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
        if not ports.visible():
            raise _Stop("minimized")

    def read(retry: bool = False) -> ChatView:
        view = ports.read()
        waited = 0.0
        # While the list redraws a read can come back empty for a moment.
        while view is None and retry and waited < POLL_LIMIT_S:
            ports.sleep(POLL_S)
            waited += POLL_S
            check()
            view = ports.read()
        if view is None:
            raise _Stop("unreadable")
        if normalize_name(view.chat.title) != normalize_name(title):
            raise _Stop("chat_changed")
        if private(view.chat.title):
            raise _Stop("private")
        return view

    def pause(low: float, high: float) -> None:
        if high > 0:
            ports.sleep(ports.random(low, high))

    def scroll(notches: int, low: float, high: float) -> None:
        nonlocal scrolled
        for _ in range(abs(notches)):
            check()
            ports.scroll(1 if notches > 0 else -1)
            scrolled += 1 if notches > 0 else -1
            pause(low, high)

    def next_screen(before: tuple) -> tuple[ChatLine, ...]:
        """The screen after a scroll. Polling: read until the screen moves."""
        pause(*pace.screen)
        check()
        screen = read(retry=pace.poll).lines
        if not pace.poll:
            return screen
        waited = 0.0
        while waited < POLL_LIMIT_S:
            if _signature(screen) != before:
                return screen
            ports.sleep(POLL_S)
            waited += POLL_S
            check()
            screen = read(retry=True).lines
        return screen

    def report(reason: str) -> LoadReport:
        lines = () if reason == "private" else tuple(block)
        ms = round((time.perf_counter() - started) * 1000)
        return LoadReport(reason, screens, lines, reason == "start", ms)

    pace = ports.pace
    cutoff = months_before(ports.today(), limits.months) if limits.months else ""
    try:
        check()
        block = list(read().lines)
        last_seen = _signature(block)
        screens = 1
        stuck = 0
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
            scroll(notches, *pace.notch)
            screen = next_screen(last_seen)
            stored: Sequence[Stored] = from_lines(screen, mask=False)
            # A new screen overlaps the top of the block: no need to compare the rest.
            known = from_lines(block[: max(len(screen) * 3, 60)], mask=False)
            moved = _signature(screen) != last_seen
            last_seen = _signature(screen)
            if not moved:  # nothing moved: the start of the chat, or WhatsApp still loading
                same_top += 1
                if same_top >= SAME_TOP_READS:
                    raise _Stop("start")
                ports.sleep(ports.random(*PATIENCE))
                continue
            same_top = 0
            overlap = find_overlap(stored, known)
            recovered = overlap is None
            if overlap is None:
                # Too far: step back notch by notch until the screen joins the block again.
                went = notches
                notches = MIN_NOTCHES
                for _ in range(went):
                    scroll(-1, *pace.notch)
                    screen = next_screen(last_seen)
                    last_seen = _signature(screen)
                    stored = from_lines(screen, mask=False)
                    overlap = find_overlap(stored, known)
                    if overlap is not None:
                        break
                if overlap is None:
                    raise _Stop("lost")
            before = len(block)
            block = _stitch(screen, block, overlap)
            screens += 1
            # Stepping back and gaining nothing, again and again: no readable line survives
            # even the shortest step, so the thread cannot be followed.
            if len(block) > before:
                stuck = 0
            elif recovered:
                stuck += 1
                if stuck >= STUCK_LIMIT:
                    raise _Stop("lost")
            if len(screen) < FEW_LINES:
                notches = MIN_NOTCHES
                continue
            # Many lines in common: the step can be longer. Few: shorter, so none is skipped.
            if overlap.length >= 0.6 * len(screen):
                notches = min(MAX_NOTCHES, notches + 1)
            elif overlap.length <= 2:
                notches = max(MIN_NOTCHES, notches - 1)
    except _Stop as stop:
        reason = stop.reason
    if reason in ("limit", "months", "start", "joined", "lost") and scrolled > 0:
        try:
            if ports.scroll_bottom is not None:
                check()
                ports.scroll_bottom()
            else:
                scroll(-scrolled, *pace.back)  # back to the newest messages, interruptible
        except _Stop:
            pass
    return report(reason)
