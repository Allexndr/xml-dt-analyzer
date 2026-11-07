"""Expert review base: listing, decisions by reviewers only, reflection in check results."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XMLDT_VAR", str(tmp_path))
    monkeypatch.setenv("XMLDT_REVIEWERS", "expert@example.com")
    monkeypatch.setenv("XMLDT_AUTH_DEV_ECHO", "1")
    import xmldt.api.auth as authmod
    importlib.reload(authmod)
    import xmldt.api.review as reviewmod
    importlib.reload(reviewmod)
    import xmldt.api.app as appmod
    return importlib.reload(appmod).app


def login(c: TestClient, email: str) -> None:
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    assert c.post("/api/auth/verify", json={"email": email, "code": code}).status_code == 200


def test_items_listed_by_type(app):
    c = TestClient(app)
    assert c.get("/api/review/items").status_code == 401
    login(c, "viewer@example.com")
    r = c.get("/api/review/items").json()
    assert set(r["counts"]) >= {"rule", "manual", "concept", "binding", "edition"}
    assert r["can_review"] is False
    assert c.get("/api/review/items", params={"type": "concept"}).json()["total"] == 65


def test_only_reviewers_decide_and_comment_required(app):
    anon = TestClient(app)
    assert anon.post("/api/review/items/concept:consignor", json={"status": "approved"}).status_code == 401
    other = TestClient(app)
    login(other, "someone@example.com")
    assert other.post("/api/review/items/concept:consignor", json={"status": "approved"}).status_code == 403
    ex = TestClient(app)
    login(ex, "expert@example.com")
    assert ex.get("/api/review/items").json()["can_review"] is True
    assert ex.post("/api/review/items/concept:consignor", json={"status": "rejected"}).status_code == 422
    assert ex.post("/api/review/items/concept:consignor", json={"status": "approved"}).status_code == 200
    item = ex.get("/api/review/items", params={"type": "concept", "q": "consignor"}).json()["items"][0]
    assert item["status"] == "approved" and item["reviewer"] == "expert@example.com"
    assert ex.get("/api/review/export").json()["decisions"][0]["key"] == "concept:consignor"


def test_decision_shown_in_checks(app):
    ex = TestClient(app)
    login(ex, "expert@example.com")
    assert ex.post("/api/review/items/rule:T3-19.18.28.4.1-1", json={"status": "approved"}).status_code == 200
    raw = SAMPLE.read_text(encoding="utf-8").replace("<casdo:InformationKindCode>13<", "<casdo:InformationKindCode>10<", 1)
    fid = ex.post("/api/files", files={"file": ("dt.xml", raw.encode(), "text/xml")}).json()["id"]
    rep = ex.get(f"/api/files/{fid}/checks", params={"jurisdiction": "KZ"}).json()
    r = next(x for x in rep["results"] if x["rule_id"] == "T3-19.18.28.4.1-1")
    assert r["legal_ref"]["review"] == "approved"
    assert "expert@example.com" not in str(rep)


def test_reviewer_identity_hidden_from_others(app):
    ex = TestClient(app)
    login(ex, "expert@example.com")
    ex.post("/api/review/items/concept:consignee", json={"status": "approved"})
    viewer = TestClient(app)
    login(viewer, "viewer@example.com")
    item = viewer.get("/api/review/items", params={"type": "concept", "q": "consignee"}).json()["items"][0]
    assert item["status"] == "approved" and "@" not in item["reviewer"]
    assert viewer.get("/api/review/export").status_code == 403
    assert TestClient(app).get("/api/review/export").status_code == 401
