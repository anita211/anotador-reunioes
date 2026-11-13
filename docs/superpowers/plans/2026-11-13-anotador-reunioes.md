# Anotador de Reuniões Local — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Windows tray app that detects a Google Meet call in Opera, records system audio + mic locally, transcribes after the call with faster-whisper, summarizes with `claude -p`, and writes one Markdown file per meeting.

**Architecture:** One Python package `anotador`. Pure logic (detector state machine, track merging, filenames, pipeline orchestration) is separated from Windows-touching adapters (registry, window titles, WASAPI audio, tkinter popup, toast, tray) so the logic is unit-tested and adapters are verified manually. A background worker thread processes finished recordings one at a time.

**Tech Stack:** Python 3.12 (venv), `pyaudiowpatch` (WASAPI loopback + mic), `faster-whisper` (CPU int8), `pystray` + `Pillow` (tray), `tkinter` (popup), `windows-toasts` (silent toast), `pywin32` (window enumeration), `pytest`.

**Spec:** `C:\Users\anita\Downloads\anotador-reunioes\design.md`

## Global Constraints

- Project root: `C:\Users\anita\Downloads\anotador-reunioes` (git repo initialized in Task 1).
- User-facing text is Brazilian Portuguese.
- Only `opera.exe` is monitored by default; browser exe and title keyword come from `config.json`.
- Start rule: mic in use by browser **and** a browser window title contains `"Meet"`, checked for up to **30 s** after the mic turns on.
- Stop rule: mic released for more than **15 s** continuous.
- Poll interval: **3 s**.
- Warning popup: text "Gravando reunião", buttons **[OK]** and **[Cancelar gravação]**, closes on OK or after **15 s**, never shown again in the same meeting. No other UI during a meeting.
- Audio never leaves the PC; only transcript text goes to `claude -p`.
- WAV files are deleted only after the transcript has been written successfully.
- Whisper: model `small` default (configurable), `language="pt"`, `device="cpu"`, `compute_type="int8"`.
- Output: `%USERPROFILE%\Documents\Reuniões\AAAA-MM-DD HHhMM - <título>.md`, summary on top, full transcript below.
- Completion notification is silent (Action Center only, no popup).
- Tray icon does not change appearance while recording.
- If Claude fails, save transcript-only file and retry summary later (on next app start).

## Review Focus

1. User drops and rejoins the call within 15 s → must remain **one** recording (Task 2 test `test_short_mic_gap_keeps_recording`).
2. Opera uses the mic for something that is not Meet (no "Meet" title within 30 s) → never records that mic session, even if a Meet tab appears later in the same session (Task 2 test `test_no_meet_title_within_window_never_starts`).
3. Meet tab title with characters illegal in Windows filenames (`: / \ ? * " < > |`) or empty → valid filename (Task 4 test `test_filename_sanitizes_illegal_chars`, `test_filename_empty_title`).
4. Claude offline / CLI error / timeout → transcript still saved, pending marker created, WAVs deleted, retry later succeeds (Task 6 tests).
5. One track silent (e.g., user never speaks) → transcript contains only the other speaker, no crash (Task 4 test `test_merge_with_empty_track`).

---

## File Structure

```
anotador-reunioes/
  design.md
  config.example.json
  requirements.txt
  install.ps1                 # venv + deps + Startup shortcut
  anotador/
    __init__.py
    config.py                 # Config dataclass + load_config()
    detector.py               # MeetingDetector (pure) + windows probes
    recorder.py               # Recorder (pyaudiowpatch)
    transcriber.py            # transcribe_file(), merge_tracks(), format_transcript()
    summarizer.py             # summarize() via claude -p
    output.py                 # meeting_filename(), write_meeting()
    pipeline.py               # process_recording(), retry_pending()
    ui.py                     # show_recording_warning(), notify_silent()
    app.py                    # tray + main loop + worker thread
  tests/
    test_config.py
    test_detector.py
    test_transcriber.py
    test_output.py
    test_summarizer.py
    test_pipeline.py
```

