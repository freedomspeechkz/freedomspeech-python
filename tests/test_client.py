"""HTTP-методы клиента с поддельной сессией. / HTTP client methods against a fake session."""
from __future__ import annotations

import base64
import io

import pytest

from freedomspeech import (AuthenticationError, ConnectionFailed, FreedomSpeech, FreedomSpeechError,
                           Transcription)
from freedomspeech.audio import pcm16_to_wav

from conftest import FakeResponse, FakeSession


WAV = pcm16_to_wav(b"\x00\x00" * 2400, 24000)


# ---------------------------------------------------------------- ключ и заголовки

def test_key_from_env(monkeypatch, session):
    monkeypatch.setenv("FS_API_KEY", "env-key")
    fs = FreedomSpeech(session=session)
    assert fs.api_key == "env-key"
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    fs.speak("Сәлем")
    assert session.last["headers"]["X-API-Key"] == "env-key"
    assert "Authorization" not in session.last["headers"]


def test_bearer_flag(session):
    fs = FreedomSpeech(api_key="k", session=session, bearer_auth=True)
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    fs.speak("Сәлем")
    h = session.last["headers"]
    assert h["X-API-Key"] == "k"
    assert h["Authorization"] == "Bearer k"
    assert "freedomspeech-python" in h["User-Agent"]


def test_missing_key_raises_before_request(monkeypatch, session):
    monkeypatch.delenv("FS_API_KEY", raising=False)
    fs = FreedomSpeech(session=session)
    with pytest.raises(AuthenticationError):
        fs.speak("Сәлем")
    assert session.calls == []


def test_public_endpoints_without_key(monkeypatch, session):
    monkeypatch.delenv("FS_API_KEY", raising=False)
    fs = FreedomSpeech(session=session)
    session.push(FakeResponse(200, json_body=[{"id": "tomiris", "name": "Tomiris", "type": "builtin"}]))
    voices = fs.voices()
    assert voices[0].id == "tomiris" and voices[0].type == "builtin"
    assert "X-API-Key" not in session.last["headers"]
    assert session.last["url"] == "https://api.freedomspeech.kz/api/voices"


def test_base_url_and_ws_url(session):
    fs = FreedomSpeech(api_key="k", base_url="https://example.test/", session=session)
    assert fs.base_url == "https://example.test"
    assert fs.ws_base_url == "wss://example.test"
    assert fs._ws_url("/ws/asr", prettify="false") == "wss://example.test/ws/asr?apiKey=k&prettify=false"


# ---------------------------------------------------------------- ошибки

def test_401_message(client, session):
    session.push(FakeResponse(401, json_body={"error": "Invalid or missing API key"}))
    with pytest.raises(AuthenticationError) as ei:
        client.speak("Сәлем")
    err = ei.value
    assert err.status == 401
    assert "Ключ не принят" in str(err)
    assert "FS_API_KEY" in str(err)
    assert err.body == {"error": "Invalid or missing API key"}


def test_error_body_and_code(client, session):
    session.push(FakeResponse(502, json_body={"error": "Voice cloning failed",
                                              "details": "Voice service is temporarily unavailable"}))
    with pytest.raises(FreedomSpeechError) as ei:
        client.clone_voice("text", voice_id="alikhan_kz")
    assert ei.value.status == 502
    assert "Voice cloning failed" in str(ei.value)
    assert ei.value.body["details"].startswith("Voice service")


def test_error_non_json_body(client, session):
    session.push(FakeResponse(500, content=b"<html>boom</html>", content_type="text/html"))
    with pytest.raises(FreedomSpeechError) as ei:
        client.health()
    assert ei.value.status == 500
    assert "boom" in str(ei.value)


def test_network_error(client, session):
    import requests

    class Boom(FakeSession):
        def request(self, *a, **kw):
            raise requests.ConnectionError("dns failed")

    fs = FreedomSpeech(api_key="k", session=Boom())
    with pytest.raises(ConnectionFailed):
        fs.voices()


# ---------------------------------------------------------------- распознавание

