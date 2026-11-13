"""Detecta quando uma reunião do Meet começa e termina no navegador."""
from __future__ import annotations

import re
from typing import Literal

Event = Literal["start", "resume", "stop"]

SUPPRESS_SECONDS = 3 * 3600   # reunião cancelada/parada à mão não volta a gravar sozinha por 3 h
RESUME_SECONDS = 10 * 60      # voltar à mesma reunião em até 10 min retoma sem novo aviso


def is_meet_call_title(title: str, keyword: str) -> bool:
    """Título de aba de chamada do Meet: "Meet - abc-defg-hij", "Meet – Nome", "Meet: Nome"."""
    return re.match(rf"^{re.escape(keyword)}\s*[-–—:]\s*\S", title) is not None


class MeetingDetector:
    """Máquina de estados pura: recebe observações periódicas e decide início/fim."""

    def __init__(self, title_wait_seconds: float, stop_grace_seconds: float):
        self._title_wait = title_wait_seconds
        self._grace = stop_grace_seconds
        self._recording = False
        self._manual = False
        self._title: str | None = None
        self._mic_on_since: float | None = None
        self._mic_off_since: float | None = None
        self._ignored = False  # sessão de microfone atual não deve iniciar gravação
        self._now = 0.0
        self._suppressed: dict[str, float] = {}  # título -> até quando não iniciar
        self._last_stop: tuple[str | None, float] | None = None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def title(self) -> str | None:
        return self._title

    def update(self, now: float, mic_active: bool, meet_title: str | None) -> Event | None:
        self._now = now
        if self._recording:
            return self._update_recording(now, mic_active)
        return self._update_idle(now, mic_active, meet_title)

    def _update_idle(self, now: float, mic_active: bool, meet_title: str | None) -> Event | None:
        if not mic_active:
            self._mic_on_since = None
            self._ignored = False
            return None
        if self._ignored:
            return None
        if self._mic_on_since is None:
            self._mic_on_since = now
        if now - self._mic_on_since > self._title_wait:
            self._ignored = True
            return None
        if meet_title:
            if self._suppressed.get(meet_title, -1.0) > now:
                return None
            resume = (
                self._last_stop is not None
                and self._last_stop[0] == meet_title
                and now - self._last_stop[1] <= RESUME_SECONDS
            )
            self._begin(meet_title, manual=False)
            return "resume" if resume else "start"
        return None

    def _update_recording(self, now: float, mic_active: bool) -> Event | None:
        if self._manual:
            return None
        if mic_active:
            self._mic_off_since = None
            return None
        if self._mic_off_since is None:
            self._mic_off_since = now
            return None
        if now - self._mic_off_since > self._grace:
            self._last_stop = (self._title, now)
            self._end(ignore_current_mic=False)
            return "stop"
        return None

    def _begin(self, title: str | None, manual: bool) -> None:
        self._recording = True
        self._manual = manual
        self._title = title
        self._mic_off_since = None

    def _end(self, ignore_current_mic: bool) -> None:
        self._recording = False
        self._manual = False
        self._mic_off_since = None
        self._mic_on_since = None
        self._ignored = ignore_current_mic

    def _suppress_current(self) -> None:
        if self._title:
            self._suppressed[self._title] = self._now + SUPPRESS_SECONDS
        self._last_stop = None

    def cancel(self) -> None:
        """Cancela sem emitir "stop"; a mesma reunião não volta a gravar sozinha (3 h)."""
        self._suppress_current()
        self._end(ignore_current_mic=True)

    def interrupt(self, now: float) -> None:
        """Encerra por motivo externo (suspensão, limite de duração); a mesma reunião pode retomar sem aviso."""
        self._last_stop = (self._title, now) if self._title else None
        self._end(ignore_current_mic=False)

    def force_start(self, now: float) -> None:
        self._now = now
        self._begin(None, manual=True)

    def force_stop(self) -> None:
        self._suppress_current()
        self._end(ignore_current_mic=True)


# --- Sondas do Windows (verificadas manualmente) ---------------------------------

_MIC_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone\NonPackaged"
)


def mic_in_use_from_entries(entries, exe_name: str, process_running: bool) -> bool:
    """Decide pelo registro: (nome, LastUsedTimeStart, LastUsedTimeStop) de cada app.

    Exige o processo rodando: se o navegador travou segurando o microfone, o registro
    pode ficar com Stop=0 para sempre.
    """
    if not process_running:
        return False
    suffix = "#" + exe_name.lower()
    return any(name.lower().endswith(suffix) and start and stop == 0 for name, start, stop in entries)


def _mic_entries() -> list[tuple[str, int, int]]:
    import winreg

    entries = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_KEY)
    except OSError:
        return entries
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, name) as sub:
                    start, _ = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                    stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
            except OSError:
                continue
            entries.append((name, start, stop))
    return entries


def _process_running(exe_name: str) -> bool:
    import win32api
    import win32con
    import win32process

    target = exe_name.lower()
    for pid in win32process.EnumProcesses():
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except Exception:
            continue
        try:
            if win32process.GetModuleFileNameEx(handle, 0).rsplit("\\", 1)[-1].lower() == target:
                return True
        except Exception:
            pass
        finally:
            win32api.CloseHandle(handle)
    return False


def browser_mic_in_use(exe_name: str) -> bool:
    """True se o Windows registra o navegador usando o microfone agora."""
    entries = _mic_entries()
    if not mic_in_use_from_entries(entries, exe_name, process_running=True):
        return False
    return _process_running(exe_name)


def _process_image_name(hwnd: int) -> str | None:
    import win32api
    import win32con
    import win32process

    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return None
    try:
        return win32process.GetModuleFileNameEx(handle, 0).rsplit("\\", 1)[-1]
    except Exception:
        return None
    finally:
        win32api.CloseHandle(handle)


def find_meet_title(exe_name: str, keyword: str) -> str | None:
    """Título da primeira janela visível do navegador que contém a palavra-chave."""
    import win32gui

    found: list[str] = []

    def visit(hwnd, _):
        if found or not win32gui.IsWindowVisible(hwnd):
            return True
        title = _strip_browser_suffix(win32gui.GetWindowText(hwnd))
        if not is_meet_call_title(title, keyword):
            return True
        image = _process_image_name(hwnd)
        if image and image.lower() == exe_name.lower():
            found.append(title)
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        return None
    return found[0] if found else None


def _strip_browser_suffix(title: str) -> str:
    for tail in (" - Opera", " – Opera"):
        if title.endswith(tail):
            return title[: -len(tail)]
    return title
