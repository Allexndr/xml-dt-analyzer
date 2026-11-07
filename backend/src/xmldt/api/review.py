"""Expert review of the AI-prepared knowledge base.

Everything the AI expert prepared is listed here for a customs specialist to confirm:

* ``rule``     — conditional / format rules compiled from table 3 and the expert rules (``ai-expert``);
* ``manual``   — table-3 sentences that are still not automated (the specialist can describe them);
* ``concept``  — the concept catalog (graph ↔ XML element mapping);
* ``binding``  — element ↔ classifier bindings;
* ``edition``  — legal-text editions whose start date is not in the markup (ambiguous).

Decisions are stored in SQLite (``XMLDT_VAR/review.sqlite3``) with reviewer and time, can be exported,
and are reflected in check results (``legal_ref.review = "approved:<email>"``).
Only e-mails listed in ``XMLDT_REVIEWERS`` (comma-separated) may record decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from xmldt.api import auth

STATUSES = ("pending", "approved", "rejected", "needs_changes")
TYPES = ("rule", "manual", "concept", "binding", "edition")

router = APIRouter(prefix="/api/review", tags=["review"])
_items: dict[str, dict] = {}
_db_path: Path | None = None
_lock = threading.Lock()


def reviewers() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("XMLDT_REVIEWERS", "").split(",") if e.strip()}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  item_key TEXT NOT NULL, status TEXT NOT NULL, comment TEXT, reviewer TEXT NOT NULL,
  at REAL NOT NULL, item_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS decisions_key ON decisions(item_key, at);
"""


def _conn():
    from xmldt.api import db

    assert _db_path is not None
    return db.connect(_db_path, _SCHEMA)


def _hash(item: dict) -> str:
    body = {k: v for k, v in item.items() if k not in ("status", "comment", "reviewer", "at", "stale")}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def configure(var: Path, *, rules, expert, catalog, bindings, editions: list[dict]) -> None:
    """Build the list of reviewable items from the loaded knowledge base."""
    global _db_path
    _db_path = var / "review.sqlite3"
    items: dict[str, dict] = {}

    def add(kind: str, iid: str, title: str, proposal, source: str | None, text: str = ""):
        key = f"{kind}:{iid}"
        items[key] = {"key": key, "type": kind, "id": iid, "title": title, "proposal": proposal,
                      "source": source, "text": text}

    for r in rules:
        ref = r.legal_ref or {}
        src = f"{ref.get('doc', '')}, табл. {ref.get('table', '')}, поз. {ref.get('position', '')}" if ref else None
        if r.kind == "conditional":
            add("rule", r.id, r.text[:140], r.params.get("dsl"), src, r.text)
        elif r.kind == "manual":
            add("manual", r.id, r.text[:140], None, src, r.text)
        elif r.kind in ("pattern", "allowed_values", "classifier_extra", "one_of_children", "single_instance"):
            add("rule", r.id, r.text[:140], {"kind": r.kind, **r.params}, src, r.text)
    for e in expert:
        add("rule", e["id"], e.get("note") or e["id"], e, f"экспертное правило (основание {e.get('source')})", e.get("note", ""))
    for c in catalog.concepts.values():
        add("concept", c.key, f"{c.title} (графа {', '.join(c.graphs) or '—'})",
            {"graphs": c.graphs, "paths": c.paths, "level": c.level, "classifier": c.classifier,
             "pitfalls": c.pitfalls}, "каталог понятий R.055 v1.2.0")
    for b in bindings:
        bid = (b.element or "@" + (b.attribute or "")) + (f"@{b.within}" if b.within else "")
        add("binding", bid, f"{b.element or 'атрибут ' + b.attribute} → {b.classifier}", asdict(b), "привязка к классификатору")
    for e in editions:
        add("edition", e["id"], f"{e['doc']}: редакция по «{e['amending_act']}»",
            {"amending_act": e["amending_act"], "valid_from": e["valid_from"], "valid_to": e["valid_to"]},
            e["doc"], "Дата начала редакции не указана в разметке и не найдена в изменяющем акте; подтвердите её.")
    with _lock:
        _items.clear()
        _items.update(items)


