"""Transcrição local (faster-whisper) e junção das faixas "Eu" / "Outros"."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Line:
    start: float
    speaker: str
    text: str


_models: dict[str, object] = {}
_models_lock = threading.Lock()


def _model(name: str):
    with _models_lock:
        if name not in _models:
            from faster_whisper import WhisperModel

            _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _models[name]


_TARGET_RATE = 16000


def load_wav_16k_mono(path: Path):
    """Lê WAV PCM 16 bits em blocos, mistura para mono e reamostra para 16 kHz (float32)."""
    import wave

    import numpy as np

    out = []
    with wave.open(str(path), "rb") as w:
        rate, channels = w.getframerate(), w.getnchannels()
        block = rate * 60
        consumed = 0  # frames de entrada já lidos
        produced = 0  # amostras de saída já geradas
        while True:
            raw = w.readframes(block)
            if not raw:
                break
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            x = x.reshape(-1, channels).mean(axis=1)
            end = consumed + len(x)
            n_end = int(end * _TARGET_RATE / rate)
            t_out = np.arange(produced, n_end) * rate / _TARGET_RATE - consumed
            out.append(np.interp(t_out, np.arange(len(x)), x).astype(np.float32))
            consumed, produced = end, n_end
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def transcribe_file(path: Path, model_name: str) -> list[Segment]:
    audio = load_wav_16k_mono(path)
    if len(audio) == 0:
        return []
    segments, _info = _model(model_name).transcribe(audio, language="pt", vad_filter=True)
    return [
        Segment(s.start, s.end, s.text)
        for s in segments
        if is_probably_speech(s.no_speech_prob, s.avg_logprob)
    ]


def is_probably_speech(no_speech_prob: float, avg_logprob: float) -> bool:
    """Descarta trechos que o Whisper "inventou" em silêncio ou ruído."""
    return no_speech_prob < 0.6 and avg_logprob > -1.0


def _norm(text: str) -> str:
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def drop_echo(me: list[Segment], others: list[Segment]) -> list[Segment]:
    """Remove trechos do microfone que são só o eco dos alto-falantes (fala dos outros)."""
    from difflib import SequenceMatcher

    kept = []
    for m in me:
        echo = False
        for o in others:
            if m.end < o.start - 1 or m.start > o.end + 1:
                continue
            if SequenceMatcher(None, _norm(m.text), _norm(o.text)).ratio() >= 0.6:
                echo = True
                break
        if not echo:
            kept.append(m)
    return kept


def merge_tracks(me: list[Segment], others: list[Segment]) -> list[Line]:
    """Intercala as duas faixas por horário; em empate, "Outros" vem primeiro."""
    me = drop_echo(me, others)
    tagged = [(s.start, 0, "Outros", s.text) for s in others] + [(s.start, 1, "Eu", s.text) for s in me]
    tagged.sort(key=lambda t: (t[0], t[1]))
    return [Line(start, speaker, text.strip()) for start, _, speaker, text in tagged if text.strip()]


def _hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_transcript(lines: list[Line]) -> str:
    return "\n".join(f"[{_hms(l.start)}] {l.speaker}: {l.text}" for l in lines)
