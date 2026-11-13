import subprocess

import pytest

from anotador.summarizer import SummaryError, summarize


class FakeRun:
    def __init__(self, returncode=0, stdout="## Resumo\nok", exc=None):
        self.calls = []
        self.returncode, self.stdout, self.exc = returncode, stdout, exc

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "erro")


def test_returns_stdout():
    run = FakeRun()
    assert summarize("[00:00:01] Eu: oi", "claude", 600, run=run) == "## Resumo\nok"
    args, kw = run.calls[0]
    assert args[0].lower().replace(".exe", "").endswith("claude") and "-p" in args
    assert kw["input"].startswith("Você recebe a transcrição")
    assert kw["input"].endswith("[00:00:01] Eu: oi")
    assert kw["timeout"] == 600


def test_nonzero_raises():
    with pytest.raises(SummaryError):
        summarize("x", "claude", 600, run=FakeRun(returncode=1))


def test_empty_stdout_raises():
    with pytest.raises(SummaryError):
        summarize("x", "claude", 600, run=FakeRun(stdout="  \n"))


def test_missing_cli_raises():
    with pytest.raises(SummaryError):
        summarize("x", "claude", 600, run=FakeRun(exc=FileNotFoundError()))


def test_timeout_raises():
    with pytest.raises(SummaryError):
        summarize("x", "claude", 600, run=FakeRun(exc=subprocess.TimeoutExpired("claude", 600)))


def test_long_transcript_chunks():
    run = FakeRun()
    line = "[00:00:01] Outros: " + "a" * 80 + "\n"
    transcript = line * (900_000 // len(line) + 1)
    summarize(transcript, "claude", 600, run=run)
    assert len(run.calls) == 4
