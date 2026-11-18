import queue
from datetime import datetime
from pathlib import Path

from anotador.app import Controller
from anotador.config import Config
from anotador.detector import MeetingDetector
from anotador.recorder import Recording


class FakeRecorder:
    def __init__(self, fail=False):
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
        self.starts += 1

    def stop(self):
        self.active = False
        return Recording(Path("s.wav"), Path("m.wav"), datetime(2026, 11, 13))

    def cancel(self):
        self.active = False
        self.cancelled += 1


class Probe:
    def __init__(self):
        self.mic, self.title = False, None


def make(fail=False):
    probe, warns, q = Probe(), [], queue.Queue()
    c = Controller(
        Config(),
        MeetingDetector(30, 15),
        FakeRecorder(fail),
        q,
        warn=lambda on_cancel, secs: warns.append((on_cancel, secs)),
        mic_probe=lambda exe: probe.mic,
        title_probe=lambda exe, kw: probe.title,
    )
    return c, probe, warns, q


def test_start_records_and_warns_once():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.tick(3)
    assert c.recorder.active
    assert len(warns) == 1 and warns[0][1] == 15.0


def test_stop_enqueues_with_title():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    probe.mic, probe.title = False, None
    c.tick(10)
    c.tick(26)
    rec, title = q.get_nowait()
    assert title == "Meet - abc" and rec.mic_wav == Path("m.wav")
    assert not c.recorder.active


def test_cancel_from_warning_discards():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    warns[0][0]()  # usuário clicou "Cancelar gravação"
    c.tick(3)
    assert c.recorder.cancelled == 1 and not c.recorder.active
    assert q.empty()


def test_title_probe_only_when_mic_on():
    calls = []
    c, probe, warns, q = make()
    c._title_probe = lambda exe, kw: calls.append(1)
    c.tick(0)
    assert calls == []


def test_recorder_failure_does_not_crash_and_resets():
    c, probe, warns, q = make(fail=True)
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    assert not c.detector.recording
    assert warns == []


def test_manual_start_and_stop():
    c, probe, warns, q = make()
    c.manual_start(0)
    assert c.recorder.active and len(warns) == 1   # confirmação visual do clique
    c.manual_stop()
    assert not c.recorder.active
    assert q.get_nowait()[1] is None


def test_resume_same_meeting_records_without_warning():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    probe.mic, probe.title = False, None
    c.tick(10); c.tick(26)
    q.get_nowait()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(60)
    assert c.recorder.active
    assert len(warns) == 1


def test_sleep_gap_stops_and_enqueues():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.tick(3)
    probe.mic, probe.title = False, None   # ao acordar, a chamada já caiu
    c.tick(3 + 3600)  # notebook dormiu
    assert q.get_nowait()[1] == "Meet - abc"
    assert not c.recorder.active


def test_wake_still_in_call_resumes_without_warning():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.tick(3 + 600)
    assert q.get_nowait()[1] == "Meet - abc"
    assert c.recorder.active and len(warns) == 1


def test_max_duration_stops_and_enqueues():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    t = 0
    while t <= 4 * 3600 + 6:
        c.tick(t)
        t += 3
    assert not q.empty()


def test_manual_recording_also_capped():
    c, probe, warns, q = make()
    c.manual_start(0)
    t = 3
    while t <= 4 * 3600 + 6:
        c.tick(t)
        t += 3
    assert not c.recorder.active and not q.empty()


def test_broken_recorder_restarts_without_warning():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.recorder.is_broken = True
    c.tick(3)
    assert q.get_nowait()[1] == "Meet - abc"
    assert c.recorder.active and c.recorder.starts == 2
    assert len(warns) == 1


def test_toggle_manual_starts_then_stops():
    c, probe, warns, q = make()
    c.toggle_manual(0)
    assert c.recorder.active
    c.toggle_manual(5)
    assert not c.recorder.active and not q.empty()


def test_toggle_during_auto_recording_stops_it():
    c, probe, warns, q = make()
    probe.mic, probe.title = True, "Meet - abc"
    c.tick(0)
    c.toggle_manual(5)
    assert not c.recorder.active
    assert q.get_nowait()[1] == "Meet - abc"


def test_app_loads_whisper_runtime_before_toast_library():
    # winrt (avisos) traz um MSVCP140.dll antigo; se ele carregar antes do
    # ctranslate2 (Whisper), o processo fecha sozinho ao transcrever.
    import subprocess, sys
    code = (
        "import sys, anotador.app; "
        "print('ctranslate2' in sys.modules, 'winrt' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["True", "False"]
