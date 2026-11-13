"""Resumo da transcrição via Claude CLI (`claude -p`)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

PROMPT = """Você recebe a transcrição de uma reunião em português. "Eu" é a dona destas anotações; "Outros" são os demais participantes.
Escreva em Markdown, em português, com exatamente estas seções:
## Resumo
(5 a 10 linhas)
## Decisões
## Pendências
(lista: tarefa — responsável, se citado — prazo, se citado)
## Pontos em aberto
Se uma seção não tiver conteúdo, escreva "Nenhum.". Não invente informações.

Transcrição:
"""

MAX_CHARS = 400_000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SummaryError(Exception):
    pass


def _resolve(cmd: str) -> str:
    found = shutil.which(cmd)
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / f"{cmd}.exe"
    return str(fallback) if fallback.exists() else cmd


def _ask(text: str, claude_cmd: str, timeout: float, run) -> str:
    try:
        result = run(
            [
                _resolve(claude_cmd), "-p",
                "--no-session-persistence",   # não guarda cópia da transcrição em ~/.claude
                "--tools", "",                # só texto: a transcrição vem de terceiros
                "--strict-mcp-config",        # sem servidores MCP
                "--disable-slash-commands",
            ],
            input=PROMPT + text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            creationflags=_NO_WINDOW,
            cwd=tempfile.gettempdir(),       # fora de qualquer projeto (sem CLAUDE.md)
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SummaryError(str(e)) from e
    if result.returncode != 0 or not (result.stdout or "").strip():
        raise SummaryError(f"claude saiu com código {result.returncode}: {(result.stderr or '')[:500]}")
    return result.stdout.strip()


def _chunks(transcript: str) -> list[str]:
    chunks, current, size = [], [], 0
    for line in transcript.splitlines(keepends=True):
        if size + len(line) > MAX_CHARS and current:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def summarize(transcript: str, claude_cmd: str, timeout: float, run=subprocess.run) -> str:
    if len(transcript) <= MAX_CHARS:
        return _ask(transcript, claude_cmd, timeout, run)
    partials = [_ask(c, claude_cmd, timeout, run) for c in _chunks(transcript)]
    return _ask("\n\n".join(partials), claude_cmd, timeout, run)
