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
