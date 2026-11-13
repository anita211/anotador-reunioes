"""Configuração do Anotador de Reuniões."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path


def _default_work_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "AnotadorReunioes"


def _documents_dir() -> Path:
    """Pasta Documentos real do Windows (pode estar redirecionada para o OneDrive)."""
    try:
        import ctypes
        import ctypes.wintypes

        buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


@dataclass(frozen=True)
class Config:
    browser_exe: str = "opera.exe"
    title_keyword: str = "Meet"
    poll_seconds: float = 3.0
    title_wait_seconds: float = 30.0
    stop_grace_seconds: float = 15.0
    warning_seconds: float = 15.0
    whisper_model: str = "small"
    output_dir: Path = _documents_dir() / "Reuniões"
    work_dir: Path = _default_work_dir()
    claude_cmd: str = "claude"
    claude_timeout_seconds: float = 600.0


_PATH_FIELDS = {"output_dir", "work_dir"}


def load_config(path: Path | None) -> Config:
    """Lê o JSON de configuração; arquivo ausente usa os padrões, chaves desconhecidas são ignoradas."""
    cfg = Config()
    if path is None or not Path(path).exists():
        return cfg
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config deve ser um objeto JSON")
    except (OSError, ValueError) as e:
        logging.getLogger(__name__).warning("config.json inválido (%s); usando padrões", e)
        return cfg
    known = {f.name for f in fields(Config)}
    updates = {}
    for key, value in data.items():
        if key not in known:
            continue
        if key in _PATH_FIELDS:
            value = Path(os.path.expandvars(str(value))).expanduser()
        updates[key] = value
    return replace(cfg, **updates)
