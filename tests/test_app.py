import queue
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anotador.app import HOLD_SECONDS, Controller
from anotador.config import Config
from anotador.detector import MeetingDetector
from anotador.recorder import Recording


class FakeRecorder:
    def __init__(self, folder: Path, fail=False):
        self.folder = folder
        self.active = False
        self.fail = fail
        self.cancelled = 0
        self.starts = 0
        self.is_broken = False

    def broken(self):
        return self.is_broken

    def start(self):
        if self.fail:
            raise OSError("sem dispositivo")
        self.active = True
        self.is_broken = False
        self.starts += 1

    def stop(self):
        self.active = False
        n = self.starts
        rec = Recording(self.folder / f"{n}-system.wav", self.folder / f"{n}-mic.wav",
                        datetime(2026, 11, 13) + timedelta(minutes=n))
        rec.system_wav.write_bytes(b"")
        rec.mic_wav.write_bytes(b"")
        return rec

    def cancel(self):
        self.active = False
        self.cancelled += 1


class Probe:
    def __init__(self):
        self.mic, self.title, self.render = False, None, False


@pytest.fixture
def make(tmp_path):
    def _make(fail=False):
        probe, warns, q = Probe(), [], queue.Queue()
        c = Controller(
            Config(),
            MeetingDetector(30, 15),
            FakeRecorder(tmp_path, fail),
            q,
            warn=lambda on_cancel, secs: warns.append((on_cancel, secs)),
            mic_probe=lambda exe: probe.mic,
            title_probe=lambda exe, kw: probe.title,
            render_probe=lambda exe: probe.render,
        )
        return c, probe, warns, q
    return _make


def run(c, start, end, step=3):
    t = start
    while t <= end:
        c.tick(t)
        t += step
    return t


def test_start_records_and_warns_once(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.tick(3)
    assert c.recorder.active
    assert len(warns) == 1 and warns[0][1] == 15.0


def test_stop_is_held_then_released_as_one_job(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    probe.mic, probe.title = False, None
    c.tick(10)
    c.tick(26)
    assert not c.recorder.active
    assert q.empty()  # espera um pouco: pode ser só uma pausa
    c.tick(26 + HOLD_SECONDS + 1)
    parts, title = q.get_nowait()
    assert title == "Meet - abc" and len(parts) == 1


def test_short_drop_merges_parts_into_one_job(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    probe.mic, probe.title = False, None
    c.tick(10)
    c.tick(26)  # parou
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(33)  # voltou 7 s depois (caso de 16/11)
    assert c.recorder.active and len(warns) == 1
    probe.mic, probe.title = False, None
    c.tick(900)
    c.tick(916)
    c.tick(916 + HOLD_SECONDS + 1)
    parts, title = q.get_nowait()
    assert len(parts) == 2 and title == "Meet - abc"
    assert q.empty()


def test_broken_device_restart_stays_in_same_job(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.recorder.is_broken = True
    c.tick(3)
    assert c.recorder.active and c.recorder.starts == 2 and len(warns) == 1
    assert q.empty()
    probe.mic, probe.title = False, None
    c.tick(100)
    c.tick(116)
    c.tick(116 + HOLD_SECONDS + 1)
    parts, _ = q.get_nowait()
    assert len(parts) == 2


def test_cancel_discards_whole_job_including_earlier_parts(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.recorder.is_broken = True
    c.tick(3)  # parte 1 guardada, parte 2 gravando (caso de 17/11 09:05)
    held = list(c._parts)
    assert held
    warns[0][0]()  # Cancelar gravação
    assert c.recorder.cancelled == 1 and not c.recorder.active
    run(c, 6, 6 + HOLD_SECONDS + 10)
    assert q.empty()
    assert all(not r.mic_wav.exists() and not r.system_wav.exists() for r in held)


def test_new_meeting_releases_previous_job_immediately(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - a"
    c.tick(0)
    probe.mic, probe.title = False, None
    c.tick(10)
    c.tick(26)
    probe.mic, probe.title = True, "Meet - b"
    c.tick(40)
    parts, title = q.get_nowait()
    assert title == "Meet - a" and len(parts) == 1
    assert c.recorder.active and len(warns) == 2


def test_playing_call_audio_keeps_recording_while_muted(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    probe.mic, probe.title, probe.render = False, None, True  # mudo, mas ouvindo os outros
    run(c, 3, 600)
    assert c.recorder.active and q.empty()
    probe.render = False
    run(c, 603, 603 + 15 + HOLD_SECONDS + 6)
    assert not c.recorder.active
    assert len(q.get_nowait()[0]) == 1


def test_render_alone_does_not_start(make):
    c, probe, warns, q = make()
    probe.render, probe.title = True, "Meet - abc"
    run(c, 0, 60)
    assert not c.recorder.active


def test_cancel_from_warning_discards(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    warns[0][0]()
    c.tick(3)
    assert c.recorder.cancelled == 1 and not c.recorder.active
    assert q.empty()


def test_title_probe_only_when_mic_on(make):
    calls = []
    c, probe, warns, q = make()
    c._title_probe = lambda exe, kw: calls.append(1)
    c.tick(0)
    assert calls == []


def test_recorder_failure_does_not_crash_and_resets(make):
    c, probe, warns, q = make(fail=True)
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    assert not c.detector.recording
    assert warns == []


def test_manual_stop_releases_job_immediately(make):
    c, probe, warns, q = make()
    c.manual_start(0)
    assert c.recorder.active and len(warns) == 1
    c.manual_stop()
    assert not c.recorder.active
    parts, title = q.get_nowait()
    assert title is None and len(parts) == 1


def test_sleep_gap_holds_and_wake_in_call_continues_same_job(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.tick(3 + 600)  # dormiu e acordou ainda na chamada
    assert c.recorder.active and len(warns) == 1
    probe.mic, probe.title = False, None
    run(c, 606, 606 + 15 + HOLD_SECONDS + 6)
    parts, title = q.get_nowait()
    assert len(parts) == 2 and title == "Meet - abc"


def test_sleep_gap_call_over_releases_after_hold(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    probe.mic, probe.title = False, None
    c.tick(3600)
    assert not c.recorder.active
    c.tick(3600 + HOLD_SECONDS + 1)
    assert q.get_nowait()[1] == "Meet - abc"


def test_max_duration_splits_but_keeps_one_job(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    t = run(c, 0, 4 * 3600 + 30)
    assert c.recorder.active and q.empty()
    probe.mic, probe.title = False, None
    run(c, t, t + 15 + HOLD_SECONDS + 6)
    assert len(q.get_nowait()[0]) == 2


def test_manual_recording_capped(make):
    c, probe, warns, q = make()
    c.manual_start(0)
    run(c, 3, 4 * 3600 + 6 + HOLD_SECONDS + 3)
    assert not c.recorder.active and not q.empty()


def test_toggle_manual_starts_then_stops(make):
    c, probe, warns, q = make()
    c.toggle_manual(0)
    assert c.recorder.active
    c.toggle_manual(5)
    assert not c.recorder.active and not q.empty()


def test_toggle_during_auto_recording_stops_and_releases(make):
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.toggle_manual(5)
    assert not c.recorder.active
    assert q.get_nowait()[1] == "Meet - abc"


def test_app_loads_whisper_runtime_before_toast_library():
    # winrt (avisos) traz um MSVCP140.dll antigo; se ele carregar antes do
    # ctranslate2 (Whisper), o processo fecha sozinho ao transcrever.
    import subprocess
    import sys

    code = (
        "import sys, anotador.app; "
        "print('ctranslate2' in sys.modules, 'winrt' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["True", "False"]
