"""Browser relay for a local model (ТЗ п. 8.1 in the cloud deployment).

The server cannot reach the user's ``localhost``. The user's browser can. While the analysis page is
open, the browser keeps a WebSocket to ``/api/relay``; when a question needs the model, the server
sends the OpenAI-compatible request over that socket, the browser forwards it to the local model
(Ollama / LM Studio / llama.cpp on ``http://localhost:…``) and returns the response.

The declaration never goes to a third-party provider: only the question, the catalog and tool results
travel server → browser → local model. The browser refuses any target that is not a loopback address.

Threading: ``/ask`` runs in a worker thread; the socket lives on the event loop. Requests are sent with
``run_coroutine_threadsafe`` and answered through ``concurrent.futures.Future``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import itertools
import threading
from dataclasses import dataclass, field

from xmldt.ai.providers import OpenAICompatible, ProviderError, Reply

TIMEOUT = 180.0


@dataclass(eq=False)
class Connection:
    send: callable  # async (dict) -> None
    loop: asyncio.AbstractEventLoop
    pending: dict[int, concurrent.futures.Future] = field(default_factory=dict)
    closed: bool = False


def _fail(f: concurrent.futures.Future, message: str) -> None:
    if not f.done():
        try:
            f.set_exception(ProviderError(message))
        except concurrent.futures.InvalidStateError:
            pass  # resolved concurrently


class RelayHub:
    """Several tabs of the same user may be open: each keeps its own socket; requests go to the newest."""

    def __init__(self):
        self._conns: dict[str, list[Connection]] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def attach(self, owner: str, conn: Connection) -> None:
        with self._lock:
            self._conns.setdefault(owner, []).append(conn)

    def detach(self, owner: str, conn: Connection) -> None:
        with self._lock:
            conn.closed = True
            lst = self._conns.get(owner, [])
            if conn in lst:
                lst.remove(conn)
            if not lst:
                self._conns.pop(owner, None)
            pending = list(conn.pending.values())
            conn.pending.clear()
        for f in pending:
            _fail(f, "Связь с браузером прервана")

    def connected(self, owner: str) -> bool:
        with self._lock:
            return bool(self._conns.get(owner))

    def resolve(self, owner: str, conn: Connection, msg: dict) -> None:
        try:
            rid = int(msg.get("id", -1))
        except (TypeError, ValueError):
            return
        with self._lock:
            f = conn.pending.pop(rid, None)  # only the socket that got the request may answer it
        if f is None or f.done():
            return
        try:
            if msg.get("error"):
                f.set_exception(ProviderError(f"Локальная модель: {str(msg['error'])[:200]}"))
            else:
                f.set_result(msg)
        except concurrent.futures.InvalidStateError:
            pass

    def call(self, owner: str, request: dict, timeout: float = TIMEOUT) -> dict:
        """Send a request to the owner's newest browser tab and wait for its reply (worker thread)."""
        rid = next(self._ids)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            conns = self._conns.get(owner) or []
            conn = conns[-1] if conns else None
            if conn is None or conn.closed:
                raise ProviderError("Локальный ИИ: браузер не подключён. Откройте страницу анализа и проверьте настройки.")
            conn.pending[rid] = fut
        asyncio.run_coroutine_threadsafe(conn.send({"id": rid, **request}), conn.loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                conn.pending.pop(rid, None)
            raise ProviderError("Локальная модель не ответила вовремя") from None


class RelayProvider(OpenAICompatible):
    """OpenAI-compatible provider whose HTTP transport is the user's browser."""

    def __init__(self, hub: RelayHub, owner: str, base_url: str, model: str):
        super().__init__("relay", base_url.rstrip("/"), model, None)
        self.hub, self.owner = hub, owner

    def _post(self, path: str, body: dict) -> dict:
        msg = self.hub.call(self.owner, {"op": "fetch", "method": "POST", "url": self.base_url + path, "body": body})
        return self._unwrap(msg)

    def _get(self, path: str) -> dict:
        return self._unwrap(self.hub.call(self.owner, {"op": "fetch", "method": "GET", "url": self.base_url + path}))

    @staticmethod
    def _unwrap(msg: dict) -> dict:
        status = int(msg.get("status", 0))
        if status >= 400 or status == 0:
            raise ProviderError(f"Локальная модель вернула ошибку {status}: {str(msg.get('text') or '')[:200]}")
        data = msg.get("json")
        if not isinstance(data, dict):
            raise ProviderError("Локальная модель вернула не JSON")
        return data

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        return self._parse(self._post("/chat/completions", self._body(messages, tools)))

    def list_models(self) -> list[str]:
        return sorted(m["id"] for m in self._get("/models").get("data", []))
