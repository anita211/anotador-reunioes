"""Programa da bandeja: detecta a reunião, grava e manda processar."""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Carregar o runtime do Whisper ANTES de qualquer uso de windows_toasts/winrt:
# o pacote winrt traz um MSVCP140.dll antigo e, se ele for carregado primeiro,
# o ctranslate2 derruba o processo (0xc0000005) ao transcrever.
import ctranslate2  # noqa: F401,E402

from .config import Config, load_config
from .detector import MeetingDetector, browser_mic_in_use, browser_playing_audio, find_meet_title

log = logging.getLogger("anotador")

MAX_RECORDING_SECONDS = 4 * 3600  # abaixo do limite de 4 GiB do WAV a 48 kHz estéreo
SLEEP_GAP_SECONDS = 60            # intervalo entre verificações maior que isso = notebook dormiu
HOLD_SECONDS = 120                # espera após o fim antes de processar: se voltar, é a mesma reunião


class Controller:
    """Liga detector, gravador e fila de processamento. Seguro para chamar de várias threads.

    Uma reunião pode ter várias partes (queda rápida, dispositivo que reabre, suspensão).
    As partes ficam juntas em `_parts` e só vão para a fila como um único trabalho
    `(partes, título)` quando a reunião realmente termina.
    """

    def __init__(self, cfg: Config, detector, recorder, jobs: queue.Queue, warn,
                 mic_probe, title_probe, render_probe=lambda exe: False):
        self.cfg = cfg
        self.detector = detector
        self.recorder = recorder
        self.jobs = jobs
        self._warn = warn
        self._mic_probe = mic_probe
        self._title_probe = title_probe
        self._render_probe = render_probe
        self._lock = threading.RLock()
        self._last_tick: float | None = None
        self._rec_started = 0.0
        self._parts: list = []
        self._job_title: str | None = None
        self._held_since: float | None = None

    # --- ciclo ---------------------------------------------------------------

    def tick(self, now: float) -> None:
        slept = self._last_tick is not None and now - self._last_tick > SLEEP_GAP_SECONDS
        self._last_tick = now
        with self._lock:
            if self.recorder.active:
                if slept or now - self._rec_started > MAX_RECORDING_SECONDS:
                    log.info("gravação pausada (%s)", "suspensão" if slept else "limite de duração")
                    self._remember_title(self.detector.title)
                    self.detector.interrupt(now)
                    self._stop_part()
                    self._held_since = now
                elif self.recorder.broken():
                    log.warning("dispositivo de áudio caiu; reabrindo no dispositivo atual")
                    self._stop_part()
                    if not self._start_recorder(now):
                        self.detector.interrupt(now)
                        self._held_since = now
            if self._held_since is not None and now - self._held_since > HOLD_SECONDS:
                self._release_job()

        mic = self._mic_probe(self.cfg.browser_exe)
        title = self._title_probe(self.cfg.browser_exe, self.cfg.title_keyword) if mic else None
        with self._lock:
            in_call = mic
            if self.detector.recording and not mic:
                # mudo no Meet pode soltar o microfone; a chamada continua enquanto o
                # navegador estiver tocando o áudio dos outros
                in_call = self._render_probe(self.cfg.browser_exe)
            event = self.detector.update(now, in_call, title)
            if event == "start":
                self._release_job()  # outra reunião: a anterior já pode ser processada
                if self._start_recorder(now):
                    self._remember_title(self.detector.title)
                    self._warn(self.cancel_current, self.cfg.warning_seconds)
            elif event == "resume":
                if self._start_recorder(now):
                    self._remember_title(self.detector.title)
                    self._held_since = None
            elif event == "stop":
                self._remember_title(self.detector.title)
                self._stop_part()
                self._held_since = now

    # --- partes e trabalhos --------------------------------------------------

    def _remember_title(self, title: str | None) -> None:
        if title and not self._job_title:
            self._job_title = title

    def _start_recorder(self, now: float) -> bool:
        try:
            self.recorder.start()
            self._rec_started = now
            log.info("gravação iniciada")
            return True
        except Exception:
            log.exception("não foi possível iniciar a gravação")
            self.detector.cancel()
            return False

    def _stop_part(self) -> None:
        if not self.recorder.active:
            return
        rec = self.recorder.stop()
        log.info("parte gravada: %s", rec.mic_wav.name)
        self._parts.append(rec)

    def _release_job(self) -> None:
        if self._parts:
            log.info("reunião encerrada: %d parte(s)", len(self._parts))
            self.jobs.put((self._parts, self._job_title))
        self._parts, self._job_title, self._held_since = [], None, None

    def _discard_job(self) -> None:
        for rec in self._parts:
            rec.system_wav.unlink(missing_ok=True)
            rec.mic_wav.unlink(missing_ok=True)
        self._parts, self._job_title, self._held_since = [], None, None

    # --- ações do usuário ----------------------------------------------------

    def cancel_current(self) -> None:
        with self._lock:
            if self.recorder.active:
                self.recorder.cancel()
            self._discard_job()
            log.info("gravação cancelada pelo usuário")
            self.detector.cancel()

    def manual_start(self, now: float) -> None:
        with self._lock:
            if self.recorder.active:
                return
            self._release_job()
            self.detector.force_start(now)
            if self._start_recorder(now):
                self._warn(self.cancel_current, self.cfg.warning_seconds)

    def toggle_manual(self, now: float) -> None:
        """Clique no ícone: inicia se parado, para se gravando."""
        with self._lock:
            if self.recorder.active:
                self.manual_stop()
            else:
                self.manual_start(now)

    def manual_stop(self) -> None:
        with self._lock:
            self._remember_title(self.detector.title)
            self.detector.force_stop()
            self._stop_part()
            self._release_job()

    def shutdown(self) -> None:
        """Ao sair: encerra a gravação e deixa os WAVs para serem processados no próximo início."""
        with self._lock:
            if self.recorder.active:
                self.recorder.stop()


