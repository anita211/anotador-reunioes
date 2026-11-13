"""Nome e conteúdo do arquivo Markdown de cada reunião."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MEET_PREFIX = re.compile(r"^meet(\s+-\s*|\s*-?\s*$)", re.IGNORECASE)
_TRANSCRIPT_HEADER = "## Transcrição"
_PENDING = "_Resumo pendente_ — será gerado quando o Claude estiver disponível."
_MAX_NAME = 150


def clean_title(title: str | None) -> str:
    t = _MEET_PREFIX.sub("", (title or "").strip())
    t = _ILLEGAL.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t or "Reunião"


def meeting_filename(started_at: datetime, title: str | None) -> str:
    prefix = started_at.strftime("%Y-%m-%d %Hh%M") + " - "
    room = _MAX_NAME - len(prefix) - len(".md") - len(" (99)")
    return prefix + clean_title(title)[:room].rstrip(" .") + ".md"


def _render(started_at: datetime, title: str | None, summary: str | None, transcript: str) -> str:
    head = f"# {clean_title(title)}\n\n_{started_at.strftime('%d/%m/%Y %H:%M')}_\n\n"
    body = (summary.strip() if summary else _PENDING) + "\n\n"
    return head + body + _TRANSCRIPT_HEADER + "\n\n" + transcript.rstrip("\n") + "\n"


def write_meeting(
    output_dir: Path,
    started_at: datetime,
    title: str | None,
    summary: str | None,
    transcript: str,
    path: Path | None = None,
) -> Path:
    """Escreve o arquivo. Com `path`, reescreve esse arquivo; sem, cria um nome novo sem sobrescrever."""
    if path is None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        base = meeting_filename(started_at, title)[: -len(".md")]
        path = output_dir / f"{base}.md"
        n = 2
        while path.exists():
            path = output_dir / f"{base} ({n}).md"
            n += 1
    path.write_text(_render(started_at, title, summary, transcript), encoding="utf-8")
    return path


def read_transcript(md_path: Path) -> str:
    text = Path(md_path).read_text(encoding="utf-8")
    _, _, after = text.partition(_TRANSCRIPT_HEADER + "\n\n")
    return after.rstrip("\n")
