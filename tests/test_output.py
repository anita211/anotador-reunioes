from datetime import datetime

from anotador.output import meeting_filename, write_meeting

T = datetime(2026, 11, 13, 14, 5)


def test_filename_basic():
    assert meeting_filename(T, "Meet - Alinhamento") == "2026-11-13 14h05 - Alinhamento.md"


def test_filename_sanitizes_illegal_chars():
    name = meeting_filename(T, r'Meet - Q4: plano/CTA? "x" <a>|b*\c')
    assert not any(c in name for c in r'\/:*?"<>|')
    assert name.endswith(".md")
    assert len(name) <= 150


def test_filename_long_title_truncated():
    assert len(meeting_filename(T, "Meet - " + "x" * 500)) <= 150


def test_filename_empty_title():
    assert meeting_filename(T, None) == "2026-11-13 14h05 - Reunião.md"
    assert meeting_filename(T, "Meet") == "2026-11-13 14h05 - Reunião.md"
    assert meeting_filename(T, "Meet - ") == "2026-11-13 14h05 - Reunião.md"


def test_write_meeting_layout(tmp_path):
    p = write_meeting(tmp_path / "out", T, "Meet - Alinhamento", "## Resumo\nx", "[00:00:01] Eu: oi")
    text = p.read_text(encoding="utf-8")
    assert p.exists()
    i_title = text.index("# Alinhamento")
    i_sum = text.index("## Resumo\nx")
    i_tr = text.index("## Transcrição")
    i_line = text.index("[00:00:01] Eu: oi")
    assert i_title < i_sum < i_tr < i_line


def test_write_meeting_pending_summary(tmp_path):
    p = write_meeting(tmp_path, T, None, None, "[00:00:01] Eu: oi")
    assert "_Resumo pendente_" in p.read_text(encoding="utf-8")


def test_write_meeting_no_overwrite(tmp_path):
    a = write_meeting(tmp_path, T, "Meet - A", None, "x")
    b = write_meeting(tmp_path, T, "Meet - A", None, "y")
    assert a != b
    assert b.name == "2026-11-13 14h05 - A (2).md"
    assert a.read_text(encoding="utf-8").endswith("x\n")


def test_write_meeting_rewrites_given_path(tmp_path):
    from anotador.output import read_transcript
    a = write_meeting(tmp_path, T, "Meet - A", None, "[00:00:01] Eu: oi\n[00:00:02] Outros: olá")
    b = write_meeting(tmp_path, T, "Meet - A", "## Resumo\nok", read_transcript(a), path=a)
    assert a == b
    text = a.read_text(encoding="utf-8")
    assert "## Resumo\nok" in text and "_Resumo pendente_" not in text
    assert read_transcript(a) == "[00:00:01] Eu: oi\n[00:00:02] Outros: olá"