def _latest() -> dict:
    if _db_path is None:
        return {}
    c = _conn()
    try:
        rows = c.execute("SELECT d.* FROM decisions d JOIN (SELECT item_key, MAX(at) at FROM decisions GROUP BY item_key) m "
                         "ON d.item_key = m.item_key AND d.at = m.at").fetchall()
    finally:
        c.close()
    return {r["item_key"]: r for r in rows}


def _alias(email: str) -> str:
    """Non-identifying reviewer label for people outside the reviewer list."""
    return "эксперт " + hashlib.sha256(("xmldt-reviewer:" + email).encode()).hexdigest()[:6]


def status_of(item_key: str) -> str | None:
    """'approved' / 'rejected' / 'needs_changes' for check results (no e-mail); None when undecided or stale."""
    row = _latest().get(item_key)
    item = _items.get(item_key)
    if not row or not item or row["item_hash"] != _hash(item):
        return None
    return row["status"]


def _viewer(request: Request) -> tuple[str, bool]:
    user = auth.user_from_request(request)
    if not user:
        raise HTTPException(401, "Войдите по e-mail, чтобы открыть экспертную проверку")
    return user[1].lower(), user[1].lower() in reviewers()


def _reviewer(request: Request) -> str:
    user = auth.user_from_request(request)
    if not user:
        raise HTTPException(401, "Войдите по e-mail, чтобы подтверждать правила")
    if user[1].lower() not in reviewers():
        raise HTTPException(403, "Ваш e-mail не входит в список экспертов (XMLDT_REVIEWERS)")
    return user[1].lower()


@router.get("/items")
def list_items(request: Request, type: str | None = None, status: str | None = None, q: str | None = None) -> dict:
    _, is_reviewer = _viewer(request)
    show = (lambda e: e) if is_reviewer else (lambda e: _alias(e) if e else None)  # noqa: E731
    latest = _latest()
    out = []
    for key, it in _items.items():
        if type and it["type"] != type:
            continue
        row = latest.get(key)
        stale = bool(row) and row["item_hash"] != _hash(it)
        st = row["status"] if row and not stale else "pending"
        if status and st != status:
            continue
        if q and q.lower() not in (it["title"] + " " + it["id"] + " " + (it["text"] or "")).lower():
            continue
        out.append({**it, "status": st, "stale": stale, "comment": row["comment"] if row else None,
                    "reviewer": show(row["reviewer"]) if row else None, "at": row["at"] if row else None})
    counts: dict[str, dict[str, int]] = {}
    for key, it in _items.items():
        row = latest.get(key)
        st = row["status"] if row and row["item_hash"] == _hash(it) else "pending"
        counts.setdefault(it["type"], {}).setdefault(st, 0)
        counts[it["type"]][st] += 1
    return {"items": out[:500], "total": len(out), "counts": counts, "can_review": is_reviewer}


class DecisionIn(BaseModel):
    status: str
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/items/{key:path}")
def decide(key: str, body: DecisionIn, request: Request) -> dict:
    who = _reviewer(request)
    if body.status not in STATUSES:
        raise HTTPException(422, "Неизвестный статус")
    item = _items.get(key)
    if not item:
        raise HTTPException(404, "Элемент не найден")
    if body.status in ("rejected", "needs_changes") and not (body.comment or "").strip():
        raise HTTPException(422, "Для отклонения или доработки нужен комментарий")
    c = _conn()
    try:
        c.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                  (key, body.status, body.comment, who, time.time(), _hash(item)))
        c.commit()
    finally:
        c.close()
    return {"key": key, "status": body.status, "reviewer": who}


@router.get("/export")
def export(request: Request) -> dict:
    """All current decisions (for applying them to the YAML sources of the knowledge base). Reviewers only."""
    _reviewer(request)
    latest = _latest()
    return {"decisions": [
        {"key": k, "status": r["status"], "comment": r["comment"], "reviewer": r["reviewer"], "at": r["at"],
         "stale": k in _items and r["item_hash"] != _hash(_items[k])}
        for k, r in sorted(latest.items())]}
