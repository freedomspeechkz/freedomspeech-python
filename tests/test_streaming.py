"""WebSocket-потоки с поддельным сокетом. / Streaming ASR and TTS against a fake socket."""
from __future__ import annotations

import base64
import time

import pytest

from freedomspeech import ConnectionFailed, FreedomSpeech, StreamError, pcm16_frames
from freedomspeech.audio import ASR_SAMPLE_RATE, TTS_SAMPLE_RATE, pcm16_to_wav, silence, wav_to_pcm16

from conftest import FakeWS, make_ws_connect


class Closed(Exception):
    """Имитация websockets.exceptions.ConnectionClosedOK."""


Closed.__name__ = "ConnectionClosedOK"


def _client(ws: FakeWS) -> FreedomSpeech:
    return FreedomSpeech(api_key="k", ws_connect=make_ws_connect(ws))


# ---------------------------------------------------------------- ASR

def _after_stop(ws):
    """Кадр, который сервер присылает только после {"type": "stop"}."""
    deadline = time.monotonic() + 5
    while not any(isinstance(x, str) and "stop" in x for x in ws.sent):
        if time.monotonic() > deadline:
            raise AssertionError("stop was not sent")
        time.sleep(0.005)
    return {"type": "final", "text": "қалайсың", "language": "kk"}


def test_stream_transcribe_events_and_protocol():
    ws = FakeWS([
        {"type": "ready"},
        {"type": "partial", "text": "сәлем", "is_final": False},
        {"type": "final", "text": "сәлем әлем", "language": "kk"},
        _after_stop,
    ])
    fs = _client(ws)
    pcm = silence(300)  # 300 мс тишины = 9600 байт, 3 кадра по 100 мс
    t0 = time.monotonic()
    events = list(fs.stream_transcribe(pcm16_frames(pcm, frame_ms=100), prettify=False, realtime=True,
                                       final_timeout=5.0))
    assert time.monotonic() - t0 < 3.0  # закончили по final после stop, а не по final_timeout
    assert ws.url == "wss://freedomspeech.kz/ws/asr?apiKey=k&prettify=false"
    assert ws.headers["User-Agent"].startswith("Mozilla/5.0")
    assert [e.type for e in events] == ["partial", "final", "final"]
    assert events[0].is_final is False and events[1].is_final is True
    assert events[1].language == "kk"
    # отправлено ровно аудио + stop
    assert ws.sent_bytes() == pcm
    assert len([x for x in ws.sent if isinstance(x, bytes)]) == 3
    assert ws.sent_json() == [{"type": "stop"}]
    assert ws.closed


def test_stream_transcribe_default_prettify_url():
    ws = FakeWS([{"type": "ready"}, TimeoutError()])
    fs = _client(ws)
    list(fs.stream_transcribe([silence(100)], final_timeout=0.1))
    assert ws.url == "wss://freedomspeech.kz/ws/asr?apiKey=k"


def test_stream_transcribe_error_frame():
    ws = FakeWS([{"type": "ready"}, {"type": "error", "message": "upstream connection failed"}])
    fs = _client(ws)
    with pytest.raises(StreamError) as ei:
        list(fs.stream_transcribe([silence(100)], final_timeout=0.1))
    assert "upstream connection failed" in str(ei.value)
    assert ws.closed


def test_asr_stream_manual_use():
    ws = FakeWS([{"type": "ready"}, {"type": "partial", "text": "а"}, {"type": "final", "text": "абв", "language": "ru"}])
    fs = _client(ws)
    with fs.asr_stream() as s:
        assert s.ready
        s.send(silence(100))
        s.stop()
        got = list(s.poll(timeout=0.05))
    assert [g.text for g in got] == ["а", "абв"]
    assert ws.sent_json() == [{"type": "stop"}]
    assert ws.closed


def test_asr_stream_odd_frame_rejected():
    ws = FakeWS([{"type": "ready"}])
    s = _client(ws).asr_stream()
    with pytest.raises(ValueError):
        s.send(b"\x00\x00\x00")


def test_asr_stream_no_ready():
    ws = FakeWS([{"type": "error", "message": "bad key"}])
    with pytest.raises(StreamError):
        _client(ws).asr_stream()
    assert ws.closed


def test_asr_stream_closed_by_server():
    ws = FakeWS([{"type": "ready"}, Closed("1011")])
    s = _client(ws).asr_stream()
    with pytest.raises(ConnectionFailed):
        s.recv(timeout=1)