---

### Task 1: Project setup and config

**Files:**
- Create: `requirements.txt`, `config.example.json`, `anotador/__init__.py`, `anotador/config.py`, `tests/test_config.py`, `.gitignore` (`.venv/`, `config.json`, `__pycache__/`, `*.wav`)

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Config:
      browser_exe: str = "opera.exe"
      title_keyword: str = "Meet"
      poll_seconds: float = 3.0
      title_wait_seconds: float = 30.0
      stop_grace_seconds: float = 15.0
      warning_seconds: float = 15.0
      whisper_model: str = "small"
      output_dir: Path = Path.home() / "Documents" / "Reuniões"
      work_dir: Path = Path(os.environ["LOCALAPPDATA"]) / "AnotadorReunioes"
      claude_cmd: str = "claude"
      claude_timeout_seconds: float = 600.0
  def load_config(path: Path | None) -> Config  # missing file -> defaults; unknown keys ignored; paths expanded
  ```

- [ ] **Step 1:** `git init` in project root; write `.gitignore`.
- [ ] **Step 2:** Create venv: `py -3.12 -m venv .venv`. If `py -3.12` is missing, `winget install -e --id Python.Python.3.12` first. Expected: `.venv\Scripts\python.exe --version` → `Python 3.12.x`.
- [ ] **Step 3:** `requirements.txt`: `pyaudiowpatch`, `faster-whisper`, `pystray`, `Pillow`, `windows-toasts`, `pywin32`, `pytest`. Install: `.venv\Scripts\pip install -r requirements.txt`. Expected: success.
- [ ] **Step 4: Failing tests** in `tests/test_config.py`:
  - `test_defaults_when_missing`: `load_config(tmp_path/"nope.json").browser_exe == "opera.exe"` and `.stop_grace_seconds == 15.0`.
  - `test_overrides_and_unknown_keys`: file `{"whisper_model": "medium", "foo": 1, "output_dir": "~/X"}` → `whisper_model == "medium"`, `output_dir == Path.home()/"X"`.
- [ ] **Step 5:** Run `.venv\Scripts\pytest tests/test_config.py -v` → FAIL.
- [ ] **Step 6:** Implement `Config` and `load_config` (json, `Path(...).expanduser()` for path fields).
- [ ] **Step 7:** Run → PASS. Write `config.example.json` with all defaults.
- [ ] **Step 8:** Commit `feat: project setup and config`.

---

### Task 2: Meeting detector

**Files:**
- Create: `anotador/detector.py`, `tests/test_detector.py`

**Interfaces:**
- Consumes: `Config`
- Produces:
  ```python
  class MeetingDetector:
      def __init__(self, title_wait_seconds: float, stop_grace_seconds: float): ...
      def update(self, now: float, mic_active: bool, meet_title: str | None) -> Literal["start", "stop"] | None
      @property
      def recording(self) -> bool
      @property
      def title(self) -> str | None          # title captured at start
      def cancel(self) -> None                # stop recording state WITHOUT emitting "stop"; suppress restart until mic is released
      def force_start(self, now: float) -> None   # manual start from tray
      def force_stop(self) -> None                # manual stop from tray; also suppress auto-restart until mic released
  def browser_mic_in_use(exe_name: str) -> bool
  def find_meet_title(exe_name: str, keyword: str) -> str | None
  ```

State machine rules (pure, `now` in seconds):
- idle + mic on → note `mic_on_since=now`. While mic stays on and `now - mic_on_since <= title_wait_seconds`, the first `meet_title` seen → emit `"start"`, capture title.
- Mic on past the window without a title → mark this mic session "ignored" until mic goes off.
- recording + mic off → note `mic_off_since`; mic back on before `stop_grace_seconds` elapsed → clear it; elapsed `> stop_grace_seconds` → emit `"stop"`.
- `cancel()`/`force_stop()` → not recording, current mic session "ignored".
- A recording begun with `force_start()` never auto-stops; only `force_stop()` ends it.

- [ ] **Step 1: Failing tests** (`d = MeetingDetector(30, 15)`):
  - `test_starts_when_mic_and_title`: `d.update(0, True, "Meet - abc") == "start"`; `d.title == "Meet - abc"`.
  - `test_title_appears_within_window`: updates at t=0,3 with `None` → `None`; t=27 with title → `"start"`.
  - `test_no_meet_title_within_window_never_starts`: t=0..33 no title; t=36 with title → `None`.
  - `test_stops_after_grace`: started at 0; mic off at 100 → `None`; t=115 → `None`; t=116 → `"stop"`.
  - `test_short_mic_gap_keeps_recording`: started; mic off t=100, on t=110, off t=200, t=214 → no `"stop"` emitted anywhere; `d.recording is True`.
  - `test_cancel_suppresses_restart_until_mic_off`: start, `cancel()`, t+3 mic on + title → `None`; mic off then on with title → `"start"`.
  - `test_no_mic_no_start`: `update(0, False, "Meet - x") is None`.
  - `test_force_start_ignores_mic_off`: `force_start(0)`; updates t=3..60 with mic off → never `"stop"`; `force_stop()` → `recording is False`.
- [ ] **Step 2:** Run `pytest tests/test_detector.py -v` → FAIL.
- [ ] **Step 3:** Implement `MeetingDetector`.
- [ ] **Step 4:** Implement probes:
  - `browser_mic_in_use`: open `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged`; for each subkey whose name ends with `#{exe_name}` (case-insensitive), return True if `LastUsedTimeStop == 0` and `LastUsedTimeStart != 0`. Any `OSError` → False.
  - `find_meet_title`: `win32gui.EnumWindows` over visible windows whose process image name is `exe_name` (via `win32process.GetWindowThreadProcessId` + `psutil`-free `OpenProcess`/`QueryFullProcessImageName`); return first title containing `keyword`, with trailing `" - Opera"` removed.
