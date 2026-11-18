import json
from dataclasses import replace
from datetime import datetime

import pytest

from anotador.config import Config
from anotador.pipeline import process_recording, retry_pending
from anotador.recorder import Recording
from anotador.summarizer import SummaryError
from anotador.transcriber import Segment


@pytest.fixture
def setup(tmp_path):
    cfg = replace(Config(), output_dir=tmp_path / "out", work_dir=tmp_path / "work")
    rec_dir = cfg.work_dir / "rec"
    rec_dir.mkdir(parents=True)
    rec = Recording(rec_dir / "a-system.wav", rec_dir / "a-mic.wav", datetime(2026, 11, 13, 14, 5))
    rec.system_wav.write_bytes(b"")
    rec.mic_wav.write_bytes(b"")
    return cfg, rec


def fake_transcribe(path, model):
    if "mic" in path.name:
        return [Segment(5, 6, "Eu reviso.")]
    return [Segment(1, 2, "Bom dia.")]


class Notes:
    def __init__(self):
        self.calls = []

    def __call__(self, path, ok):
        self.calls.append((path, ok))


def failing_summary(*a, **k):
    raise SummaryError("offline")


def test_success_writes_summary_and_deletes_wavs(setup):
    cfg, rec = setup
    notes = Notes()
    path = process_recording(rec, "Meet - Alinhamento", cfg, transcribe=fake_transcribe,
                             summarize_fn=lambda t, c, to: "## Resumo\nTudo certo.", notify=notes)
    text = path.read_text(encoding="utf-8")
    assert "## Resumo\nTudo certo." in text
    assert "## Transcrição" in text and "Outros: Bom dia." in text and "Eu: Eu reviso." in text
    assert not rec.system_wav.exists() and not rec.mic_wav.exists()
    assert not list((cfg.work_dir / "pending").glob("*.json"))
    assert notes.calls == [(path, True)]
    assert path.parent == cfg.output_dir


def test_summary_failure_keeps_transcript_and_marks_pending(setup):
    cfg, rec = setup
    notes = Notes()
    path = process_recording(rec, None, cfg, transcribe=fake_transcribe,
                             summarize_fn=failing_summary, notify=notes)
    text = path.read_text(encoding="utf-8")
    assert "_Resumo pendente_" in text and "Outros: Bom dia." in text
    assert not rec.system_wav.exists() and not rec.mic_wav.exists()
    pending = list((cfg.work_dir / "pending").glob("*.json"))
    assert len(pending) == 1
    assert json.loads(pending[0].read_text(encoding="utf-8"))["md_path"] == str(path)
    assert notes.calls == [(path, False)]


def test_transcribe_failure_keeps_wavs(setup):
    cfg, rec = setup

    def boom(path, model):
        raise RuntimeError("falhou")

    with pytest.raises(RuntimeError):
        process_recording(rec, None, cfg, transcribe=boom, summarize_fn=failing_summary)
    assert rec.system_wav.exists() and rec.mic_wav.exists()


def test_retry_pending_success(setup):
    cfg, rec = setup
    path = process_recording(rec, None, cfg, transcribe=fake_transcribe, summarize_fn=failing_summary)
    seen = []

    def ok(transcript, cmd, timeout):
        seen.append(transcript)
        return "## Resumo\nAgora sim."

    assert retry_pending(cfg, summarize_fn=ok) == [path]
    text = path.read_text(encoding="utf-8")
    assert "## Resumo\nAgora sim." in text and "_Resumo pendente_" not in text
    assert "Outros: Bom dia." in seen[0]
    assert not list((cfg.work_dir / "pending").glob("*.json"))


def test_retry_pending_failure_keeps_marker(setup):
    cfg, rec = setup
    process_recording(rec, None, cfg, transcribe=fake_transcribe, summarize_fn=failing_summary)
    assert retry_pending(cfg, summarize_fn=failing_summary) == []
    assert len(list((cfg.work_dir / "pending").glob("*.json"))) == 1