def test_transcribe_bytes_multipart(client, session):
    session.push(FakeResponse(200, json_body={"text": "Сәлем", "duration": 1.5,
                                              "processingTime": 0.4, "language": "kk"}))
    r = client.transcribe(b"RIFF....", filename="a.wav")
    assert isinstance(r, Transcription)
    assert r.text == "Сәлем" and r.duration == 1.5 and r.processing_time == 0.4 and r.language == "kk"
    assert r.segments == []
    call = session.last
    assert call["method"] == "POST"
    assert call["url"] == "https://api.freedomspeech.kz/v1/audio/transcriptions"
    name, blob, mime = call["files"]["file"]
    assert (name, blob, mime) == ("a.wav", b"RIFF....", "audio/x-wav") or mime == "audio/wav"
    assert call["data"] == {"prettify": "true"}
    assert call["json"] is None


def test_transcribe_path_and_options(client, session, tmp_path):
    p = tmp_path / "call.mp3"
    p.write_bytes(b"ID3abc")
    session.push(FakeResponse(200, json_body={
        "task": "transcribe", "text": "Agent: a\nClient: b", "duration": 12.5, "language": "kk",
        "segments": [
            {"type": "transcript.text.segment", "id": "seg_001", "start": 1.128, "end": 3.512, "text": "a", "speaker": "Agent"},
            {"type": "transcript.text.segment", "id": "seg_002", "start": 4.232, "end": 11.448, "text": "b", "speaker": "Client"},
        ]}))
    r = client.transcribe(str(p), prettify=False, diarize=True, speaker_names=["Agent", "Client"])
    call = session.last
    assert call["files"]["file"][0] == "call.mp3"
    assert call["files"]["file"][1] == b"ID3abc"
    assert call["files"]["file"][2] == "audio/mpeg"
    assert call["data"] == {"prettify": "false", "response_format": "diarized_json",
                            "known_speaker_names[]": ["Agent", "Client"]}
    assert len(r.segments) == 2
    assert r.segments[0].speaker == "Agent" and r.segments[0].start == 1.128 and r.segments[1].end == 11.448
    assert r.task == "transcribe"
    assert str(r) == r.text


def test_transcribe_file_object_and_language(client, session):
    session.push(FakeResponse(200, json_body={"text": "ok"}))
    f = io.BytesIO(b"OggS")
    f.name = "/tmp/x/rec.ogg"
    client.transcribe(f, language="ru")
    call = session.last
    assert call["files"]["file"][0] == "rec.ogg"
    assert call["data"]["language"] == "ru"


def test_transcribe_rejects_text_mode(client):
    with pytest.raises(TypeError):
        client.transcribe(io.StringIO("abc"))


# ---------------------------------------------------------------- синтез

def test_speak_json_body(client, session):
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    out = client.speak("Сәлем <pause> қош келдіңіз", voice="Tomiris", language="kk", emotion="happy")
    assert out == WAV
    call = session.last
    assert call["url"] == "https://api.freedomspeech.kz/v1/audio/speech"
    assert call["json"] == {"input": "Сәлем <pause> қош келдіңіз", "voice": "Tomiris", "language": "kk", "emotion": "happy"}
    assert call["files"] is None and call["data"] is None
    assert call["timeout"] == 60.0


def test_speak_optional_fields_not_sent(client, session):
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    client.speak("Сәлем")
    assert session.last["json"] == {"input": "Сәлем", "voice": "tomiris"}


def test_speak_model_sent_only_when_given(client, session):
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    client.speak("Сәлем", model="freedomspeech")
    assert session.last["json"]["model"] == "freedomspeech"


def test_speak_empty_text(client):
    with pytest.raises(ValueError):
        client.speak("   ")


# ---------------------------------------------------------------- голоса и клон

def test_voice_preview(client, session):
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    assert client.voice_preview("alikhan_kz") == WAV
    assert session.last["url"].endswith("/api/voices/alikhan_kz/preview")
    assert "X-API-Key" not in session.last["headers"]


def test_clone_voice_reference(client, session, tmp_path):
    ref = tmp_path / "me.wav"
    ref.write_bytes(WAV)
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    out = client.clone_voice("Сәлеметсіз бе", reference_audio=str(ref), language="kk", speed=1.2)
    assert out == WAV
    call = session.last
    assert call["url"] == "https://api.freedomspeech.kz/api/voice-clone"
    assert call["params"] is None
    assert call["data"] == {"text": "Сәлеметсіз бе", "language": "kk", "speed": "1.2"}
    assert call["files"]["audio"][0] == "me.wav"
    assert call["files"]["audio"][1] == WAV
    assert call["timeout"] == 180.0


