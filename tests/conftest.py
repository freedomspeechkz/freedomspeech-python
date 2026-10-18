"""Моки без сети: поддельная requests.Session и поддельный WebSocket. / Network-free fakes."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from freedomspeech import FreedomSpeech  # noqa: E402


class FakeResponse:
    def __init__(self, status: int = 200, json_body: Any = None, content: bytes = b"",
                 content_type: str = "application/json") -> None:
        self.status_code = status
        self._json = json_body
        self.content = content if json_body is None else json.dumps(json_body, ensure_ascii=False).encode()
        self.headers = {"Content-Type": content_type}

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


class FakeSession:
    """Запоминает запросы и отдаёт заранее заданные ответы по очереди."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.queue: List[FakeResponse] = []
        self.closed = False

    def push(self, resp: FakeResponse) -> "FakeSession":
        self.queue.append(resp)
        return self

    def request(self, method: str, url: str, **kw: Any) -> FakeResponse:
        call = {"method": method, "url": url}
        call.update(kw)
        self.calls.append(call)
        if not self.queue:
            raise AssertionError("unexpected request %s %s" % (method, url))
        return self.queue.pop(0)

    def close(self) -> None:
        self.closed = True

    @property
    def last(self) -> Dict[str, Any]:
        return self.calls[-1]


class FakeWS:
    """Поддельный сокет: отдаёт заготовленные кадры, запоминает отправленное."""

    def __init__(self, frames: Optional[List[Any]] = None) -> None:
        self.frames: List[Any] = list(frames or [])
        self.sent: List[Any] = []
        self.closed = False
        self.url: Optional[str] = None
        self.headers: Optional[Dict[str, str]] = None

    def recv(self, timeout: Optional[float] = None) -> Any:
        if not self.frames:
            raise TimeoutError("no more frames")
        item = self.frames.pop(0)
        if callable(item) and not isinstance(item, BaseException):
            item = item(self)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict):
            return json.dumps(item, ensure_ascii=False)
        return item

    def send(self, data: Any) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True

    def sent_json(self) -> List[Dict[str, Any]]:
        return [json.loads(x) for x in self.sent if isinstance(x, str)]

    def sent_bytes(self) -> bytes:
        return b"".join(x for x in self.sent if isinstance(x, (bytes, bytearray)))


def make_ws_connect(ws: FakeWS):
    def connect(url: str, **kw: Any) -> FakeWS:
        ws.url = url
        ws.headers = kw.get("additional_headers")
        return ws
    return connect


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def client(session: FakeSession, monkeypatch: pytest.MonkeyPatch) -> FreedomSpeech:
    monkeypatch.delenv("FS_API_KEY", raising=False)
    monkeypatch.delenv("FS_BASE_URL", raising=False)
    return FreedomSpeech(api_key="test-key", session=session)