- [ ] **Step 5:** Run → PASS. Manual check: with Opera in a Meet test call (meet.google.com → "Nova reunião"), `python -c "from anotador.detector import *; print(browser_mic_in_use('opera.exe'), find_meet_title('opera.exe','Meet'))"` → `True Meet - ...`.
- [ ] **Step 6:** Commit `feat: meeting detector`.

---

### Task 3: Recorder

**Files:**
- Create: `anotador/recorder.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class Recording:
      system_wav: Path
      mic_wav: Path
      started_at: datetime
  class Recorder:
      def __init__(self, work_dir: Path): ...
      def start(self) -> None          # opens default output loopback + default mic, writes two WAVs in work_dir/"rec"
      def stop(self) -> Recording      # closes streams/files
      def cancel(self) -> None         # stops and deletes both WAVs
      @property
      def active(self) -> bool
  def find_leftover_recordings(work_dir: Path) -> list[Recording]
      # pairs of *-system.wav / *-mic.wav in work_dir/"rec"; started_at parsed from the filename prefix
  ```

Approach: `pyaudiowpatch.PyAudio().get_default_wasapi_loopback()` for system audio, default input for mic; each stream with callback writing frames to `wave` file at device native rate/channels. Both streams opened back-to-back so start offsets match (accept <100 ms drift). Filenames `YYYYmmdd-HHMMSS-system.wav` / `-mic.wav`.

- [ ] **Step 1:** Implement.
- [ ] **Step 2: Manual check:** script `start(); sleep 10 while playing a YouTube video and speaking; stop()` → both WAVs exist, ~10 s, system WAV has the video audio, mic WAV has voice.
- [ ] **Step 3:** Manual check `cancel()` → both files gone.
- [ ] **Step 4:** Manual check: start, kill the Python process, then `find_leftover_recordings(work_dir)` returns one `Recording` with the right `started_at`.
- [ ] **Step 5:** Commit `feat: audio recorder`.

