"""Programa da bandeja: detecta a reunião, grava e manda processar."""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Config, load_config
from .detector import MeetingDetector, browser_mic_in_use, find_meet_title

log = logging.getLogger("anotador")


class Controller:
    """Liga detector, gravador e fila de processamento. Seguro para chamar de várias threads."""

    def __init__(self, cfg: Config, detector, recorder, jobs: queue.Queue, warn, mic_probe, title_probe):
        self.cfg = cfg
        self.detector = detector
        self.recorder = recorder
        self.jobs = jobs
        self._warn = warn
        self._mic_probe = mic_probe
        self._title_probe = title_probe
        self._lock = threading.RLock()

    def tick(self, now: float) -> None:
        mic = self._mic_probe(self.cfg.browser_exe)
        title = self._title_probe(self.cfg.browser_exe, self.cfg.title_keyword) if mic else None
        with self._lock:
            event = self.detector.update(now, mic, title)
            if event == "start":
                if self._start_recorder():
                    self._warn(self.cancel_current, self.cfg.warning_seconds)
            elif event == "stop":
                self._stop_and_enqueue(self.detector.title)

    def _start_recorder(self) -> bool:
        try:
            self.recorder.start()
            log.info("gravação iniciada")
            return True
        except Exception:
            log.exception("não foi possível iniciar a gravação")
            self.detector.cancel()
            return False

    def _stop_and_enqueue(self, title: str | None) -> None:
        if not self.recorder.active:
            return
        rec = self.recorder.stop()
        log.info("gravação encerrada: %s", rec.mic_wav.name)
        self.jobs.put((rec, title))

    def cancel_current(self) -> None:
        with self._lock:
            if self.recorder.active:
                self.recorder.cancel()
                log.info("gravação cancelada pelo usuário")
            self.detector.cancel()

    def manual_start(self, now: float) -> None:
        with self._lock:
            if self.recorder.active:
                return
            self.detector.force_start(now)
            self._start_recorder()

    def manual_stop(self) -> None:
        with self._lock:
            title = self.detector.title
            self.detector.force_stop()
            self._stop_and_enqueue(title)

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
    from .pipeline import process_recording, retry_pending
    from .ui import notify_silent

    try:
        for path in retry_pending(cfg):
            notify_silent(path, True)
    except Exception:
        log.exception("erro ao refazer resumos pendentes")
    while True:
        rec, title = jobs.get()
        try:
            path = process_recording(rec, title, cfg, notify=notify_silent)
            log.info("reunião salva: %s", path)
        except Exception:
            log.exception("erro ao processar %s", rec.mic_wav)


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
    cfg = load_config(defaults.work_dir / "config.json")
    _setup_logging(cfg.work_dir)

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
        jobs.put((rec, None))

    controller = Controller(
        cfg,
        MeetingDetector(cfg.title_wait_seconds, cfg.stop_grace_seconds),
        Recorder(cfg.work_dir),
        jobs,
        warn=show_recording_warning,
        mic_probe=browser_mic_in_use,
        title_probe=find_meet_title,
    )
    stop = threading.Event()
    threading.Thread(target=_worker, args=(cfg, jobs), daemon=True).start()
    threading.Thread(target=_poller, args=(controller, stop), daemon=True).start()

    def on_quit(icon, _item):
        stop.set()
        controller.shutdown()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Iniciar gravação", lambda i, _: controller.manual_start(time.monotonic())),
        pystray.MenuItem("Parar gravação", lambda i, _: controller.manual_stop()),
        pystray.MenuItem("Abrir pasta de reuniões", lambda i, _: os.startfile(str(cfg.output_dir))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", on_quit),
    )
    log.info("Anotador iniciado; monitorando %s", cfg.browser_exe)
    pystray.Icon("anotador", _icon_image(), "Anotador", menu).run()
