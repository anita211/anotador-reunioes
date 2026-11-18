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

def mic_active_from_sessions(sessions, exe_name: str) -> bool:
    """sessions: (nome do executável, estado) de cada sessão de captura; estado 1 = ativa."""
    target = exe_name.lower()
    return any(name.lower() == target and state == 1 for name, state in sessions)


def _pid_image(pid: int) -> str | None:
    import win32api
    import win32con
    import win32process

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


def _capture_sessions(flow: str = "capture") -> list[tuple[str, int]]:
    """Sessões de áudio do Windows: "capture" (microfones) ou "render" (alto-falantes/fones)."""
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities, IAudioSessionControl2, IAudioSessionManager2

    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # já inicializado nesta thread
    out = []
    devices = AudioUtilities.GetDeviceEnumerator().EnumAudioEndpoints(
        (EDataFlow.eRender if flow == "render" else EDataFlow.eCapture).value,
        DEVICE_STATE.ACTIVE.value,
    )
    for i in range(devices.GetCount()):
        try:
            manager = devices.Item(i).Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
            sessions = manager.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                control = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                name = _pid_image(control.GetProcessId())
                if name:
                    out.append((name, control.GetState()))
        except Exception:
            continue
    return out


def browser_mic_in_use(exe_name: str) -> bool:
    """True se o navegador tem uma sessão de microfone ativa agora."""
    return mic_active_from_sessions(_capture_sessions(), exe_name)


def browser_playing_audio(exe_name: str) -> bool:
    """True se o navegador está tocando áudio agora (ex.: a chamada, mesmo com você muda)."""
    return mic_active_from_sessions(_capture_sessions("render"), exe_name)


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