---

### Task 4: Transcriber and output file

**Files:**
- Create: `anotador/transcriber.py`, `anotador/output.py`, `tests/test_transcriber.py`, `tests/test_output.py`

**Interfaces:**
- Consumes: `Recording`
- Produces:
  ```python
  @dataclass(frozen=True)
  class Segment: start: float; end: float; text: str
  @dataclass(frozen=True)
  class Line: start: float; speaker: str; text: str     # speaker in {"Eu", "Outros"}
  def transcribe_file(path: Path, model_name: str) -> list[Segment]
  def merge_tracks(me: list[Segment], others: list[Segment]) -> list[Line]   # sorted by start; ties: "Outros" first
  def format_transcript(lines: list[Line]) -> str                           # "[HH:MM:SS] Eu: texto" per line
  def meeting_filename(started_at: datetime, title: str | None) -> str
  def write_meeting(output_dir: Path, started_at: datetime, title: str | None,
                    summary: str | None, transcript: str) -> Path
  ```

- [ ] **Step 1: Failing tests:**
  - `test_merge_orders_by_start`: me `[Segment(5,6,"oi")]`, others `[Segment(1,2,"bom dia"), Segment(7,8,"tchau")]` → speakers `["Outros","Eu","Outros"]`.
  - `test_merge_with_empty_track`: `merge_tracks([], [Segment(0,1,"a")])` → one `Line(0,"Outros","a")`.
  - `test_format_transcript`: `Line(3725.4,"Eu","ok")` → `"[01:02:05] Eu: ok"`.
  - `test_filename_basic`: `meeting_filename(datetime(2026,11,13,14,5), "Meet - Alinhamento")` → `"2026-11-13 14h05 - Alinhamento.md"` (prefix `"Meet - "` stripped).
  - `test_filename_sanitizes_illegal_chars`: title `'Meet - Q4: plano/CTA? "x"'` → no char from `\/:*?"<>|`, ends `.md`, length ≤ 150.
  - `test_filename_empty_title`: `None` and `"Meet"` → `"2026-11-13 14h05 - Reunião.md"`.
  - `test_write_meeting_layout`: summary `"## Resumo\nx"`, transcript `"[00:00:01] Eu: oi"` → file exists; content order: `# <title>` header, summary, `## Transcrição`, transcript. With `summary=None` → contains `"_Resumo pendente_"`.
  - `test_write_meeting_no_overwrite`: writing twice same name → second gets `" (2).md"`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. `transcribe_file`: `WhisperModel(model_name, device="cpu", compute_type="int8")` cached module-level; `model.transcribe(str(path), language="pt", vad_filter=True)`.
- [ ] **Step 4:** Run → PASS. Manual: `transcribe_file` on the Task 3 mic WAV prints Portuguese text; note elapsed time.
- [ ] **Step 5:** Commit `feat: transcription and meeting file`.

---

### Task 5: Summarizer

**Files:**
- Create: `anotador/summarizer.py`, `tests/test_summarizer.py`

**Interfaces:**
- Produces:
  ```python
  class SummaryError(Exception): ...
  def summarize(transcript: str, claude_cmd: str, timeout: float,
                run=subprocess.run) -> str
  ```

Approach: `run([claude_cmd, "-p"], input=PROMPT + transcript, capture_output=True, text=True, encoding="utf-8", timeout=timeout)`. Non-zero exit, empty stdout, `FileNotFoundError`, or `TimeoutExpired` → `SummaryError`. If transcript > 400 000 chars, split on line boundaries into chunks ≤ 400 000, summarize each, then summarize the concatenated partial summaries with the same prompt.

