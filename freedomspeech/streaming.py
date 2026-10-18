"""Потоковые соединения: ``WS /ws/asr`` и ``WS /ws/tts``. / WebSocket streams for ASR and TTS.

Протоколы взяты из https://freedomspeech.kz/docs (разделы «WS: ASR streaming» и «WS: TTS streaming»)
и из рабочего клиентского кода замеров. Работа синхронная, через ``websockets.sync``.

ASR (``/ws/asr?apiKey=...&prettify=...``):
    клиент -> сервер: бинарные кадры PCM16 16 кГц моно; ``{"type": "stop"}`` закрывает текущую фразу;
    сервер -> клиент: ``{"type": "ready"}``, ``{"type": "partial", "text"}``,
    ``{"type": "final", "text", "language"}``, ``{"type": "error", "message"}``.
    Соединение закрывается сервером после 15 с без аудио.

TTS (``/ws/tts?apiKey=...``):
    клиент -> сервер: ``{"type": "synthesize", "text", "voice", "emotion", "language", "model", "requestId"}``,
    ``{"type": "cancel", "requestId"?}``;
    сервер -> клиент: ``audio_chunk{audio: base64 PCM16, chunkIndex, sampleRate, format}``,
    ``audio_end{totalChunks, sampleRate}``, ``cancelled{totalChunks}``, ``error{code, message}``.
    На одном соединении очередь до 8 запросов, ожидание до 60 с.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, Iterator, Optional

from .audio import ASR_SAMPLE_RATE, TTS_SAMPLE_RATE
from .errors import ConnectionFailed, FreedomSpeechError, StreamError
from .models import TranscriptEvent

WSConnect = Callable[..., Any]


def default_ws_connect() -> WSConnect:
    """Фабрика соединений ``websockets.sync.client.connect``. / Default WebSocket connector."""
    try:
        from websockets.sync.client import connect
    except ImportError as e:  # pragma: no cover
        raise ImportError("Для потоковых методов нужен пакет websockets>=12: pip install websockets") from e
    return connect


def _is_closed_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name.startswith("ConnectionClosed") or isinstance(exc, (ConnectionError, OSError))


def _recv_json(ws: Any, timeout: Optional[float]) -> Optional[Dict[str, Any]]:
    """Прочитать один JSON-кадр. None при таймауте. Бинарные кадры пропускаются."""
    while True:
        try:
            raw = ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        except Exception as exc:
            if _is_closed_error(exc):
                raise ConnectionFailed("Соединение закрыто сервером (connection closed): %s" % exc) from exc
            raise
        if isinstance(raw, (bytes, bytearray)):
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict):
            return msg


class ASRStream:
    """Открытое соединение ``WS /ws/asr``. / An open streaming-recognition connection.

    Пример / example::

        with client.asr_stream() as s:
            for frame in frames:          # PCM16, 16 кГц, моно, без заголовка
                s.send(frame)
                for ev in s.poll():
                    print(ev.type, ev.text)
            s.stop()
            for ev in s.poll(timeout=3):
                print(ev.type, ev.text)

    Соединение держите тёплым: первая фраза на новом сокете начинается с задержкой,
    следующие идут без прогрева. ``stop`` завершает фразу, сокет остаётся открытым.
    """

    def __init__(self, ws: Any, timeout: float = 60.0) -> None:
        self._ws = ws
        self.timeout = timeout
        self.ready = False
        self.closed = False

    def wait_ready(self, timeout: Optional[float] = None) -> None:
        """Дождаться ``{"type": "ready"}``; до него аудио слать нельзя. / Wait for the ready frame."""
        if self.ready:
            return
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ConnectionFailed("Сервер не прислал ready за %.0f с (no ready frame)" % self.timeout)
            msg = _recv_json(self._ws, left)
            if msg is None:
                continue
            t = msg.get("type")
            if t == "ready":
                self.ready = True
                return
            if t == "error":
                raise StreamError(str(msg.get("message", "error")), body=msg, code=msg.get("code"))

    def send(self, pcm16: bytes) -> None:
        """Отправить кадр PCM16 (16 кГц, моно, little-endian, чётное число байт). / Send a PCM16 frame."""
        if len(pcm16) % 2:
            raise ValueError("PCM16 frame must contain whole samples (even number of bytes)")
        self.wait_ready()
        try:
            self._ws.send(bytes(pcm16))
        except Exception as exc:
            if _is_closed_error(exc):
                raise ConnectionFailed("Соединение закрыто (connection closed): %s" % exc) from exc
            raise

    def stop(self) -> None:
        """Завершить текущую фразу: ``{"type": "stop"}``. Сокет остаётся открытым. / Force a final."""
        self.wait_ready()
        self._ws.send(json.dumps({"type": "stop"}))

    def recv(self, timeout: Optional[float] = None) -> Optional[TranscriptEvent]:
        """Одно событие ``partial``/``final`` или None по таймауту. / One event or None on timeout.

        Кадр ``error`` поднимает ``StreamError``.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            msg = _recv_json(self._ws, left if left is not None else self.timeout)
            if msg is None:
                if deadline is None:
                    continue
                return None
            t = msg.get("type")
            if t == "ready":
                self.ready = True
                continue
            if t in ("partial", "final"):
                return TranscriptEvent.from_json(msg)
            if t == "error":
                raise StreamError(str(msg.get("message", "error")), body=msg, code=msg.get("code"))
            # неизвестные типы пропускаем

    def poll(self, timeout: float = 0.0) -> Iterator[TranscriptEvent]:
        """Все события, накопившиеся за ``timeout`` секунд. / Drain events available within timeout."""
        deadline = time.monotonic() + timeout
        while True:
            left = max(0.0, deadline - time.monotonic())
            ev = self.recv(timeout=left)
            if ev is None:
                return
            yield ev

    def events(self) -> Iterator[TranscriptEvent]:
        """Бесконечный итератор событий до закрытия соединения. / Iterate events until the socket closes."""
        try:
            while True:
                ev = self.recv()
                if ev is not None:
                    yield ev
        except ConnectionFailed:
            return

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._ws.close()
            except Exception:
                pass

    def __enter__(self) -> "ASRStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def transcribe_iter(stream: ASRStream, pcm_chunks: Iterable[bytes], *, realtime: bool = False,
                    stop: bool = True, final_timeout: float = 5.0,
                    sample_rate: int = ASR_SAMPLE_RATE) -> Iterator[TranscriptEvent]:
    """Прогнать итератор PCM16 через открытый ``ASRStream``, отдавая события по мере прихода.

    Отправка идёт в фоновом потоке, чтение событий в вызывающем. После конца аудио
    отправляется ``stop`` (если ``stop=True``), затем события читаются ещё ``final_timeout`` секунд
    или до первого ``final``, пришедшего после ``stop``.
    ``realtime=True`` выдерживает темп живой речи (пауза по длительности каждого кадра).
    """
    stream.wait_ready()
    done = threading.Event()
    errors: list = []
    t_done = [0.0]

    def sender() -> None:
        try:
            for chunk in pcm_chunks:
                if stream.closed:
                    break
                stream.send(chunk)
                if realtime:
                    time.sleep(len(chunk) / float(2 * sample_rate))
            if stop and not stream.closed:
                stream.stop()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            t_done[0] = time.monotonic()
            done.set()

    th = threading.Thread(target=sender, name="freedomspeech-asr-sender", daemon=True)
    th.start()
    try:
        while True:
            if errors:
                raise errors[0]
            ev = stream.recv(timeout=0.2)
            if ev is not None:
                yield ev
                if done.is_set() and ev.is_final and time.monotonic() >= t_done[0]:
                    break
                continue
            if done.is_set() and time.monotonic() - t_done[0] > final_timeout:
                break
    finally:
        done.set()
        th.join(timeout=1.0)