# --- Execução real ---------------------------------------------------------------


def _setup_logging(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(work_dir / "anotador.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _single_instance() -> object | None:
    import win32api
    import win32event
    import winerror

    handle = win32event.CreateMutex(None, False, "AnotadorReunioesMutex")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return None
    return handle


def _icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((12, 6, 52, 58), radius=6, fill=(120, 120, 120, 255))
    for y in (20, 30, 40):
        d.line((20, y, 44, y), fill=(235, 235, 235, 255), width=3)
    return img


def _worker(cfg: Config, jobs: queue.Queue) -> None:
    from .pipeline import process_job, retry_pending
    from .ui import notify_processing_started, notify_silent

    try:
        for path in retry_pending(cfg):
            notify_silent(path, True)
    except Exception:
        log.exception("erro ao refazer resumos pendentes")
    while True:
        parts, title = jobs.get()
        try:
            path = process_job(
                parts, title, cfg, notify=notify_silent, on_start=notify_processing_started
            )
            if path:
                log.info("reunião salva: %s", path)
        except Exception:
            log.exception("erro ao processar %s", parts[0].mic_wav)


def _poller(controller: Controller, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            controller.tick(time.monotonic())
        except Exception:
            log.exception("erro na detecção")
        stop.wait(controller.cfg.poll_seconds)


def main() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    defaults = Config()
    _setup_logging(defaults.work_dir)
    cfg = load_config(defaults.work_dir / "config.json")

    mutex = _single_instance()
    if mutex is None:
        log.info("já existe uma instância rodando; saindo")
        return

    import pystray

    from .recorder import Recorder, find_leftover_recordings
    from .ui import show_recording_warning

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    jobs: queue.Queue = queue.Queue()
    for rec in find_leftover_recordings(cfg.work_dir):
        log.info("gravação pendente encontrada: %s", rec.mic_wav.name)
        jobs.put(([rec], None))

    controller = Controller(
        cfg,
        MeetingDetector(cfg.title_wait_seconds, cfg.stop_grace_seconds),
        Recorder(cfg.work_dir),
        jobs,
        warn=show_recording_warning,
        mic_probe=browser_mic_in_use,
        title_probe=find_meet_title,
        render_probe=browser_playing_audio,
    )
    stop = threading.Event()
    threading.Thread(target=_worker, args=(cfg, jobs), daemon=True).start()
    threading.Thread(target=_poller, args=(controller, stop), daemon=True).start()

    def on_quit(icon, _item):
        stop.set()
        controller.shutdown()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda _item: "Parar gravação" if controller.recorder.active else "Iniciar gravação",
            lambda i, _: controller.toggle_manual(time.monotonic()),
            default=True,  # clique esquerdo no ícone
        ),
        pystray.MenuItem("Abrir pasta de reuniões", lambda i, _: os.startfile(str(cfg.output_dir))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", on_quit),
    )
    log.info("Anotador iniciado; monitorando %s", cfg.browser_exe)
    pystray.Icon("anotador", _icon_image(), "Anotador", menu).run()
