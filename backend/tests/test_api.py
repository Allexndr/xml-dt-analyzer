"""HTTP API tests (docs/api.md) with an isolated storage directory."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"
pytestmark = pytest.mark.skipif(not (DATA / "derived" / "r055_1.2.0_rules_compiled.json").exists(),
                                reason="run xmldt.kb.build first")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XMLDT_VAR", str(tmp_path))
    monkeypatch.setenv("XMLDT_MAX_UPLOAD", str(2 * 1024 * 1024))
    import xmldt.api.app as appmod
    appmod = importlib.reload(appmod)
    return TestClient(appmod.app)


def upload(c, data: bytes, name="dt.xml"):
    return c.post("/api/files", files={"file": (name, data, "text/xml")})


def test_upload_analyse_and_fetch(client):
    r = upload(client, SAMPLE.read_bytes())
    assert r.status_code == 201
    m = r.json()
    assert m["status"] == "ready" and m["schema"]["version"] == "1.2.0" and m["xsd_valid"] is True
    assert m["goods_items"] == 3 and "owner" not in m
    fid = m["id"]
    text = client.get(f"/api/files/{fid}/text").text
    g = client.get(f"/api/files/{fid}/facts/goods").json()
    assert g["value"]["goods_items"] == 3
    ev = g["evidence"][0]
    assert text[ev["start"]:ev["end"]].startswith("<cacdo:GDGoodsItemDetails")  # ASCII-only prefix: UTF-16 == str index here
    assert client.get(f"/api/files/{fid}/facts/marking").json()["value"]["codes_in_ranges"] == 300
    assert client.get(f"/api/files/{fid}/concepts/consignee").json()["warnings"]
    rep = client.get(f"/api/files/{fid}/checks", params={"jurisdiction": "KZ"}).json()
    assert [x["rule_id"] for x in rep["results"]] == ["CS-marking-duplicates"]
    assert rep["summary"]["by_status"]["not_implemented"] > 0
    hist = client.get(f"/api/files/{fid}/history").json()
    assert [h["kind"] for h in hist] == ["checks", "concept", "marking", "goods"]
    assert hist[-1]["result"]["value"]["goods_items"] == 3
    assert client.get("/api/files").json()[0]["id"] == fid


def test_files_are_isolated_between_sessions(client, tmp_path):
    fid = upload(client, SAMPLE.read_bytes()).json()["id"]
    import xmldt.api.app as appmod
    other = TestClient(appmod.app)
    assert other.get(f"/api/files/{fid}").status_code == 404
    assert other.get(f"/api/files/{fid}/text").status_code == 404
    assert other.get("/api/files").json() == []
    assert other.delete(f"/api/files/{fid}").status_code == 404


def test_broken_xml_is_stored_with_error(client):
    m = upload(client, "<a>\n<b></a>".encode()).json()
    assert m["status"] == "error" and m["error"]["line"] == 2
    assert client.get(f"/api/files/{m['id']}/text").text == "<a>\n<b></a>"
    assert client.get(f"/api/files/{m['id']}/facts/goods").status_code == 409


def test_limits_and_types(client):
    assert upload(client, b"<a/>" * (600 * 1024)).status_code == 413
    assert upload(client, b"hello", name="x.txt").status_code in (201, 415)  # text/xml content-type is sent
    r = client.post("/api/files", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    assert client.get("/api/files/../../etc/passwd").status_code == 404
    assert client.get("/api/files/notanid").status_code == 404


def test_delete(client):
    fid = upload(client, SAMPLE.read_bytes()).json()["id"]
    assert client.delete(f"/api/files/{fid}").status_code == 204
    assert client.get(f"/api/files/{fid}").status_code == 404


def test_bad_jurisdiction(client):
    fid = upload(client, SAMPLE.read_bytes()).json()["id"]
    assert client.get(f"/api/files/{fid}/checks", params={"jurisdiction": "XX"}).status_code == 422
