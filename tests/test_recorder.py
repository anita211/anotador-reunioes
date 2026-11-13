from datetime import datetime

from anotador.recorder import find_leftover_recordings


def test_find_leftover_pairs(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "20261113-140501-system.wav").write_bytes(b"")
    (rec / "20261113-140501-mic.wav").write_bytes(b"")
    (rec / "20261113-150000-system.wav").write_bytes(b"")  # sem par -> ignorado
    found = find_leftover_recordings(tmp_path)
    assert len(found) == 1
    assert found[0].started_at == datetime(2026, 11, 13, 14, 5, 1)
    assert found[0].mic_wav.name == "20261113-140501-mic.wav"


def test_find_leftover_no_dir(tmp_path):
    assert find_leftover_recordings(tmp_path) == []


def test_frames_to_pad_small_lag_ignored():
    from anotador.recorder import frames_to_pad
    assert frames_to_pad(expected_end=48000, frame_count=1024, written=48000 - 1024 - 4800, rate=48000) == 0


def test_frames_to_pad_fills_gap():
    from anotador.recorder import frames_to_pad
    assert frames_to_pad(expected_end=480000, frame_count=1024, written=0, rate=48000) == 480000 - 1024


def test_frames_to_pad_caps_huge_gap():
    from anotador.recorder import MAX_PAD_SECONDS, frames_to_pad
    n = frames_to_pad(expected_end=48000 * 3600 * 10, frame_count=1024, written=0, rate=48000)
    assert n == MAX_PAD_SECONDS * 48000


def test_silence_chunks_bounded():
    from anotador.recorder import silence_chunks
    chunks = list(silence_chunks(250_000, 4, max_chunk_frames=100_000))
    assert [len(c) for c in chunks] == [400_000, 400_000, 200_000]
    assert all(set(c) <= {0} for c in chunks)


class _FakeStream:
    def __init__(self, active=True):
        self._active = active
        self.closed = False

    def is_active(self):
        return self._active

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class _FakePA:
    def __init__(self, fail_on_open=None, loopback_missing=False):
        self.opens = 0
        self.fail_on_open = fail_on_open
        self.loopback_missing = loopback_missing
        self.terminated = False
        self.streams = []

    def get_host_api_info_by_type(self, t):
        return {"defaultInputDevice": 1}

    def get_default_wasapi_loopback(self):
        if self.loopback_missing:
            raise LookupError("sem loopback")
        return {"index": 0, "maxInputChannels": 2, "defaultSampleRate": 48000.0}

    def get_device_info_by_index(self, i):
        return {"index": i, "maxInputChannels": 1, "defaultSampleRate": 44100.0}

    def open(self, **kw):
        self.opens += 1
        if self.opens == self.fail_on_open:
            raise OSError("dispositivo ocupado")
        s = _FakeStream()
        self.streams.append(s)
        return s

    def terminate(self):
        self.terminated = True


def test_start_failure_on_second_device_cleans_up(tmp_path):
    import pytest
    from anotador.recorder import Recorder
    pa = _FakePA(fail_on_open=2)
    r = Recorder(tmp_path, pa_factory=lambda: pa)
    with pytest.raises(OSError):
        r.start()
    assert not r.active
    assert pa.terminated and all(s.closed for s in pa.streams)
    assert list((tmp_path / "rec").glob("*.wav")) == []


def test_start_missing_loopback_cleans_up(tmp_path):
    import pytest
    from anotador.recorder import Recorder
    pa = _FakePA(loopback_missing=True)
    r = Recorder(tmp_path, pa_factory=lambda: pa)
    with pytest.raises(LookupError):
        r.start()
    assert pa.terminated and not r.active


def test_broken_when_a_stream_stops(tmp_path):
    from anotador.recorder import Recorder
    pa = _FakePA()
    r = Recorder(tmp_path, pa_factory=lambda: pa)
    r.start()
    assert r.broken() is False
    pa.streams[0]._active = False
    assert r.broken() is True
    rec = r.stop()
    assert rec.system_wav.exists() and rec.mic_wav.exists()