def test_retry_pending_md_deleted_drops_marker(setup):
    cfg, rec = setup
    path = process_recording(rec, None, cfg, transcribe=fake_transcribe, summarize_fn=failing_summary)
    path.unlink()
    assert retry_pending(cfg, summarize_fn=failing_summary) == []
    assert not list((cfg.work_dir / "pending").glob("*.json"))


def test_retry_pending_no_dir(tmp_path):
    cfg = replace(Config(), work_dir=tmp_path / "nada")
    assert retry_pending(cfg, summarize_fn=failing_summary) == []


def test_marker_exists_while_summarizing_and_removed_after(setup):
    cfg, rec = setup
    seen = []

    def summ(t, c, to):
        seen.append(len(list((cfg.work_dir / "pending").glob("*.json"))))
        return "## Resumo\nok"

    process_recording(rec, None, cfg, transcribe=fake_transcribe, summarize_fn=summ)
    assert seen == [1]
    assert not list((cfg.work_dir / "pending").glob("*.json"))


def test_unexpected_summary_error_keeps_marker(setup):
    cfg, rec = setup

    def boom(*a):
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "x")

    notes = Notes()
    path = process_recording(rec, None, cfg, transcribe=fake_transcribe, summarize_fn=boom, notify=notes)
    assert len(list((cfg.work_dir / "pending").glob("*.json"))) == 1
    assert notes.calls == [(path, False)]


def test_on_start_called_before_transcription(setup):
    cfg, rec = setup
    events = []

    def transcribe(path, model):
        events.append("transcribe")
        return fake_transcribe(path, model)

    process_recording(rec, "Meet - abc", cfg, transcribe=transcribe,
                      summarize_fn=lambda t, c, to: "## Resumo\nok",
                      on_start=lambda r, title: events.append(("start", title)))
    assert events[0] == ("start", "Meet - abc")
    assert events.count("transcribe") == 2


def _part(folder, name, started_at):
    r = Recording(folder / f"{name}-system.wav", folder / f"{name}-mic.wav", started_at)
    r.system_wav.write_bytes(b"")
    r.mic_wav.write_bytes(b"")
    return r


def test_job_merges_parts_with_time_offsets(setup):
    from anotador.pipeline import process_job
    cfg, _ = setup
    folder = cfg.work_dir / "rec"
    a = _part(folder, "a", datetime(2026, 11, 16, 15, 39, 0))
    b = _part(folder, "b", datetime(2026, 11, 16, 15, 40, 0))

    def transcribe(path, model):
        return [Segment(5, 6, "oi")] if "mic" in path.name else []

    path = process_job([a, b], "Meet - PCB", cfg, transcribe=transcribe,
                       summarize_fn=lambda t, c, to: "## Resumo\nok", duration_fn=lambda p: 60.0)
    text = path.read_text(encoding="utf-8")
    assert "[00:00:05] Eu: oi" in text and "[00:01:05] Eu: oi" in text
    assert "_16/11/2026 15:39_" in text
    assert path.name.startswith("2026-11-16 15h39 - PCB")
    assert not any(p.exists() for r in (a, b) for p in (r.mic_wav, r.system_wav))


def test_tiny_job_is_dropped_without_note(setup):
    from anotador.pipeline import process_job
    cfg, rec = setup
    calls = []
    result = process_job([rec], "Meet - x", cfg, transcribe=fake_transcribe,
                         summarize_fn=lambda t, c, to: "x",
                         notify=lambda p, ok: calls.append("notify"),
                         on_start=lambda r, t: calls.append("start"),
                         duration_fn=lambda p: 3.0)
    assert result is None and calls == []
    assert not rec.mic_wav.exists() and not rec.system_wav.exists()
    assert not cfg.output_dir.exists() or not list(cfg.output_dir.glob("*.md"))
