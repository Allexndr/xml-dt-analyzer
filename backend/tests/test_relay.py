"""Local-model relay through the user's browser (WebSocket /api/relay)."""

from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XMLDT_VAR", str(tmp_path))
    monkeypatch.delenv("XMLDT_ALLOW_LOCAL_LLM", raising=False)
    import xmldt.api.app as appmod
    return TestClient(importlib.reload(appmod).app)


def _completion(content=None, tool_calls=None):
    return {"choices": [{"message": {"content": content, "tool_calls": tool_calls}}]}


def test_relay_answers_through_browser(client):
    fid = client.post("/api/files", files={"file": ("dt.xml", SAMPLE.read_bytes(), "text/xml")}).json()["id"]
    client.put("/api/settings/ai", json={"mode": "local", "provider": "ollama", "model": "qwen-test"})
    scripted = [
        _completion(tool_calls=[{"id": "t1", "type": "function", "function": {"name": "count_goods", "arguments": "{}"}}]),
        _completion(content=json.dumps({"summary": "Товарных позиций: {{r1.goods_items}}.", "facts": []}, ensure_ascii=False)),
    ]
    seen: list[dict] = []
    result: dict = {}
    with client.websocket_connect("/api/relay", headers={"origin": "http://testserver"}) as ws:
        t = threading.Thread(target=lambda: result.update(
            client.post(f"/api/files/{fid}/ask", json={"question": "Сколько товаров?"}).json()))
        t.start()
        for reply in scripted:
            req = ws.receive_json()
            seen.append(req)
            ws.send_json({"id": req["id"], "status": 200, "json": reply})
        t.join(timeout=30)
    assert result["mode"] == "llm" and result["model"] == "local-browser:qwen-test"
    assert result["summary"] == "Товарных позиций: 3."
    assert seen[0]["op"] == "fetch" and seen[0]["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen[0]["body"]["model"] == "qwen-test" and seen[0]["body"]["tools"]


def test_relay_without_browser_falls_back(client):
    fid = client.post("/api/files", files={"file": ("dt.xml", SAMPLE.read_bytes(), "text/xml")}).json()["id"]
    client.put("/api/settings/ai", json={"mode": "local", "provider": "ollama", "model": "qwen-test"})
    r = client.post(f"/api/files/{fid}/ask", json={"question": "Сколько товаров?"}).json()
    assert r["mode"] == "rules" and "браузер не подключён" in r["warnings"][0]


def test_relay_rejects_cross_site_origin(client):
    client.get("/api/files")  # gets the anonymous cookie
    with pytest.raises(Exception):
        with client.websocket_connect("/api/relay", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_hub_multiple_tabs_and_foreign_answers():
    import asyncio
    import threading as th

    from xmldt.ai.providers import ProviderError
    from xmldt.ai.relay import Connection, RelayHub

    loop = asyncio.new_event_loop()
    th.Thread(target=loop.run_forever, daemon=True).start()
    hub = RelayHub()
    sent: dict[str, list] = {"a": [], "b": []}

    def conn(name):
        async def send(msg):
            sent[name].append(msg)
        return Connection(send=send, loop=loop)

    a, b = conn("a"), conn("b")
    hub.attach("u", a)
    hub.attach("u", b)

    def answer_later(c, name, payload_from=None):
        import time
        for _ in range(100):
            if sent[name]:
                rid = sent[name][-1]["id"]
                hub.resolve("u", payload_from or c, {"id": rid, "status": 200, "json": {"ok": name}})
                return
            time.sleep(0.01)

    # newest tab (b) gets the request; an answer from tab a for b's request id is ignored
    t = th.Thread(target=answer_later, args=(b, "b", a)); t.start()
    with pytest.raises(ProviderError):
        hub.call("u", {"op": "fetch"}, timeout=0.5)
    t.join()
    # closing b: requests go to a
    hub.detach("u", b)
    sent["a"].clear()
    t = th.Thread(target=answer_later, args=(a, "a")); t.start()
    assert hub.call("u", {"op": "fetch"}, timeout=2)["json"] == {"ok": "a"}
    t.join()
    hub.detach("u", a)
    assert not hub.connected("u")
    loop.call_soon_threadsafe(loop.stop)
