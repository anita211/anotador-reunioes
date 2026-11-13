"""Depois da reunião: transcreve, salva, apaga o áudio e resume."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import Config
from .output import read_transcript, write_meeting
from .recorder import Recording
from .summarizer import SummaryError, summarize
from .transcriber import format_transcript, merge_tracks, transcribe_file


def _pending_dir(cfg: Config) -> Path:
    return Path(cfg.work_dir) / "pending"


def process_recording(
    rec: Recording,
    title: str | None,
    cfg: Config,
    transcribe=transcribe_file,
    summarize_fn=summarize,
    notify=lambda path, ok: None,
) -> Path:
    me = transcribe(rec.mic_wav, cfg.whisper_model)
    others = transcribe(rec.system_wav, cfg.whisper_model)
    transcript = format_transcript(merge_tracks(me, others))

    path = write_meeting(cfg.output_dir, rec.started_at, title, None, transcript)
    rec.system_wav.unlink(missing_ok=True)
    rec.mic_wav.unlink(missing_ok=True)

    try:
        summary = summarize_fn(transcript, cfg.claude_cmd, cfg.claude_timeout_seconds)
    except SummaryError:
        pending = _pending_dir(cfg)
        pending.mkdir(parents=True, exist_ok=True)
        marker = {"md_path": str(path), "title": title, "started_at": rec.started_at.isoformat()}
        (pending / f"{path.stem}.json").write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
        notify(path, False)
        return path

    write_meeting(cfg.output_dir, rec.started_at, title, summary, transcript, path=path)
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
        except SummaryError:
            continue
        started_at = datetime.fromisoformat(info["started_at"])
        write_meeting(md.parent, started_at, info.get("title"), summary, transcript, path=md)
        marker.unlink(missing_ok=True)
        done.append(md)
    return done
