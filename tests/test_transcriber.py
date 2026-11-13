from anotador.transcriber import Line, Segment, format_transcript, merge_tracks


def test_merge_orders_by_start():
    me = [Segment(5, 6, "oi")]
    others = [Segment(1, 2, "bom dia"), Segment(7, 8, "tchau")]
    assert [l.speaker for l in merge_tracks(me, others)] == ["Outros", "Eu", "Outros"]


def test_merge_ties_put_outros_first():
    lines = merge_tracks([Segment(1, 2, "a")], [Segment(1, 2, "b")])
    assert [l.speaker for l in lines] == ["Outros", "Eu"]


def test_merge_with_empty_track():
    assert merge_tracks([], [Segment(0, 1, "a")]) == [Line(0, "Outros", "a")]


def test_merge_strips_text_and_drops_blank():
    lines = merge_tracks([Segment(0, 1, "  oi  "), Segment(2, 3, "   ")], [])
    assert lines == [Line(0, "Eu", "oi")]


def test_format_transcript():
    assert format_transcript([Line(3725.4, "Eu", "ok")]) == "[01:02:05] Eu: ok"


def test_format_transcript_multiline():
    out = format_transcript([Line(0, "Outros", "a"), Line(61, "Eu", "b")])
    assert out == "[00:00:00] Outros: a\n[00:01:01] Eu: b"


def _write_wav(path, rate, channels, seconds, amp=0.5):
    import math, struct, wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            v = int(amp * 32767 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<h", v) * channels
        w.writeframes(bytes(frames))


def test_load_wav_resamples_to_16k_mono(tmp_path):
    import numpy as np
    from anotador.transcriber import load_wav_16k_mono
    p = tmp_path / "a.wav"
    _write_wav(p, 48000, 2, 1.0)
    audio = load_wav_16k_mono(p)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert abs(len(audio) - 16000) <= 2
    assert 0.4 < float(np.abs(audio).max()) <= 1.0


def test_load_wav_empty(tmp_path):
    from anotador.transcriber import load_wav_16k_mono
    p = tmp_path / "e.wav"
    _write_wav(p, 44100, 1, 0)
    assert len(load_wav_16k_mono(p)) == 0


def test_drop_echo_removes_my_copy_of_others_speech():
    from anotador.transcriber import drop_echo
    others = [Segment(10, 14, "Vamos entregar o relatório na sexta-feira.")]
    me = [Segment(10.3, 14.2, "vamos entregar o relatório na sexta feira"), Segment(20, 22, "Combinado.")]
    assert drop_echo(me, others) == [Segment(20, 22, "Combinado.")]


def test_drop_echo_keeps_overlapping_different_speech():
    from anotador.transcriber import drop_echo
    others = [Segment(10, 14, "Vamos entregar o relatório na sexta-feira.")]
    me = [Segment(11, 13, "Eu acho melhor na segunda.")]
    assert drop_echo(me, others) == me


def test_drop_echo_keeps_same_text_far_in_time():
    from anotador.transcriber import drop_echo
    others = [Segment(10, 12, "Combinado.")]
    me = [Segment(100, 101, "Combinado.")]
    assert drop_echo(me, others) == me


def test_is_probably_speech_filters_hallucinations():
    from anotador.transcriber import is_probably_speech
    assert is_probably_speech(no_speech_prob=0.1, avg_logprob=-0.3)
    assert not is_probably_speech(no_speech_prob=0.8, avg_logprob=-0.3)
    assert not is_probably_speech(no_speech_prob=0.1, avg_logprob=-1.5)
