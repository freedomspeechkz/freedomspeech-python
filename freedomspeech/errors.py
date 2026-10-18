"""Ошибки SDK. / SDK exceptions."""
from __future__ import annotations

from typing import Any, Optional


class FreedomSpeechError(Exception):
    """Базовая ошибка API Freedom Speech. / Base error for the Freedom Speech API.

    Атрибуты / attributes:
        status: HTTP-код ответа (None для ошибок WebSocket и сети).
        body: тело ответа как есть (dict, если сервер вернул JSON, иначе str).
        code: машинный код ошибки, если сервер его прислал (например ``queue_full``).
    """

    def __init__(self, message: str, status: Optional[int] = None,
                 body: Any = None, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body
        self.code = code

    def __str__(self) -> str:
        parts = [self.message]
        if self.status is not None:
            parts.append("HTTP %d" % self.status)
        if self.code:
            parts.append("code=%s" % self.code)
        return " | ".join(parts)


class AuthenticationError(FreedomSpeechError):
    """Ключ не принят (401). / The API key was rejected (401)."""


class StreamError(FreedomSpeechError):
    """Сервер прислал кадр ``{"type": "error"}`` по WebSocket. / Error frame on a WebSocket."""


class ConnectionFailed(FreedomSpeechError):
    """Не удалось соединиться или соединение оборвалось. / Network or socket failure."""


def _extract_message(body: Any) -> str:
    """Главное сообщение из JSON-тела ошибки: ``error``, затем ``message``, затем ``details``."""
    if isinstance(body, dict):
        for key in ("error", "message", "details"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
        return str(body)
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8", "replace")[:300]
        except Exception:  # pragma: no cover
            return repr(body)[:300]
    return str(body)[:300]


def error_from_response(status: int, body: Any) -> FreedomSpeechError:
    """Собрать исключение по HTTP-коду и телу ответа. / Build an exception from an HTTP response."""
    detail = _extract_message(body)
    code = body.get("code") if isinstance(body, dict) else None
    if status == 401:
        return AuthenticationError(
            "Ключ не принят: проверьте api_key или переменную окружения FS_API_KEY "
            "(API key was not accepted). Ответ сервера: %s" % detail,
            status=status, body=body, code=code)
    if status == 403:
        msg = "Эндпоинт недоступен для этого ключа (endpoint not available for this key): %s" % detail
    elif status == 404:
        msg = "Не найдено (not found): %s" % detail
    elif status == 400:
        msg = "Неверный параметр (bad request): %s" % detail
    elif status in (502, 503):
        msg = "Речевой сервис временно недоступен (speech service unavailable): %s" % detail
    else:
        msg = "Ошибка API (API error): %s" % detail
    return FreedomSpeechError(msg, status=status, body=body, code=code)