PROMPT (exact copy):
```
Você recebe a transcrição de uma reunião em português. "Eu" é a dona destas anotações; "Outros" são os demais participantes.
Escreva em Markdown, em português, com exatamente estas seções:
## Resumo
(5 a 10 linhas)
## Decisões
## Pendências
(lista: tarefa — responsável, se citado — prazo, se citado)
## Pontos em aberto
Se uma seção não tiver conteúdo, escreva "Nenhum.". Não invente informações.

Transcrição:
```

- [ ] **Step 1: Failing tests** with a fake `run`:
  - `test_returns_stdout`: fake returns `returncode=0, stdout="## Resumo\nok"` → result `"## Resumo\nok"`; fake received `input` starting with `"Você recebe a transcrição"` and ending with the transcript.
  - `test_nonzero_raises`: returncode 1 → `SummaryError`.
  - `test_missing_cli_raises`: fake raises `FileNotFoundError` → `SummaryError`.
  - `test_timeout_raises`: fake raises `subprocess.TimeoutExpired` → `SummaryError`.
  - `test_long_transcript_chunks`: transcript of 900 000 chars → fake called 4 times (3 chunks + 1 combine).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Manual: `summarize("[00:00:01] Eu: Vamos entregar o relatório sexta.\n[00:00:05] Outros: Combinado, eu reviso.", "claude", 600)` returns Markdown with the four sections.
- [ ] **Step 6:** Commit `feat: claude summarizer`.

---

### Task 6: Pipeline

**Files:**
- Create: `anotador/pipeline.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Recording`, `transcribe_file`, `merge_tracks`, `format_transcript`, `summarize`, `SummaryError`, `write_meeting`, `Config`
- Produces:
  ```python
  def process_recording(rec: Recording, title: str | None, cfg: Config,
                        transcribe=transcribe_file, summarize_fn=summarize,
                        notify=lambda path, ok: None) -> Path
  def retry_pending(cfg: Config, summarize_fn=summarize) -> list[Path]
  ```

Behavior: transcribe mic → "Eu", system → "Outros"; write transcript-only file first (`summary=None`); delete both WAVs; try summary → rewrite same file with summary. On `SummaryError`: write `cfg.work_dir/"pending"/<stem>.json` with `{"md_path", "title", "started_at"}`, leave transcript-only file. `notify(path, ok)` called once at the end. `retry_pending`: for each pending JSON, read transcript section from the .md, summarize, rewrite, delete JSON; failures leave JSON in place.

