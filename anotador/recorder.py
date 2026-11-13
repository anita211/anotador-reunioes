"""Grava o som do computador (os outros) e o microfone (você) em dois WAVs."""
from __future__ import annotations

import re
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_STAMP = "%Y%m%d-%H%M%S"
_NAME_RE = re.compile(r"^(\d{8}-\d{6})-system\.wav$")


@dataclass
class Recording:
    system_wav: Path
    mic_wav: Path
    started_at: datetime


def find_leftover_recordings(work_dir: Path) -> list[Recording]:
    """Pares *-system.wav / *-mic.wav deixados em work_dir/rec (saída ou travamento no meio)."""
    rec_dir = Path(work_dir) / "rec"
    if not rec_dir.is_dir():
        return []
    found = []
    for system in sorted(rec_dir.glob("*-system.wav")):
        m = _NAME_RE.match(system.name)
        if not m:
            continue
        mic = rec_dir / f"{m.group(1)}-mic.wav"
        if mic.exists():
            found.append(Recording(system, mic, datetime.strptime(m.group(1), _STAMP)))
    return found


class _Track:
    """Um stream de captura gravando num WAV, completando com silêncio as lacunas.

    O loopback do WASAPI não entrega dados enquanto nada toca; sem completar com
    silêncio, as duas faixas sairiam desalinhadas no tempo.
    """

    def __init__(self, pa, device: dict, path: Path, t0: float):
        import pyaudiowpatch as pyaudio

        self.path = path
        self.channels = max(1, min(2, int(device["maxInputChannels"])))
        self.rate = int(device["defaultSampleRate"])
        self.width = pyaudio.get_sample_size(pyaudio.paInt16)
        self.frame_bytes = self.channels * self.width
        self._t0 = t0
        self._frames = 0
        self._lock = threading.Lock()
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(self.channels)
        self._wav.setsampwidth(self.width)
        self._wav.setframerate(self.rate)
        self._stream = pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=1024,
            stream_callback=self._callback,
        )

    def _pad_to(self, target_frames: int) -> None:
        missing = target_frames - self._frames
        if missing > 0:
            self._wav.writeframes(b"\x00" * missing * self.frame_bytes)
            self._frames += missing

    def _callback(self, data, frame_count, _info, _status):
        import pyaudiowpatch as pyaudio

        with self._lock:
            if self._wav is None:
                return (None, pyaudio.paComplete)
            expected_end = int((time.monotonic() - self._t0) * self.rate)
            # se ficou para trás mais de 0,2 s, completa com silêncio antes deste bloco
            if expected_end - frame_count - self._frames > self.rate // 5:
                self._pad_to(expected_end - frame_count)
            self._wav.writeframes(data)
            self._frames += frame_count
        return (None, pyaudio.paContinue)

    def close(self) -> None:
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        with self._lock:
            if self._wav is not None:
                self._pad_to(int((time.monotonic() - self._t0) * self.rate))
                self._wav.close()
                self._wav = None


class Recorder:
    def __init__(self, work_dir: Path):
        self._rec_dir = Path(work_dir) / "rec"
        self._pa = None
        self._tracks: list[_Track] = []
        self._started_at: datetime | None = None

    @property
    def active(self) -> bool:
        return bool(self._tracks)

    def start(self) -> None:
        import pyaudiowpatch as pyaudio

        if self.active:
            return
        self._rec_dir.mkdir(parents=True, exist_ok=True)
        self._pa = pyaudio.PyAudio()
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        loopback = self._pa.get_default_wasapi_loopback()
        mic = self._pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        self._started_at = datetime.now()
        stamp = self._started_at.strftime(_STAMP)
        t0 = time.monotonic()
        try:
            self._tracks = [
                _Track(self._pa, loopback, self._rec_dir / f"{stamp}-system.wav", t0),
                _Track(self._pa, mic, self._rec_dir / f"{stamp}-mic.wav", t0),
            ]
        except Exception:
            self._close_all()
            raise

    def _close_all(self) -> list[_Track]:
        tracks, self._tracks = self._tracks, []
        for t in tracks:
            t.close()
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
        return tracks

    def stop(self) -> Recording:
        tracks = self._close_all()
        system, mic = tracks[0].path, tracks[1].path
        return Recording(system, mic, self._started_at)

    def cancel(self) -> None:
        for t in self._close_all():
            t.path.unlink(missing_ok=True)
