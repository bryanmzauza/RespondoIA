import ctypes

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


def test_pause_stops_too(qtbot, monkeypatch):
    monkeypatch.setattr(scroll, "_register", lambda hwnd, remove: True)
    guard = scroll.InputGuard()
    guard.arm()
    guard.stop()
    assert guard.stopped()
    guard.arm()
    assert not guard.stopped()
