"""Помощники для звука: PCM16 <-> WAV, нарезка на кадры. / Audio helpers: PCM16 and WAV.

Форматы по документации API:
    выход синтеза: PCM16, моно, 24 000 Гц (HTTP отдаёт WAV, WebSocket отдаёт сырой PCM);
    вход потокового распознавания: PCM16 little-endian, моно, 16 000 Гц, без контейнера.
"""
from __future__ import annotations

import io
import wave
from typing import Iterator

TTS_SAMPLE_RATE = 24000
ASR_SAMPLE_RATE = 16000


def pcm16_to_wav(pcm: bytes, sample_rate: int = TTS_SAMPLE_RATE, channels: int = 1) -> bytes:
    """Обернуть сырой PCM16 в WAV (44-байтный RIFF-заголовок). / Wrap raw PCM16 into a WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm16(data: bytes, expect_rate: int = ASR_SAMPLE_RATE) -> bytes:
    """Достать PCM16 из WAV. / Extract PCM16 samples from a WAV file.

    Требует 16-битный моно WAV с частотой ``expect_rate`` (по умолчанию 16 кГц, как ждёт ``/ws/asr``).
    Сервер не пересэмплирует, поэтому другой формат здесь отклоняется, а не пропускается молча.
    Конвертация: ``ffmpeg -i in.mp3 -ar 16000 -ac 1 -f s16le out.pcm``.
    """
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("WAV must be 16-bit PCM, got %d bytes per sample" % w.getsampwidth())
        if w.getnchannels() != 1:
            raise ValueError("WAV must be mono, got %d channels" % w.getnchannels())
        if expect_rate and w.getframerate() != expect_rate:
            raise ValueError("WAV must be %d Hz, got %d Hz" % (expect_rate, w.getframerate()))
        return w.readframes(w.getnframes())


def wav_duration(data: bytes) -> float:
    """Длительность WAV в секундах. / WAV duration in seconds."""
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def pcm16_duration(pcm: bytes, sample_rate: int = ASR_SAMPLE_RATE, channels: int = 1) -> float:
    """Длительность сырого PCM16 в секундах. / Raw PCM16 duration in seconds."""
    return len(pcm) / float(2 * channels * sample_rate)


def pcm16_frames(pcm: bytes, frame_ms: int = 100, sample_rate: int = ASR_SAMPLE_RATE) -> Iterator[bytes]:
    """Нарезать PCM16 на кадры по ``frame_ms`` мс (по умолчанию 100 мс = 3200 байт при 16 кГц).

    Каждый кадр содержит целое число сэмплов, как требует ``/ws/asr``.
    """
    step = int(sample_rate * frame_ms / 1000) * 2
    if step <= 0:
        raise ValueError("frame_ms too small")
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        if len(chunk) % 2:
            chunk = chunk[:-1]
        if chunk:
            yield chunk


def silence(ms: int, sample_rate: int = ASR_SAMPLE_RATE) -> bytes:
    """Тишина заданной длины в PCM16. / PCM16 silence of the given length."""
    return b"\x00" * (int(sample_rate * ms / 1000) * 2)


def is_wav(data: bytes) -> bool:
    """Начинается ли буфер с RIFF/WAVE-заголовка. / Whether the buffer looks like a WAV file."""
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"