- [ ] **Step 1: Failing tests** (fake transcribe returns fixed segments; tmp dirs; real tiny WAV files created as empty files):
  - `test_success_writes_summary_and_deletes_wavs`: file contains summary text and `## Transcrição`; both WAV paths no longer exist; no pending JSON; `notify` called with `ok=True`.
  - `test_summary_failure_keeps_transcript_and_marks_pending`: `summarize_fn` raises `SummaryError` → file contains `_Resumo pendente_` and transcript; WAVs deleted; one pending JSON; `notify(ok=False)`.
  - `test_transcribe_failure_keeps_wavs`: fake transcribe raises → exception propagates, WAVs still exist.
  - `test_retry_pending_success`: after failure test, `retry_pending` with working fake → file now has summary, JSON gone, returns `[path]`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: processing pipeline`.

---

### Task 7: UI — warning popup and silent notification

**Files:**
- Create: `anotador/ui.py`

**Interfaces:**
- Produces:
  ```python
  def show_recording_warning(on_cancel: Callable[[], None], timeout_seconds: float) -> None
      # non-blocking; runs its own Tk root on a daemon thread
  def notify_silent(md_path: Path, ok: bool) -> None
  ```

Popup: borderless (`overrideredirect(True)`), `-topmost`, bottom-right above the taskbar, small (≈260×90), label "Gravando reunião", buttons "OK" (close) and "Cancelar gravação" (close + `on_cancel()`), auto-close via `after(timeout_seconds*1000)`. Not in taskbar.
Silent notification: `windows-toasts` `InteractableWindowsToaster("Anotador de Reuniões")`, toast text `"Anotações prontas: <nome>"` (ok) / `"Transcrição salva; resumo pendente: <nome>"` (not ok), with `suppress_popup=True` so it goes straight to the Action Center; click opens the file. If suppress-popup isn't supported by the library version, set the toast via WinRT `ToastNotification.SuppressPopup = True`; if neither works, skip the toast (no popup is the hard rule).

- [ ] **Step 1:** Implement.
- [ ] **Step 2: Manual check:** `show_recording_warning(lambda: print("cancel"), 15)` → popup bottom-right; OK closes; reopen and wait → closes at 15 s; Cancel prints `cancel`.
- [ ] **Step 3: Manual check:** `notify_silent(Path("x.md"), True)` → no popup on screen; message present in Action Center (Win+N).
- [ ] **Step 4:** Commit `feat: warning popup and silent notification`.

---

### Task 8: Tray app, main loop, install

**Files:**
- Create: `anotador/app.py`, `anotador/__main__.py`, `install.ps1`

**Interfaces:**
- Consumes: everything above.
- Produces: `python -m anotador` entry point.

Behavior:
- On start: `load_config(work_dir/"config.json")`, ensure dirs, single-instance guard (named mutex `AnotadorReunioesMutex` via `win32event`; exit if exists), run `retry_pending` on the worker thread.
- Poll thread every `poll_seconds`: `ev = detector.update(time.monotonic(), browser_mic_in_use(...), find_meet_title(...))` (call `find_meet_title` only when mic is in use). `"start"` → `recorder.start()`, `show_recording_warning(on_cancel=cancel_current, warning_seconds)`. `"stop"` → `rec = recorder.stop()`, enqueue `(rec, detector.title)`.
- `cancel_current`: `recorder.cancel()`, `detector.cancel()`.
- Worker thread: takes queue items one at a time → `process_recording(..., notify=notify_silent)`; exceptions logged to `work_dir/"anotador.log"` (rotating, 1 MB), never crash the app.
- Tray (`pystray`): fixed neutral icon (simple gray note glyph drawn with Pillow), tooltip "Anotador". Menu (Portuguese): "Iniciar gravação" (`detector.force_start` + `recorder.start`, no popup), "Parar gravação" (`detector.force_stop` + stop + enqueue), "Abrir pasta de reuniões", "Sair" (if recording: `recorder.stop()` and leave the WAVs on disk; exit without waiting for the worker).
- On every startup, before polling begins, enqueue each `Recording` from `find_leftover_recordings(work_dir)` with title `None` (covers exit mid-recording, exit with queued items, and crashes).
- `install.ps1`: creates venv if missing, installs requirements, copies `config.example.json` → `%LOCALAPPDATA%\AnotadorReunioes\config.json` if absent, creates shortcut `shell:startup\Anotador de Reuniões.lnk` → `.venv\Scripts\pythonw.exe -m anotador` with working dir = project root, then launches it.

- [ ] **Step 1:** Implement `app.py`, `__main__.py`, `install.ps1`.
- [ ] **Step 2:** Run full test suite `.venv\Scripts\pytest -v` → all PASS.
- [ ] **Step 3: End-to-end manual check** (use a solo Meet test call in Opera; play a YouTube video in another tab for "Outros" audio):
  - join → popup within ~6 s; click OK; speak ~1 min; leave call → ~16 s later recording stops;
  - after processing: `Documentos\Reuniões\<data> - <título>.md` has the four summary sections and transcript with "Eu" and "Outros"; WAVs gone; Action Center has silent notice, no popup appeared.
  - Repeat, click "Cancelar gravação" → no file produced, no restart during that call.
  - Disconnect Wi-Fi before leaving a call → transcript-only file with `_Resumo pendente_`; reconnect, restart app → summary filled.
- [ ] **Step 4:** Run `install.ps1`; reboot (or sign out/in) → tray icon present, no console window.
- [ ] **Step 5:** Commit `feat: tray app and installer`.
