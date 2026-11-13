"""Detecta quando uma reunião do Meet começa e termina no navegador."""
from __future__ import annotations

from typing import Literal

Event = Literal["start", "stop"]


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

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def title(self) -> str | None:
        return self._title

    def update(self, now: float, mic_active: bool, meet_title: str | None) -> Event | None:
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
            self._begin(meet_title, manual=False)
            return "start"
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

    def cancel(self) -> None:
        """Cancela a gravação atual sem emitir "stop"; não reinicia até o microfone ser liberado."""
        self._end(ignore_current_mic=True)

    def force_start(self, now: float) -> None:
        self._begin(None, manual=True)

    def force_stop(self) -> None:
        self._end(ignore_current_mic=True)


# --- Sondas do Windows (verificadas manualmente) ---------------------------------

_MIC_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone\NonPackaged"
)


def browser_mic_in_use(exe_name: str) -> bool:
    """True se o Windows registra o executável usando o microfone agora."""
    import winreg

    suffix = "#" + exe_name.lower()
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_KEY)
    except OSError:
        return False
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                return False
            i += 1
            if not name.lower().endswith(suffix):
                continue
            try:
                with winreg.OpenKey(root, name) as sub:
                    start, _ = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                    stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
            except OSError:
                continue
            if start and stop == 0:
                return True


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
        title = win32gui.GetWindowText(hwnd)
        if keyword.lower() not in title.lower():
            return True
        image = _process_image_name(hwnd)
        if image and image.lower() == exe_name.lower():
            found.append(title)
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        return None
    if not found:
        return None
    title = found[0]
    for tail in (" - Opera", " – Opera"):
        if title.endswith(tail):
            title = title[: -len(tail)]
    return title
