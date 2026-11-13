"""Janelinha "Gravando reunião" e notificação silenciosa de "pronto"."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_WIDTH, _HEIGHT = 270, 92


def _work_area_bottom_right() -> tuple[int, int]:
    """Canto inferior direito da área útil (acima da barra de tarefas)."""
    try:
        import win32api

        monitor = win32api.MonitorFromPoint((0, 0), 1)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        return right, bottom
    except Exception:
        return 1366, 728


def _run_warning(on_cancel: Callable[[], None], timeout_seconds: float) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("Anotador - aviso")
    root.overrideredirect(True)  # sem borda e fora da barra de tarefas
    root.attributes("-topmost", True)
    root.configure(bg="#2b2b2b")
    right, bottom = _work_area_bottom_right()
    root.geometry(f"{_WIDTH}x{_HEIGHT}+{right - _WIDTH - 16}+{bottom - _HEIGHT - 16}")

    closed = {"done": False}

    def close():
        if not closed["done"]:
            closed["done"] = True
            root.destroy()

    def cancel():
        close()
        try:
            on_cancel()
        except Exception:
            log.exception("erro ao cancelar gravação")

    tk.Label(root, text="● Gravando reunião", fg="#ffffff", bg="#2b2b2b",
             font=("Segoe UI", 11, "bold")).pack(pady=(12, 8))
    row = tk.Frame(root, bg="#2b2b2b")
    row.pack()
    tk.Button(row, text="OK", width=8, command=close).pack(side="left", padx=6)
    tk.Button(row, text="Cancelar gravação", command=cancel).pack(side="left", padx=6)
    root.after(int(timeout_seconds * 1000), close)
    root.mainloop()


def show_recording_warning(on_cancel: Callable[[], None], timeout_seconds: float) -> None:
    """Mostra o aviso sem bloquear; fecha com OK, Cancelar ou sozinho após `timeout_seconds`."""
    threading.Thread(target=_run_warning, args=(on_cancel, timeout_seconds), daemon=True).start()


def notify_silent(md_path: Path, ok: bool) -> None:
    """Notificação que vai direto para a central de notificações, sem aparecer na tela."""
    try:
        from windows_toasts import Toast, ToastAudio, WindowsToaster

        text = ("Anotações prontas: " if ok else "Transcrição salva; resumo pendente: ") + Path(md_path).stem
        toast = Toast(
            [text],
            audio=ToastAudio(silent=True),
            suppress_popup=True,
            on_activated=lambda _e: os.startfile(str(md_path)),
        )
        WindowsToaster("Anotador de Reuniões").show_toast(toast)
    except Exception:
        # Sem pop-up é a regra; se a notificação silenciosa falhar, simplesmente não notifica.
        log.exception("falha na notificação silenciosa")