def test_clone_voice_catalog_id(client, session):
    session.push(FakeResponse(200, content=WAV, content_type="audio/wav"))
    client.clone_voice("Жаңа маусым", voice_id="alikhan_kz", duration=3.5)
    call = session.last
    assert call["files"] is None
    assert call["data"] == {"text": "Жаңа маусым", "voice_id": "alikhan_kz", "duration": "3.5"}


def test_clone_voice_requires_exactly_one_source(client):
    with pytest.raises(ValueError):
        client.clone_voice("text")
    with pytest.raises(ValueError):
        client.clone_voice("text", reference_audio=b"x", voice_id="tomiris")


def test_clone_voice_variants(client, session):
    b64 = base64.b64encode(WAV).decode()
    session.push(FakeResponse(200, json_body={"variants": [
        {"engine": "omnivoice", "wav_base64": b64}, {"engine": "flash", "wav_base64": b64}]}))
    variants = client.clone_voice_variants("Сәлем", reference_audio=b"RIFF", filename="ref.wav")
    assert session.last["params"] == {"variants": "all"}
    assert [v.engine for v in variants] == ["omnivoice", "flash"]
    assert variants[0].wav == WAV


def test_design_voice(client, session):
    b64 = base64.b64encode(WAV).decode()
    session.push(FakeResponse(200, json_body={
        "reference_wav_base64": b64, "voice_clone_wav_base64": b64,
        "variants": [{"engine": "flash", "wav_base64": b64}]}))
    d = client.design_voice("Сәлеметсіз бе", "A calm male voice", language="kk")
    assert session.last["url"].endswith("/api/voice-design")
    assert session.last["json"] == {"text": "Сәлеметсіз бе", "instruct": "A calm male voice", "language": "kk"}
    assert d.reference_wav == WAV and d.wav == WAV
    assert d.variants[0].engine == "flash"
    with pytest.raises(ValueError):
        client.design_voice("text", "")


# ---------------------------------------------------------------- текст и сервис

def test_translate(client, session):
    session.push(FakeResponse(200, json_body={"translations": [
        {"detected_source_language": "ru", "text": "Сәлем дүние"}]}))
    out = client.translate("Привет мир", "kk")
    assert session.last["json"] == {"text": "Привет мир", "target_lang": "kk"}
    assert out[0].text == "Сәлем дүние" and out[0].detected_source_language == "ru"

    session.push(FakeResponse(200, json_body={"translations": [{"text": "Сәлем"}, {"text": "Сау бол"}]}))
    out = client.translate(["Привет", "Пока"], "kk", source_lang="ru")
    assert session.last["json"] == {"text": ["Привет", "Пока"], "target_lang": "kk", "source_lang": "ru"}
    assert [t.text for t in out] == ["Сәлем", "Сау бол"]


def test_detect_and_languages_and_health(client, session):
    session.push(FakeResponse(200, json_body={"detections": [
        {"detected_source_language": "kk", "text": "Сәлем"}, {"detected_source_language": "en", "text": "Hello"}]}))
    d = client.detect_language(["Сәлем", "Hello"])
    assert [x.language for x in d] == ["kk", "en"]

    session.push(FakeResponse(200, json_body={"source_languages": [{"language": "kk", "name": "Kazakh"}],
                                              "target_languages": []}))
    assert client.languages()["source_languages"][0]["language"] == "kk"
    assert "X-API-Key" not in session.last["headers"]

    session.push(FakeResponse(200, json_body={"status": "healthy", "vad_pool_ready": True}))
    assert client.health()["status"] == "healthy"

    session.push(FakeResponse(503, json_body={"status": "unavailable", "model_loaded": False}))
    with pytest.raises(FreedomSpeechError) as ei:
        client.health()
    assert ei.value.status == 503 and ei.value.body["status"] == "unavailable"


def test_context_manager_closes_own_session_only(session):
    with FreedomSpeech(api_key="k", session=session):
        pass
    assert session.closed is False
    fs = FreedomSpeech(api_key="k")
    fs.close()