def test_connect_failure_wrapped():
    def connect(url, **kw):
        raise OSError("refused")
    fs = FreedomSpeech(api_key="k", ws_connect=connect)
    with pytest.raises(ConnectionFailed):
        fs.asr_stream()


# ---------------------------------------------------------------- TTS

def _chunk(rid: str, idx: int, pcm: bytes) -> dict:
    return {"type": "audio_chunk", "requestId": rid, "audio": base64.b64encode(pcm).decode(),
            "chunkIndex": idx, "sampleRate": 24000, "format": "int16"}


def test_stream_speech_collects_pcm():
    a, b = b"\x01\x00" * 100, b"\x02\x00" * 50
    ws = FakeWS([
        {"type": "text_chunk", "requestId": "r1", "index": 0},
        _chunk("r1", 0, a), _chunk("r1", 1, b),
        {"type": "audio_end", "requestId": "r1", "totalChunks": 2, "sampleRate": 24000},
    ])
    fs = _client(ws)
    chunks = list(fs.stream_speech("Сәлем", voice="tomiris", language="kk", request_id="r1"))
    assert chunks == [a, b]
    assert ws.url == "wss://freedomspeech.kz/ws/tts?apiKey=k"
    req = ws.sent_json()[0]
    assert req == {"type": "synthesize", "text": "Сәлем", "requestId": "r1", "voice": "tomiris",
                   "model": "freedomspeech-flash", "language": "kk"}
    assert ws.closed
    wav = pcm16_to_wav(b"".join(chunks), TTS_SAMPLE_RATE)
    assert wav[:4] == b"RIFF" and len(wav) == 44 + len(a) + len(b)


def test_tts_stream_multiple_requests_and_queue_error():
    ws = FakeWS([
        _chunk("x", 0, b"\x00\x01"), {"type": "audio_end", "requestId": "x", "totalChunks": 1},
        {"type": "error", "requestId": "y", "code": "queue_full", "message": "queue is full"},
    ])
    fs = _client(ws)
    with fs.tts_stream() as s:
        first = b"".join(s.synthesize("бір", "tomiris", model="freedomspeech", request_id="x"))
        assert first == b"\x00\x01" and s.last_total_chunks == 1
        with pytest.raises(StreamError) as ei:
            list(s.synthesize("екі", "tomiris", request_id="y"))
        assert ei.value.code == "queue_full"
        s.cancel()
        s.cancel("y")
    sent = ws.sent_json()
    assert sent[0]["model"] == "freedomspeech" and "emotion" not in sent[0]
    assert "model" not in sent[1]
    assert sent[2] == {"type": "cancel"} and sent[3] == {"type": "cancel", "requestId": "y"}
    assert ws.closed


def test_tts_stream_ignores_other_request_frames_and_cancelled():
    ws = FakeWS([
        {"type": "cancelled", "requestId": "old", "totalChunks": 7},
        _chunk("new", 0, b"\x07\x00"),
        {"type": "cancelled", "requestId": "new", "totalChunks": 1},
    ])
    s = _client(ws).tts_stream()
    out = list(s.synthesize("текст", request_id="new"))
    assert out == [b"\x07\x00"]
    assert s.last_total_chunks == 1


def test_tts_stream_timeout_is_connection_failed():
    ws = FakeWS([])
    s = _client(ws).tts_stream()
    with pytest.raises(ConnectionFailed):
        list(s.synthesize("текст"))


def test_tts_synthesize_empty_text():
    s = _client(FakeWS([])).tts_stream()
    with pytest.raises(ValueError):
        list(s.synthesize(""))


# ---------------------------------------------------------------- аудио-помощники

def test_audio_helpers_roundtrip():
    pcm = bytes(range(256)) * 10
    wav = pcm16_to_wav(pcm, ASR_SAMPLE_RATE)
    assert wav_to_pcm16(wav) == pcm
    frames = list(pcm16_frames(pcm, frame_ms=10))
    assert all(len(f) % 2 == 0 for f in frames)
    assert b"".join(frames) == pcm
    assert len(frames[0]) == 320
    with pytest.raises(ValueError):
        wav_to_pcm16(pcm16_to_wav(pcm, 24000))  # не та частота для /ws/asr
    assert len(silence(100)) == 3200
