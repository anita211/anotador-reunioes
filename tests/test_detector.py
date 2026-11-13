from anotador.detector import MeetingDetector


def make():
    return MeetingDetector(title_wait_seconds=30, stop_grace_seconds=15)


def test_starts_when_mic_and_title():
    d = make()
    assert d.update(0, True, "Meet - abc") == "start"
    assert d.recording is True
    assert d.title == "Meet - abc"


def test_title_appears_within_window():
    d = make()
    assert d.update(0, True, None) is None
    assert d.update(3, True, None) is None
    assert d.update(27, True, "Meet - abc") == "start"


def test_no_meet_title_within_window_never_starts():
    d = make()
    for t in range(0, 34, 3):
        assert d.update(t, True, None) is None
    assert d.update(36, True, "Meet - abc") is None
    assert d.recording is False


def test_stops_after_grace():
    d = make()
    assert d.update(0, True, "Meet - abc") == "start"
    assert d.update(100, False, None) is None
    assert d.update(115, False, None) is None
    assert d.update(116, False, None) == "stop"
    assert d.recording is False


def test_short_mic_gap_keeps_recording():
    d = make()
    assert d.update(0, True, "Meet - abc") == "start"
    events = [
        d.update(100, False, None),
        d.update(110, True, "Meet - abc"),
        d.update(200, False, None),
        d.update(214, False, None),
    ]
    assert "stop" not in events
    assert d.recording is True


def test_cancel_suppresses_restart_until_mic_off():
    d = make()
    assert d.update(0, True, "Meet - abc") == "start"
    d.cancel()
    assert d.recording is False
    assert d.update(3, True, "Meet - abc") is None
    assert d.update(6, False, None) is None
    assert d.update(9, True, "Meet - abc") == "start"


def test_no_mic_no_start():
    assert make().update(0, False, "Meet - x") is None


def test_force_start_ignores_mic_off():
    d = make()
    d.force_start(0)
    assert d.recording is True
    for t in range(3, 61, 3):
        assert d.update(t, False, None) != "stop"
    d.force_stop()
    assert d.recording is False


def test_after_stop_new_meeting_can_start():
    d = make()
    d.update(0, True, "Meet - a")
    d.update(10, False, None)
    assert d.update(26, False, None) == "stop"
    assert d.update(30, True, "Meet - b") == "start"
    assert d.title == "Meet - b"
