import json
from pathlib import Path

from anotador.config import load_config


def test_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.json")
    assert cfg.browser_exe == "opera.exe"
    assert cfg.stop_grace_seconds == 15.0
    assert cfg.title_wait_seconds == 30.0
    assert cfg.warning_seconds == 15.0
    assert cfg.whisper_model == "small"


def test_none_path_gives_defaults():
    assert load_config(None).title_keyword == "Meet"


def test_overrides_and_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"whisper_model": "medium", "foo": 1, "output_dir": "~/X"}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.whisper_model == "medium"
    assert cfg.output_dir == Path.home() / "X"


def test_default_output_dir_is_windows_documents_folder():
    import ctypes.wintypes
    from anotador.config import Config
    buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_PERSONAL
    assert Config().output_dir == Path(buf.value) / "Reuniões"