class TTSStream:
    """Открытое соединение ``WS /ws/tts``. / An open streaming-synthesis connection.

    Пример / example::

        with client.tts_stream() as s:
            pcm = b"".join(s.synthesize("Сәлем", voice="tomiris", model="freedomspeech-flash"))

    Запросы на одном соединении выполняются по очереди (до 8 в ожидании, до 60 с ожидания).
    Звук приходит сырым PCM16 24 кГц моно; обернуть в WAV: ``freedomspeech.pcm16_to_wav``.
    """

    def __init__(self, ws: Any, timeout: float = 60.0) -> None:
        self._ws = ws
        self.timeout = timeout
        self.closed = False
        self.sample_rate = TTS_SAMPLE_RATE
        self.last_total_chunks: Optional[int] = None

    def synthesize(self, text: str, voice: Optional[str] = None, *, model: Optional[str] = None,
                   language: Optional[str] = None, emotion: Optional[str] = None,
                   request_id: Optional[str] = None) -> Iterator[bytes]:
        """Отправить ``synthesize`` и отдавать куски PCM16 до ``audio_end``. / Stream PCM16 chunks.

        ``model``: ``freedomspeech`` (по умолчанию на сервере) или ``freedomspeech-flash``.
        Неизвестное значение сервер заменяет на ``freedomspeech``. Кадр ``error`` поднимает
        ``StreamError`` с ``code`` (``queue_full``, ``queue_timeout``, ``upstream_failed`` и другие).
        Кадр ``cancelled`` завершает генератор без ошибки.
        """
        if not text or not text.strip():
            raise ValueError("text is required")
        rid = request_id or ("req-" + uuid.uuid4().hex[:12])
        req: Dict[str, Any] = {"type": "synthesize", "text": text, "requestId": rid}
        if voice is not None:
            req["voice"] = voice
        if model is not None:
            req["model"] = model
        if language is not None:
            req["language"] = language
        if emotion is not None:
            req["emotion"] = emotion
        try:
            self._ws.send(json.dumps(req, ensure_ascii=False))
        except Exception as exc:
            if _is_closed_error(exc):
                raise ConnectionFailed("Соединение закрыто (connection closed): %s" % exc) from exc
            raise
        # запрос может ждать в очереди до 60 с, поэтому ждём дольше обычного таймаута
        wait = max(self.timeout, 90.0)
        while True:
            msg = _recv_json(self._ws, wait)
            if msg is None:
                raise ConnectionFailed("Нет ответа от /ws/tts за %.0f с (no frame received)" % wait)
            mrid = msg.get("requestId")
            if mrid is not None and mrid != rid:
                continue  # кадр другого запроса (например, cancelled отменённого ранее)
            t = msg.get("type")
            if t == "audio_chunk":
                sr = msg.get("sampleRate")
                if isinstance(sr, int) and sr > 0:
                    self.sample_rate = sr
                audio = msg.get("audio")
                if isinstance(audio, str) and audio:
                    yield base64.b64decode(audio)
            elif t == "audio_end":
                self.last_total_chunks = msg.get("totalChunks")
                sr = msg.get("sampleRate")
                if isinstance(sr, int) and sr > 0:
                    self.sample_rate = sr
                return
            elif t == "cancelled":
                self.last_total_chunks = msg.get("totalChunks")
                return
            elif t == "error":
                raise StreamError(str(msg.get("message", "error")), body=msg, code=msg.get("code"))
            # text_chunk и прочие служебные кадры пропускаем

    def cancel(self, request_id: Optional[str] = None) -> None:
        """Отменить один запрос по ``requestId`` или (без аргумента) текущий и всю очередь. / Cancel."""
        msg: Dict[str, Any] = {"type": "cancel"}
        if request_id:
            msg["requestId"] = request_id
        self._ws.send(json.dumps(msg))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._ws.close()
            except Exception:
                pass

    def __enter__(self) -> "TTSStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def open_ws(connect: WSConnect, url: str, headers: Dict[str, str], timeout: float) -> Any:
    """Открыть сокет с заголовками и без лимита размера кадра. / Open a socket with headers."""
    try:
        return connect(url, additional_headers=headers, max_size=None, open_timeout=timeout)
    except TypeError:
        # старые версии websockets.sync без additional_headers / open_timeout
        try:
            return connect(url, max_size=None)
        except Exception as exc:  # pragma: no cover
            raise ConnectionFailed("Не удалось открыть WebSocket (connect failed): %s" % exc) from exc
    except FreedomSpeechError:
        raise
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 401 or "401" in str(exc):
            from .errors import AuthenticationError
            raise AuthenticationError(
                "Ключ не принят при открытии WebSocket: проверьте api_key или FS_API_KEY "
                "(API key was not accepted): %s" % exc, status=401) from exc
        raise ConnectionFailed("Не удалось открыть WebSocket (connect failed): %s" % exc) from exc
