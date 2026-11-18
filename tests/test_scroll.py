import ctypes

import pytest

from respondoia.memory import scroll


def test_wheel_events_carry_the_mark():
    up = scroll.wheel_input(3)
    assert up.type == 0 and up.union.mi.dwFlags == 0x0800
    assert up.union.mi.mouseData == 360 and up.union.mi.dwExtraInfo == scroll.MARK
    down = scroll.wheel_input(-1)
    assert ctypes.c_int32(down.union.mi.mouseData).value == -120


def test_wheel_sends_one_event(monkeypatch):
    sent = []

    class User32:
        @staticmethod
        def SendInput(count, pointer, size):
            sent.append((count, size))
            return 1

    monkeypatch.setattr(ctypes.windll, "user32", User32, raising=False)
    scroll.wheel(1)
    assert sent == [(1, ctypes.sizeof(scroll._Input))]


def test_guard_ignores_injected_and_stops_on_real(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: True)
    guard = scroll.InputGuard()
    assert guard.arm(delay_s=0)
    guard.on_device(0)
    guard.on_device(0)
    assert not guard.stopped() and guard.injected == 2
    guard.on_device(1234)
    assert guard.stopped() and guard.real == 1
    guard.disarm()


def test_unreadable_input_counts_as_a_person(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: True)
    guard = scroll.InputGuard()
    guard.arm(delay_s=0)
    guard.on_device(None)
    assert guard.stopped()


def test_the_click_that_started_the_load_is_ignored(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: True)
    guard = scroll.InputGuard()
    guard.arm(delay_s=60)
    guard.on_device(1234)
    assert not guard.stopped()


def test_nothing_counts_while_disarmed(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: True)
    guard = scroll.InputGuard()
    guard.on_device(1234)
    assert not guard.stopped()


def test_arming_fails_when_raw_input_cannot_register(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: False)
    assert not scroll.InputGuard().arm()


class FakePattern:
    """The ScrollPattern of the message list: percent of the way down, view in percent."""

    def __init__(self, percent=100.0, view=25.0, grows=0):
        self.CurrentVerticalScrollPercent = percent
        self.CurrentVerticalViewSize = view
        self.calls = []
        self.grows = grows  # times the list grows after reaching the bottom

    def SetScrollPercent(self, horizontal, vertical):
        self.calls.append((horizontal, vertical))
        if vertical >= 100 and self.grows:
            self.grows -= 1
            self.CurrentVerticalScrollPercent = 90.0  # older messages loaded meanwhile
        else:
            self.CurrentVerticalScrollPercent = vertical


def test_accessible_scroll_moves_up_by_part_of_a_screen():
    pattern = FakePattern(percent=100.0, view=25.0)
    scroller = scroll.AccessibleScroll(pattern, sleep=lambda s: None)
    scroller.up(0.5)
    # Half a screen of a list four screens long: 0.5 * 25 / 75 of the way, in percent.
    assert pattern.calls == [(-1, pytest.approx(100 - 50 * 25 / 75))]


def test_accessible_scroll_stops_at_the_top():
    pattern = FakePattern(percent=1.0, view=25.0)
    scroll.AccessibleScroll(pattern, sleep=lambda s: None).up(0.3)
    assert pattern.calls[-1] == (-1, 0)


def test_accessible_scroll_reaches_the_bottom_even_when_the_list_grows():
    pattern = FakePattern(percent=40.0, grows=2)
    scroll.AccessibleScroll(pattern, sleep=lambda s: None).bottom()
    assert pattern.CurrentVerticalScrollPercent == 100 and len(pattern.calls) == 3


class GrowingPattern(FakePattern):
    """Older messages arriving: position and share move for a few readings, then settle.
    Each reading of the position moves to the next pair."""

    def __init__(self, readings):
        super().__init__()
        self._readings = list(readings)
        self._current = self._readings[0]

    def __getattribute__(self, name):
        if name == "CurrentVerticalScrollPercent":
            readings = object.__getattribute__(self, "_readings")
            current = readings.pop(0) if len(readings) > 1 else readings[0]
            object.__setattr__(self, "_current", current)
            return current[0]
        if name == "CurrentVerticalViewSize":
            return object.__getattribute__(self, "_current")[1]
        return object.__getattribute__(self, name)

    def SetScrollPercent(self, horizontal, vertical):
        self.calls.append((horizontal, vertical))


def test_accessible_scroll_waits_for_the_list_to_settle():
    pattern = GrowingPattern([(3.8, 8.4), (28.1, 10.8), (5.1, 9.8), (5.1, 9.8), (5.1, 9.8)])
    scroll.AccessibleScroll(pattern, sleep=lambda s: None).up(0.3)
    # Computed from the settled position and share, not from the first reading.
    assert pattern.calls == [(-1, pytest.approx(5.1 - 30 * 9.8 / 90.2))]


def test_pause_is_known_apart_from_real_input(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: True)
    guard = scroll.InputGuard()
    guard.arm(delay_s=0)
    guard.on_device(1234)
    assert guard.stopped() and not guard.paused()
    guard.stop()
    assert guard.paused()


def test_pause_stops_too(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: True)
    guard = scroll.InputGuard()
    guard.arm()
    guard.stop()
    assert guard.stopped()
    guard.arm()
    assert not guard.stopped()
