"""Depois da reunião: transcreve, salva, apaga o áudio e resume."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .config import Config
from .output import read_transcript, write_meeting
from .recorder import Recording
from .summarizer import summarize
from .transcriber import Segment, format_transcript, merge_tracks, transcribe_file


log = logging.getLogger(__name__)


def _pending_dir(cfg: Config) -> Path:
    return Path(cfg.work_dir) / "pending"


MIN_JOB_SECONDS = 10  # menos que isso (ex.: pedaço de 3 s) não vira nota


def _wav_seconds(path: Path) -> float | None:
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


def _delete_audio(parts: list[Recording]) -> None:
    for rec in parts:
        rec.system_wav.unlink(missing_ok=True)
        rec.mic_wav.unlink(missing_ok=True)


def _shift(segments: list[Segment], offset: float) -> list[Segment]:
    return [Segment(s.start + offset, s.end + offset, s.text) for s in segments]


def process_recording(rec: Recording, title: str | None, cfg: Config, **kwargs) -> Path | None:
    return process_job([rec], title, cfg, **kwargs)


def process_job(
    parts: list[Recording],
    title: str | None,
    cfg: Config,
    transcribe=transcribe_file,
    summarize_fn=summarize,
    notify=lambda path, ok: None,
    on_start=lambda rec, title: None,
    duration_fn=_wav_seconds,
) -> Path | None:
    """Uma reunião = uma nota, mesmo que tenha sido gravada em várias partes."""
    first = parts[0]
    durations = [duration_fn(p.mic_wav) for p in parts]
    if all(d is not None for d in durations) and sum(durations) < MIN_JOB_SECONDS:
        log.info("gravação muito curta (%.1f s); descartada", sum(durations))
        _delete_audio(parts)
        return None
    try:
        on_start(first, title)
    except Exception:
        log.exception("falha no aviso de início do processamento")
    me: list[Segment] = []
    others: list[Segment] = []
    for rec in parts:
        offset = (rec.started_at - first.started_at).total_seconds()
        me += _shift(transcribe(rec.mic_wav, cfg.whisper_model), offset)
        others += _shift(transcribe(rec.system_wav, cfg.whisper_model), offset)
    transcript = format_transcript(merge_tracks(me, others))

    path = write_meeting(cfg.output_dir, first.started_at, title, None, transcript)
    _delete_audio(parts)

    # marcador antes de chamar o Claude: se o PC desligar no meio, o resumo é refeito depois
    pending = _pending_dir(cfg)
    pending.mkdir(parents=True, exist_ok=True)
    marker_path = pending / f"{path.stem}.json"
    marker = {"md_path": str(path), "title": title, "started_at": first.started_at.isoformat()}
    marker_path.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")

    try:
        summary = summarize_fn(transcript, cfg.claude_cmd, cfg.claude_timeout_seconds)
    except Exception:
        log.exception("resumo falhou; fica pendente")
        notify(path, False)
        return path

    write_meeting(cfg.output_dir, first.started_at, title, summary, transcript, path=path)
    marker_path.unlink(missing_ok=True)
    notify(path, True)
    return path


def retry_pending(cfg: Config, summarize_fn=summarize) -> list[Path]:
    """Tenta de novo os resumos que falharam; devolve os arquivos que ficaram prontos."""
    pending = _pending_dir(cfg)
    if not pending.is_dir():
        return []
    done = []
    for marker in sorted(pending.glob("*.json")):
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
            md = Path(info["md_path"])
        except (OSError, ValueError, KeyError):
            marker.unlink(missing_ok=True)
            continue
        if not md.exists():
            marker.unlink(missing_ok=True)
            continue
        transcript = read_transcript(md)
        try:
            summary = summarize_fn(transcript, cfg.claude_cmd, cfg.claude_timeout_seconds)
        except Exception:
            log.exception("resumo pendente falhou de novo")
            continue
        started_at = datetime.fromisoformat(info["started_at"])
        write_meeting(md.parent, started_at, info.get("title"), summary, transcript, path=md)
        marker.unlink(missing_ok=True)
        done.append(md)
    return done
