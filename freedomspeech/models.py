"""Структуры ответов API. / Response data classes.

Поля повторяют публичную документацию https://freedomspeech.kz/docs.
Исходный JSON всегда доступен в поле ``raw``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass
class Segment:
    """Отрезок стенограммы с таймкодами (``response_format=diarized_json``). / Transcript segment."""

    id: Optional[str]
    start: Optional[float]
    end: Optional[float]
    text: str
    speaker: Optional[str] = None
    type: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Segment":
        return cls(id=data.get("id"), start=_num(data.get("start")), end=_num(data.get("end")),
                   text=str(data.get("text", "")), speaker=data.get("speaker"),
                   type=data.get("type"), raw=data)


@dataclass
class Transcription:
    """Ответ ``POST /v1/audio/transcriptions``. / Transcription result.

    ``text``: стенограмма; ``duration``: длительность аудио, с; ``processing_time``: время
    обработки на сервере, с (сервер может не прислать); ``language``: определённый язык;
    ``segments``: отрезки по говорящим, только при ``diarize=True``.
    """

    text: str
    duration: Optional[float] = None
    processing_time: Optional[float] = None
    language: Optional[str] = None
    segments: List[Segment] = field(default_factory=list)
    task: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Transcription":
        segs = data.get("segments") or []
        return cls(text=str(data.get("text", "")), duration=_num(data.get("duration")),
                   processing_time=_num(data.get("processingTime")), language=data.get("language"),
                   segments=[Segment.from_json(s) for s in segs if isinstance(s, dict)],
                   task=data.get("task"), raw=data)

    def __str__(self) -> str:
        return self.text


@dataclass
class TranscriptEvent:
    """Событие потока ``WS /ws/asr``: ``partial`` или ``final``. / Streaming ASR event.

    ``is_final`` True для ``final``. ``language`` приходит только с ``final``.
    """

    type: str
    text: str
    language: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_final(self) -> bool:
        return self.type == "final"

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "TranscriptEvent":
        return cls(type=str(data.get("type", "")), text=str(data.get("text", "")),
                   language=data.get("language"), raw=data)


@dataclass
class Voice:
    """Голос из ``GET /api/voices``. / Catalog voice.

    ``id`` подставляется в ``voice`` (синтез) и ``voice_id`` (клонирование).
    ``type``: ``builtin`` или ``custom``.
    """

    id: str
    name: str
    type: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Voice":
        return cls(id=str(data.get("id", "")), name=str(data.get("name", data.get("id", ""))),
                   type=data.get("type"), raw=data)


@dataclass
class CloneVariant:
    """Результат одного движка при ``variants=all``. / One engine's result of voice cloning.

    ``engine``: ``omnivoice`` (модель freedomspeech) или ``flash`` (freedomspeech-flash).
    ``wav``: готовый WAV-файл байтами.
    """

    engine: str
    wav: bytes = field(repr=False)


@dataclass
class VoiceDesign:
    """Ответ ``POST /api/voice-design``. / Voice design result.

    ``reference_wav``: образец спроектированного голоса; его можно отдать как референс
    в ``clone_voice`` и озвучивать им новый текст. ``variants``: тот же текст, озвученный
    каждым движком. ``wav``: первый удачный вариант (поле для старых клиентов).
    """

    reference_wav: bytes = field(repr=False)
    wav: Optional[bytes] = field(default=None, repr=False)
    variants: List[CloneVariant] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Translation:
    """Одна строка ответа ``POST /v2/translate``. / One translated item."""

    text: str
    detected_source_language: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Translation":
        return cls(text=str(data.get("text", "")),
                   detected_source_language=data.get("detected_source_language"), raw=data)


@dataclass
class Detection:
    """Одна строка ответа ``POST /v2/detect``. / One language detection item."""

    text: str
    language: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Detection":
        return cls(text=str(data.get("text", "")),
                   language=data.get("detected_source_language"), raw=data)
