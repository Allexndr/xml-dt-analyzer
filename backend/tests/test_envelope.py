"""National envelopes (Keden <KedenGoodsDeclaration>) around an R.055 document; settings input errors."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"


def _wrapped() -> bytes:
    """The synthetic sample inside a Keden-style envelope: EEC namespaces declared on the wrapper."""
    src = SAMPLE.read_text(encoding="utf-8")
    src = re.sub(r"^<\?xml[^>]*\?>\s*(<!--.*?-->\s*)?", "", src, flags=re.S)
    m = re.match(r"<([A-Za-z0-9_]+:)?GoodsDeclaration([^>]*)>", src)
    assert m
    decls = " ".join(re.findall(r'xmlns(?::[A-Za-z0-9_]+)?="[^"]+"', m.group(2)))
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            f'<ccecd:KedenGoodsDeclaration xmlns:ccecd="http://www.codecraft.kz/keden/ecd/v1" {decls}>\n'
            f"{src}\n<ccecd:CustomsOfficeCode>55301</ccecd:CustomsOfficeCode>\n"
            "</ccecd:KedenGoodsDeclaration>\n").encode()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XMLDT_VAR", str(tmp_path))
    import xmldt.api.app as appmod
    return TestClient(importlib.reload(appmod).app)


def test_envelope_is_unwrapped(client):
    fid = client.post("/api/files", files={"file": ("k.xml", _wrapped(), "text/xml")}).json()["id"]
    meta = client.get(f"/api/files/{fid}").json()
    assert meta["schema"]["code"] == "R.055" and meta["schema"]["version"] == "1.2.0"
    assert meta["xsd_valid"] is True and meta["goods_items"] == 3
    r = client.post(f"/api/files/{fid}/ask", json={"question": "Сколько товаров?", "mode": "rules"}).json()
    assert "3" in r["summary"]


def test_key_in_model_field_is_explained_and_not_echoed(client):
    key = "sk-proj-" + "A1b2C3d4" * 20
    r = client.put("/api/settings/ai", json={"mode": "api", "provider": "openai", "model": key})
    assert r.status_code == 422
    assert key not in r.text and "Ключ API" in r.json()["detail"]
    r = client.put("/api/settings/ai", json={"model": "sk-proj-short"})
    assert r.status_code == 422 and "Ключ API" in r.json()["detail"]


def test_keden_rate_type_concept(client):
    raw = _wrapped().replace(b"</ccecd:KedenGoodsDeclaration>", b"<ccecd:RateType>ETT</ccecd:RateType></ccecd:KedenGoodsDeclaration>")
    fid = client.post("/api/files", files={"file": ("k.xml", raw, "text/xml")}).json()["id"]
    r = client.post(f"/api/files/{fid}/ask", json={"question": "декларация ВТО или ЕТТ"}).json()
    assert "ETT" in r["summary"]


def test_unknown_model_falls_back_to_default(monkeypatch):
    from xmldt.ai import providers as pv

    calls = []

    class Resp:
        def __init__(self, status, payload):
            self.status_code, self._p = status, payload
            self.text = __import__("json").dumps(payload)

        def json(self):
            return self._p

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json, headers):
            calls.append(json["model"])
            if json["model"] == "lunaroute":
                return Resp(404, {"error": {"code": "model_not_found", "message": "does not exist"}})
            return Resp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(pv, "_client", lambda: FakeClient())
    prov = pv.OpenAICompatible("openai", "https://api.openai.com/v1", "lunaroute", "k")
    prov.fallback_model = "gpt-5.4-mini"
    assert prov.chat([{"role": "user", "content": "x"}], []).content == "ok"
    assert calls == ["lunaroute", "gpt-5.4-mini"] and prov.switched_from == "lunaroute"
