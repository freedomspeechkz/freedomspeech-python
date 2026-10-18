"""Клиент API Freedom Speech. / Freedom Speech API client.

Все вызовы соответствуют публичной документации https://freedomspeech.kz/docs:

    POST /v1/audio/transcriptions   transcribe()
    WS   /ws/asr                    stream_transcribe(), asr_stream()
    POST /v1/audio/speech           speak()
    WS   /ws/tts                    stream_speech(), tts_stream()
    GET  /api/voices                voices()
    GET  /api/voices/{id}/preview   voice_preview()
    POST /api/voice-clone           clone_voice(), clone_voice_variants()
    POST /api/voice-design          design_voice()
    POST /v2/translate              translate()
    POST /v2/detect                 detect_language()
    GET  /v2/languages              languages()
    GET  /api/asr/health            health()
"""
from __future__ import annotations

import io
import mimetypes
import os
from base64 import b64decode
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Union
from urllib.parse import urlencode

import requests

from ._version import __version__
from .audio import ASR_SAMPLE_RATE
from .errors import AuthenticationError, ConnectionFailed, FreedomSpeechError, error_from_response
from .models import (CloneVariant, Detection, Transcription, TranscriptEvent, Translation, Voice,
                     VoiceDesign)
from .streaming import ASRStream, TTSStream, WSConnect, default_ws_connect, open_ws, transcribe_iter

DEFAULT_BASE_URL = "https://api.freedomspeech.kz"
ENV_API_KEY = "FS_API_KEY"
ENV_BASE_URL = "FS_BASE_URL"
DEFAULT_TIMEOUT = 60.0

# Без User-Agent или с UA стандартных библиотек Cloudflare перед API отвечал 403 (код 1010)
# в замерах; строка ниже повторяет форму, с которой рабочий клиент проходил.
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) freedomspeech-python/%s" % __version__

AudioInput = Union[str, bytes, bytearray, "os.PathLike[str]", io.IOBase]

# Публичные эндпоинты без ключа по документации.
_PUBLIC_PATHS = ("/api/voices", "/v2/languages", "/api/asr/health")


