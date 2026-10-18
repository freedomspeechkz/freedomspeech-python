"""Команда ``fs`` с поддельным клиентом. / CLI against a fake client."""
from __future__ import annotations

import json

import pytest

from freedomspeech import cli
from freedomspeech.audio import pcm16_to_wav, silence
from freedomspeech.errors import AuthenticationError
from freedomspeech.models import Transcription, TranscriptEvent, Translation, Voice

WAV = pcm16_to_wav(b"\x00\x00" * 240, 24000)


class FakeClient:
    def __init__(self, **kw):
        self.kw = kw
        self.calls = []

    def transcribe(self, *a, **kw):
        self.calls.append(("transcribe", a, kw))
        return Transcription.from_json({"text": "Сәлем әлем", "duration": 1.0, "language": "kk"})

    def stream_transcribe(self, chunks, **kw):
        frames = list(chunks)
        self.calls.append(("stream_transcribe", len(frames), kw))
        yield TranscriptEvent.from_json({"type": "partial", "text": "сә"})
        yield TranscriptEvent.from_json({"type": "final", "text": "сәлем", "language": "kk"})

    def speak(self, *a, **kw):
        self.calls.append(("speak", a, kw))
        return WAV

    def stream_speech(self, *a, **kw):
        self.calls.append(("stream_speech", a, kw))
        yield b"\x01\x00"
        yield b"\x02\x00"

    def clone_voice(self, *a, **kw):
        self.calls.append(("clone_voice", a, kw))
        return WAV

    def voices(self):
        return [Voice.from_json({"id": "tomiris", "name": "Tomiris", "type": "builtin"})]

    def translate(self, *a, **kw):
        self.calls.append(("translate", a, kw))
        return [Translation.from_json({"text": "Сәлем"})]

    def health(self):
        return {"status": "healthy"}


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def factory(**kw):
        holder["client"] = FakeClient(**kw)
        return holder["client"]

    monkeypatch.setattr(cli, "FreedomSpeech", factory)
    return holder


def test_transcribe_plain(fake, capsys, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    assert cli.main(["--api-key", "k", "transcribe", str(f)]) == 0
    assert capsys.readouterr().out.strip() == "Сәлем әлем"
    c = fake["client"]
    assert c.kw["api_key"] == "k"
    name, args, kw = c.calls[0]
    assert args == (str(f),) and kw["prettify"] is True and kw["diarize"] is False


def test_transcribe_diarize_json(fake, capsys, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert cli.main(["transcribe", str(f), "--raw", "--names", "A", "B", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["text"] == "Сәлем әлем"
    kw = fake["client"].calls[0][2]
    assert kw["prettify"] is False and kw["diarize"] is True and kw["speaker_names"] == ["A", "B"]


def test_transcribe_stream(fake, capsys, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(pcm16_to_wav(silence(250), 16000))
    assert cli.main(["transcribe", str(f), "--stream"]) == 0
    assert capsys.readouterr().out.strip() == "сәлем"
    name, n_frames, kw = fake["client"].calls[0]
    assert name == "stream_transcribe" and n_frames == 3  # 250 мс = 3 кадра по 100 мс


def test_transcribe_stream_wrong_wav(fake, capsys, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(pcm16_to_wav(silence(100), 24000))
    assert cli.main(["transcribe", str(f), "--stream"]) == 2
    assert "16 кГц" in capsys.readouterr().err


def test_speak_to_file(fake, tmp_path):
    out = tmp_path / "out.wav"
    assert cli.main(["speak", "Сәлем", "--voice", "mia_call_center_kz", "-o", str(out), "--language", "kk"]) == 0
    assert out.read_bytes() == WAV
    name, args, kw = fake["client"].calls[0]
    assert name == "speak" and args == ("Сәлем", "mia_call_center_kz") and kw["language"] == "kk"


def test_speak_stream_wraps_wav(fake, tmp_path):
    out = tmp_path / "out.wav"
    assert cli.main(["speak", "Сәлем", "--stream", "-o", str(out)]) == 0
    data = out.read_bytes()
    assert data[:4] == b"RIFF" and data[-4:] == b"\x01\x00\x02\x00"
    name, args, kw = fake["client"].calls[0]
    assert name == "stream_speech" and kw["model"] == "freedomspeech-flash"


def test_speak_to_stdout(fake, capsysbinary):
    assert cli.main(["speak", "Сәлем"]) == 0
    assert capsysbinary.readouterr().out == WAV


def test_clone(fake, tmp_path):
    out = tmp_path / "c.wav"
    assert cli.main(["clone", "Текст", "--ref", "me.wav", "--speed", "1.1", "-o", str(out)]) == 0
    name, args, kw = fake["client"].calls[0]
    assert name == "clone_voice" and args == ("Текст",)
    assert kw["reference_audio"] == "me.wav" and kw["speed"] == 1.1 and kw["voice_id"] is None
    assert out.read_bytes() == WAV


def test_voices_translate_health(fake, capsys):
    assert cli.main(["voices"]) == 0
    assert "tomiris" in capsys.readouterr().out
    assert cli.main(["translate", "Привет", "--to", "kk"]) == 0
    assert capsys.readouterr().out.strip() == "Сәлем"
    assert fake["client"].calls[-1] == ("translate", (["Привет"], "kk"), {"source_lang": None})
    assert cli.main(["health"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "healthy"


def test_api_error_exit_code(monkeypatch, capsys):
    class Bad(FakeClient):
        def voices(self):
            raise AuthenticationError("Ключ не принят", status=401)

    monkeypatch.setattr(cli, "FreedomSpeech", lambda **kw: Bad(**kw))
    assert cli.main(["voices"]) == 1
    assert "Ключ не принят" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["--version"])
    assert ei.value.code == 0
    assert "freedomspeech 0.1.0" in capsys.readouterr().out