class FreedomSpeech:
    """Клиент API Freedom Speech: распознавание, синтез, клон голоса, перевод.

    Freedom Speech API client: speech recognition, synthesis, voice cloning, translation.

    Args:
        api_key: ключ API. Если не задан, берётся из переменной окружения ``FS_API_KEY``.
            Для публичных вызовов (``voices``, ``languages``, ``health``) ключ не нужен.
        base_url: адрес API, по умолчанию ``https://api.freedomspeech.kz``
            (или переменная окружения ``FS_BASE_URL``). Ключ из кабинета
            console.freedomspeech.kz работает на этом адресе: распознавание, синтез и оба
            WebSocket. Клонирование, voice design, перевод, каталог голосов и health на нём
            пока не опубликованы (бэкенд в работе), ключам кабинета они недоступны;
            для другого ключа и адреса передайте свой ``base_url``.
        timeout: таймаут HTTP-запроса и ожидания кадра по WebSocket, секунды.
        bearer_auth: дополнительно слать ``Authorization: Bearer <ключ>``. Документация
            описывает только ``X-API-Key``; флаг для прокси и будущих версий, по умолчанию выключен.
        user_agent: заголовок User-Agent.
        session: своя ``requests.Session`` (например, с ретраями или прокси).

    Пример / example::

        fs = FreedomSpeech()                      # ключ из FS_API_KEY
        print(fs.transcribe("call.mp3").text)
        open("hi.wav", "wb").write(fs.speak("Сәлем", voice="tomiris"))
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT, *, bearer_auth: bool = False,
                 user_agent: str = DEFAULT_USER_AGENT, session: Optional[requests.Session] = None,
                 ws_connect: Optional[WSConnect] = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get(ENV_API_KEY)
        if self.api_key is not None:
            self.api_key = self.api_key.strip() or None
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.bearer_auth = bearer_auth
        self.user_agent = user_agent
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._ws_connect = ws_connect

    # ------------------------------------------------------------------ служебное

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> "FreedomSpeech":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def ws_base_url(self) -> str:
        """``wss://`` адрес для потоковых эндпоинтов. / WebSocket base URL."""
        if self.base_url.startswith("https://"):
            return "wss://" + self.base_url[len("https://"):]
        if self.base_url.startswith("http://"):
            return "ws://" + self.base_url[len("http://"):]
        return self.base_url

    def _require_key(self) -> str:
        if not self.api_key:
            raise AuthenticationError(
                "Нет ключа API: передайте api_key или задайте переменную окружения FS_API_KEY "
                "(no API key: pass api_key or set FS_API_KEY)")
        return self.api_key

    def _headers(self, auth: bool = True) -> Dict[str, str]:
        h = {"User-Agent": self.user_agent, "Accept": "*/*"}
        if auth and self.api_key:
            h["X-API-Key"] = self.api_key
            if self.bearer_auth:
                h["Authorization"] = "Bearer " + self.api_key
        return h

    def _request(self, method: str, path: str, *, auth: bool = True, params: Optional[Dict[str, Any]] = None,
                 json_body: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None,
                 files: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> requests.Response:
        if auth and path not in _PUBLIC_PATHS:
            self._require_key()
        url = self.base_url + path
        try:
            resp = self._session.request(method, url, headers=self._headers(auth), params=params,
                                         json=json_body, data=data, files=files,
                                         timeout=timeout or self.timeout)
        except requests.RequestException as exc:
            raise ConnectionFailed("Сетевая ошибка (network error) %s %s: %s" % (method, path, exc)) from exc
        if resp.status_code >= 400:
            raise error_from_response(resp.status_code, _body_of(resp))
        return resp

    def _json(self, resp: requests.Response) -> Any:
        try:
            return resp.json()
        except ValueError as exc:
            raise FreedomSpeechError("Сервер вернул не JSON (non-JSON response): %s" % resp.text[:200],
                                     status=resp.status_code, body=resp.content) from exc

    def _ws_url(self, path: str, **query: Any) -> str:
        q = {"apiKey": self._require_key()}
        q.update({k: v for k, v in query.items() if v is not None})
        return "%s%s?%s" % (self.ws_base_url, path, urlencode(q))

    def _connect(self, path: str, **query: Any) -> Any:
        connect = self._ws_connect or default_ws_connect()
        return open_ws(connect, self._ws_url(path, **query), {"User-Agent": self.user_agent}, self.timeout)

    # ------------------------------------------------------------------ распознавание

    def transcribe(self, audio: AudioInput, *, language: Optional[str] = None, prettify: bool = True,
                   diarize: bool = False, speaker_names: Optional[Sequence[str]] = None,
                   filename: Optional[str] = None, model: Optional[str] = None,
                   timeout: Optional[float] = None) -> Transcription:
        """Распознать файл целиком: ``POST /v1/audio/transcriptions``. / Transcribe an audio file.

        Args:
            audio: путь к файлу, байты или открытый файл. Форматы: wav, mp3, ogg, oga, m4a, flac,
                webm, mp4, opus. Язык определяется автоматически.
            language: подсказка языка (``kk``, ``ru``, ``en``). В публичной документации поля нет,
                отправляется только если задано; эффект не проверен.
            prettify: ``True`` (по умолчанию) правит именованные сущности, числа и даты;
                ``False`` даёт сырой выход, для замера WER.
            diarize: ``True`` запрашивает ``response_format=diarized_json`` с отрезками по говорящим.
            speaker_names: подписи говорящих для диаризации (``known_speaker_names[]``):
                для двух каналов первый на канал 0, второй на канал 1; для моно до 4 имён
                по порядку появления.
            filename: имя файла в multipart, если ``audio`` передан байтами.
            model: поле ``model`` (OpenAI-совместимость), по умолчанию не отправляется.

        Returns:
            ``Transcription`` с ``text``, ``duration``, ``processing_time``, ``language``, ``segments``.
        """
        name, blob = _read_audio(audio, filename)
        fields: Dict[str, Any] = {"prettify": "true" if prettify else "false"}
        if diarize:
            fields["response_format"] = "diarized_json"
            if speaker_names:
                fields["known_speaker_names[]"] = list(speaker_names)
        if language:
            fields["language"] = language
        if model:
            fields["model"] = model
        files = {"file": (name, blob, _mime(name))}
        resp = self._request("POST", "/v1/audio/transcriptions", data=fields, files=files, timeout=timeout)
        data = self._json(resp)
        if not isinstance(data, dict):
            raise FreedomSpeechError("Неожиданный ответ (unexpected response): %r" % (data,), status=resp.status_code, body=data)
        return Transcription.from_json(data)

    def asr_stream(self, *, prettify: bool = True) -> ASRStream:
        """Открыть ``WS /ws/asr`` и дождаться ``ready``. / Open a streaming-recognition socket.

        Вход: сырой PCM16, 16 кГц, моно, без контейнера. Сервер не пересэмплирует и не распаковывает
        контейнеры; неверный формат даёт пустой или бессмысленный текст без ошибки.
        """
        ws = self._connect("/ws/asr", prettify=None if prettify else "false")
        stream = ASRStream(ws, timeout=self.timeout)
        try:
            stream.wait_ready()
        except Exception:
            stream.close()
            raise
        return stream

    def stream_transcribe(self, pcm16_chunks: Iterable[bytes], *, prettify: bool = True,
                          realtime: bool = False, stop: bool = True,
                          final_timeout: float = 5.0) -> Iterator[TranscriptEvent]:
        """Потоковое распознавание по ``WS /ws/asr``: генератор ``partial``/``final``.

        Stream PCM16 chunks and yield partial/final events.

        Args:
            pcm16_chunks: итератор кадров PCM16 (16 кГц, моно, little-endian, без заголовка),
                например ``freedomspeech.pcm16_frames(pcm)`` или кадры с микрофона.
            prettify: как в ``transcribe``.
            realtime: выдерживать темп живой речи (пауза по длительности кадра).
            stop: после последнего кадра отправить ``{"type": "stop"}``, чтобы получить ``final``.
            final_timeout: сколько секунд ждать ``final`` после конца аудио.

        Длинную речь сервер режет на отрезки по паузам и шлёт ``final`` на каждый:
        итоговый текст собирается из всех ``final``, не из последнего.
        """
        stream = self.asr_stream(prettify=prettify)
        try:
            for ev in transcribe_iter(stream, pcm16_chunks, realtime=realtime, stop=stop,
                                      final_timeout=final_timeout, sample_rate=ASR_SAMPLE_RATE):
                yield ev
        finally:
            stream.close()

    # ------------------------------------------------------------------ синтез

    def speak(self, text: str, voice: str = "tomiris", *, language: Optional[str] = None,
              emotion: Optional[str] = None, model: Optional[str] = None,
              response_format: Optional[str] = None, timeout: Optional[float] = None) -> bytes:
        """Озвучить текст: ``POST /v1/audio/speech``, в ответ WAV байтами. / Synthesize to WAV bytes.

        Args:
            text: текст (поле ``input``). Допустимы теги просодии ``<pause>``,
                ``<louder>...</louder>``, ``<slower>...</slower>``; у голоса tomiris ещё
                неречевые ``<sigh>``, ``<laugh>`` и другие.
            voice: ``tomiris``, ``taymas`` или id из ``voices()``; регистр не важен.
            language: подсказка языка ``kk``, ``ru``, ``en`` (пропускает автоопределение).
            emotion: только для tomiris: ``neutral``, ``happy``, ``sad``, ``surprised``,
                ``angry``, ``fearful``; у других голосов игнорируется.
            model: поле ``model``. В документации HTTP-эндпоинта его нет, а замер 07.10.2026
                показал, что HTTP всегда отвечает моделью ``freedomspeech``; ``freedomspeech-flash``
                доступна по ``stream_speech``. Отправляется только если задано.
            response_format: не описан в документации, ответ всегда ``audio/wav`` 16 бит моно 24 кГц;
                отправляется только если задано. Не проверено.

        Returns:
            WAV-файл байтами.
        """
        if not text or not text.strip():
            raise ValueError("text is required")
        body: Dict[str, Any] = {"input": text, "voice": voice}
        if language:
            body["language"] = language
        if emotion:
            body["emotion"] = emotion
        if model:
            body["model"] = model
        if response_format:
            body["response_format"] = response_format
        resp = self._request("POST", "/v1/audio/speech", json_body=body, timeout=timeout)
        return resp.content

    def tts_stream(self) -> TTSStream:
        """Открыть ``WS /ws/tts`` для нескольких запросов на одном тёплом соединении. / Open a TTS socket."""
        ws = self._connect("/ws/tts")
        return TTSStream(ws, timeout=self.timeout)

    def stream_speech(self, text: str, voice: str = "tomiris", *, model: str = "freedomspeech-flash",
                      language: Optional[str] = None, emotion: Optional[str] = None,
                      request_id: Optional[str] = None) -> Iterator[bytes]:
        """Потоковый синтез по ``WS /ws/tts``: генератор кусков PCM16 (24 кГц, моно).

        Stream synthesis: yields raw PCM16 chunks as the server generates them.

        Args:
            text: текст; теги просодии допустимы.
            voice: как в ``speak``.
            model: ``freedomspeech-flash`` (по умолчанию здесь, звук идёт по мере синтеза) или
                ``freedomspeech`` (синтезирует фразу целиком и отдаёт её кусками).
            language, emotion: как в ``speak``.
            request_id: свой ``requestId``; иначе генерируется.

        Собрать WAV: ``pcm16_to_wav(b"".join(chunks))``. Для многих фраз подряд откройте одно
        соединение через ``tts_stream()``: первый запрос на новом сокете заметно медленнее.
        """
        stream = self.tts_stream()
        try:
            for chunk in stream.synthesize(text, voice, model=model, language=language,
                                           emotion=emotion, request_id=request_id):
                yield chunk
        finally:
            stream.close()

    # ------------------------------------------------------------------ голоса

    def voices(self) -> List[Voice]:
        """Список голосов: ``GET /api/voices``, без ключа. / List catalog voices.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.
        """
        data = self._json(self._request("GET", "/api/voices", auth=False))
        if isinstance(data, dict):
            data = data.get("voices") or data.get("data") or []
        return [Voice.from_json(v) for v in data if isinstance(v, dict)]

    def voice_preview(self, voice_id: str) -> bytes:
        """Готовый образец голоса каталога: ``GET /api/voices/{id}/preview``, WAV байтами.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.

        У встроенных голосов образца нет (404): озвучьте фразу через ``speak``.
        """
        resp = self._request("GET", "/api/voices/%s/preview" % voice_id, auth=False)
        return resp.content

    def clone_voice(self, text: str, *, reference_audio: Optional[AudioInput] = None,
                    voice_id: Optional[str] = None, language: Optional[str] = None,
                    speed: Optional[float] = None, duration: Optional[float] = None,
                    filename: Optional[str] = None, timeout: Optional[float] = None) -> bytes:
        """Озвучить текст голосом из записи или голосом каталога: ``POST /api/voice-clone``.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.

        Clone a voice from a reference recording (or catalog voice) and speak the text; returns WAV.

        Args:
            text: что сказать.
            reference_audio: запись-референс (WAV, MP3, OGG, M4A, FLAC, WebM), рекомендуется до 20 с.
                Не-WAV сервер конвертирует в 16 кГц моно сам.
            voice_id: вместо записи: id голоса каталога или ``tomiris``.
            language: подсказка языка ``kk``, ``ru``, ``en``.
            speed: множитель темпа, по умолчанию 1.0; больше 1 быстрее, меньше 1 медленнее
                (основной движок).
            duration: целевая длительность в секундах; при заданной ``duration`` ``speed`` игнорируется.
            filename: имя файла в multipart, если запись передана байтами.

        Нужно ровно одно из ``reference_audio`` и ``voice_id``.
        """
        fields, files = self._clone_form(text, reference_audio, voice_id, language, speed, duration, filename)
        resp = self._request("POST", "/api/voice-clone", data=fields, files=files,
                             timeout=timeout or max(self.timeout, 180.0))
        return resp.content

    def clone_voice_variants(self, text: str, *, reference_audio: Optional[AudioInput] = None,
                             voice_id: Optional[str] = None, language: Optional[str] = None,
                             speed: Optional[float] = None, duration: Optional[float] = None,
                             filename: Optional[str] = None,
                             timeout: Optional[float] = None) -> List[CloneVariant]:
        """То же, что ``clone_voice``, но ``?variants=all``: результат каждого движка.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.

        Same as ``clone_voice`` with ``?variants=all``: one WAV per engine (``omnivoice``, ``flash``).
        Упавший движок в списке отсутствует; 502 приходит, только если упали все.
        """
        fields, files = self._clone_form(text, reference_audio, voice_id, language, speed, duration, filename)
        resp = self._request("POST", "/api/voice-clone", params={"variants": "all"}, data=fields,
                             files=files, timeout=timeout or max(self.timeout, 180.0))
        data = self._json(resp)
        return _variants(data)

    def design_voice(self, text: str, instruct: str, *, language: Optional[str] = None,
                     timeout: Optional[float] = None) -> VoiceDesign:
        """Создать голос по описанию и озвучить им текст: ``POST /api/voice-design``.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.

        Design a voice from a text description and speak the text with it.

        Args:
            text: что сказать.
            instruct: описание голоса. По документации лучше работает по-английски; казахское
                описание сервер переводит сам. Стоит указать пол, возраст, высоту, темп, эмоцию,
                тембр, сценарий.
            language: подсказка языка текста.

        Returns:
            ``VoiceDesign``: ``reference_wav`` (образец голоса, годится как референс для
            ``clone_voice``), ``variants`` по движкам, ``wav`` первого удачного варианта.
        """
        if not text or not text.strip():
            raise ValueError("text is required")
        if not instruct or not instruct.strip():
            raise ValueError("instruct is required")
        body: Dict[str, Any] = {"text": text, "instruct": instruct}
        if language:
            body["language"] = language
        resp = self._request("POST", "/api/voice-design", json_body=body,
                             timeout=timeout or max(self.timeout, 180.0))
        data = self._json(resp)
        if not isinstance(data, dict):
            raise FreedomSpeechError("Неожиданный ответ (unexpected response)", status=resp.status_code, body=data)
        ref = data.get("reference_wav_base64")
        first = data.get("voice_clone_wav_base64")
        return VoiceDesign(reference_wav=b64decode(ref) if isinstance(ref, str) else b"",
                           wav=b64decode(first) if isinstance(first, str) else None,
                           variants=_variants(data), raw=data)

    # ------------------------------------------------------------------ текст

    def translate(self, text: Union[str, Sequence[str]], target_lang: str, *,
                  source_lang: Optional[str] = None) -> List[Translation]:
        """Перевести строки между kk, ru, en: ``POST /v2/translate``. / Translate up to 50 strings.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.

        Порядок и число результатов совпадают с входом. ``detected_source_language`` заполнено,
        только если ``source_lang`` не передан.
        """
        body: Dict[str, Any] = {"text": text if isinstance(text, str) else list(text), "target_lang": target_lang}
        if source_lang:
            body["source_lang"] = source_lang
        data = self._json(self._request("POST", "/v2/translate", json_body=body))
        items = data.get("translations", []) if isinstance(data, dict) else []
        return [Translation.from_json(t) for t in items if isinstance(t, dict)]

    def detect_language(self, text: Union[str, Sequence[str]]) -> List[Detection]:
        """Определить язык строк: ``POST /v2/detect``. / Detect the language of up to 50 strings.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.
        """
        body = {"text": text if isinstance(text, str) else list(text)}
        data = self._json(self._request("POST", "/v2/detect", json_body=body))
        items = data.get("detections", []) if isinstance(data, dict) else []
        return [Detection.from_json(d) for d in items if isinstance(d, dict)]

    def languages(self) -> Dict[str, Any]:
        """Языки перевода: ``GET /v2/languages``, без ключа. / Supported translation languages.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.
        """
        return self._json(self._request("GET", "/v2/languages", auth=False))

    # ------------------------------------------------------------------ сервис

    def health(self) -> Dict[str, Any]:
        """Состояние распознавания: ``GET /api/asr/health``, без ключа. / ASR service health.

        На адресе по умолчанию ``https://api.freedomspeech.kz`` маршрут пока не опубликован (404),
        ключам кабинета недоступен; нужен свой ``base_url``.

        При 503 поднимает ``FreedomSpeechError`` с телом ``{"status": "unavailable", ...}``.
        """
        return self._json(self._request("GET", "/api/asr/health", auth=False))

    # ------------------------------------------------------------------ внутреннее

    def _clone_form(self, text: str, reference_audio: Optional[AudioInput], voice_id: Optional[str],
                    language: Optional[str], speed: Optional[float], duration: Optional[float],
                    filename: Optional[str]):
        if not text or not text.strip():
            raise ValueError("text is required")
        if (reference_audio is None) == (voice_id is None):
            raise ValueError("pass exactly one of reference_audio or voice_id")
        fields: Dict[str, Any] = {"text": text}
        files: Optional[Dict[str, Any]] = None
        if reference_audio is not None:
            name, blob = _read_audio(reference_audio, filename, default="reference.wav")
            files = {"audio": (name, blob, _mime(name))}
        else:
            fields["voice_id"] = voice_id
        if language:
            fields["language"] = language
        if speed is not None:
            fields["speed"] = str(speed)
        if duration is not None:
            fields["duration"] = str(duration)
        return fields, files


# ---------------------------------------------------------------------- помощники


def _body_of(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.content


def _mime(name: str) -> str:
    guess, _ = mimetypes.guess_type(name)
    return guess or "application/octet-stream"


def _read_audio(audio: AudioInput, filename: Optional[str], default: str = "audio.wav"):
    """(имя файла, байты) из пути, байтов или файлового объекта."""
    if isinstance(audio, (bytes, bytearray)):
        return filename or default, bytes(audio)
    if isinstance(audio, (str, os.PathLike)):
        path = os.fspath(audio)
        with open(path, "rb") as f:
            return filename or os.path.basename(path), f.read()
    if hasattr(audio, "read"):
        blob = audio.read()
        if isinstance(blob, str):
            raise TypeError("audio file must be opened in binary mode")
        name = filename or os.path.basename(getattr(audio, "name", "") or "") or default
        return name, blob
    raise TypeError("audio must be a path, bytes or a binary file object, got %s" % type(audio).__name__)


def _variants(data: Any) -> List[CloneVariant]:
    out: List[CloneVariant] = []
    if isinstance(data, dict):
        for v in data.get("variants") or []:
            if isinstance(v, dict) and isinstance(v.get("wav_base64"), str):
                out.append(CloneVariant(engine=str(v.get("engine", "")), wav=b64decode(v["wav_base64"])))
    return out


__all__ = ["FreedomSpeech", "DEFAULT_BASE_URL", "ENV_API_KEY"]
