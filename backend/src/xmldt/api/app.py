"""HTTP API of the prototype (see docs/api.md). No LLM: facts and checks are deterministic.

Run:  XMLDT_DATA=../data uv run uvicorn xmldt.api.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from collections import OrderedDict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from xmldt.ai.orchestrator import DialogState
from xmldt.ai.orchestrator import answer as ai_answer
from xmldt.ai.providers import ProviderError, make_provider
from xmldt.ai.relay import Connection, RelayHub, RelayProvider
from xmldt.ai.tools import Toolbox

from xmldt.api import auth, review
from xmldt.api.store import FileMeta, Store
from xmldt.facts.tools import Declaration, ToolResult
from xmldt.indexer import IndexResult, index_xml
from xmldt.kb.classifiers import load_all
from xmldt.kb.concepts import load_catalog
from xmldt.rules.compiler import load_bindings, load_expert, load_rules
from xmldt.rules.engine import Engine
from xmldt.schema import validate as xsdval

DATA = Path(os.environ.get("XMLDT_DATA", Path(__file__).resolve().parents[4] / "data"))
VAR = Path(os.environ.get("XMLDT_VAR", Path(__file__).resolve().parents[3] / "var"))
MAX_UPLOAD = int(os.environ.get("XMLDT_MAX_UPLOAD", 50 * 1024 * 1024))  # Vercel functions accept ~4.5 MB bodies
COOKIE = "xmldt_sid"
JURISDICTIONS = {"KZ", "RU", "BY", "AM", "KG"}

app = FastAPI(title="XML DT analyzer (prototype)", docs_url="/api/docs", openapi_url="/api/openapi.json")
from xmldt.api import db as _db  # noqa: E402
from xmldt.api.store import DbStore  # noqa: E402

# serverless hosting (Vercel): no persistent filesystem -> everything lives in Postgres (DATABASE_URL)
store = DbStore(VAR / "files.sqlite3") if _db.pg_url() else Store(VAR / "files")
PERSIST_STATE = isinstance(store, DbStore)  # AI settings and dialog state survive across instances
RELAY_AVAILABLE = os.environ.get("XMLDT_RELAY_DISABLED") != "1"  # no WebSockets on serverless platforms
auth.configure(store)
app.include_router(auth.router)  # /api/auth/request | verify | me | logout
app.include_router(review.router)  # /api/review/items | export
catalog = load_catalog(DATA / "kb" / "concepts_R.055_1.2.0.yaml")
engine = Engine(
    load_rules(DATA / "derived" / "r055_1.2.0_rules_compiled.json"),
    load_bindings(DATA / "kb" / "classifier_bindings_R.055_1.2.0.yaml"),
    load_all(DATA / "derived"), expert=load_expert(DATA / "kb" / "expert_rules_R.055_1.2.0.yaml"))


class _Cache:
    """Small LRU of indexed declarations (indexing is the expensive step)."""

    def __init__(self, size: int = 8):
        self.size, self.items, self.lock = size, OrderedDict(), Lock()

    def get(self, key: str):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        return None

    def put(self, key: str, value) -> None:
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)

    def drop(self, key: str) -> None:
        with self.lock:
            self.items.pop(key, None)


cache = _Cache()

from xmldt.kb.provisions import NormIndex  # noqa: E402

_ed_files = sorted((DATA / "derived").glob("editions_review_*.json"))
review.configure(VAR, rules=engine.rules, expert=load_expert(DATA / "kb" / "expert_rules_R.055_1.2.0.yaml"),
                 catalog=catalog, bindings=engine.bindings,
                 editions=json.loads(_ed_files[-1].read_text(encoding="utf-8")) if _ed_files else [])

_prov_files = sorted((DATA / "derived").glob("decision257_provisions_*.json"))
norms = NormIndex.load(_prov_files[-1]) if _prov_files else None


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def session(request: Request, response: Response) -> str:
    """Owner of the request: the logged-in user's account or the anonymous session (see api/auth.py)."""
    return auth.current_owner(request, response)[0]


def _meta(owner: str, file_id: str) -> FileMeta:
    try:
        return store.get(owner, file_id)
    except KeyError:
        raise HTTPException(404, "Файл не найден") from None


def _index(meta: FileMeta) -> IndexResult:
    hit = cache.get(meta.id)
    if hit is None:
        hit = index_xml(store.original(meta))
        cache.put(meta.id, hit)
    return hit


def _decl(meta: FileMeta) -> Declaration:
    idx = _index(meta)
    if not idx.ok:
        raise HTTPException(409, "Файл не является корректным XML; анализ недоступен")
    return Declaration(idx, catalog)


def _tool(tr: ToolResult) -> dict:
    return asdict(tr)


# ---- files ----------------------------------------------------------------
@app.post("/api/files", status_code=201)
async def upload(file: UploadFile, owner: str = Depends(session)) -> dict:
    name = file.filename or "file.xml"
    if not name.lower().endswith(".xml") and (file.content_type or "") not in ("text/xml", "application/xml"):
        raise HTTPException(415, "Ожидается XML-файл (.xml)")
    chunks, total = [], 0
    while chunk := await file.read(1 << 20):
        total += len(chunk)
        if total > MAX_UPLOAD:
            raise HTTPException(413, f"Файл больше {MAX_UPLOAD // (1024 * 1024)} МБ")
        chunks.append(chunk)
    data = b"".join(chunks)
    meta = store.create(owner, name, data)
    await run_in_threadpool(_analyse_upload, meta, data)
    return meta.public()


def _analyse_upload(meta: FileMeta, data: bytes) -> None:
    idx = index_xml(data)
    meta.encoding = idx.encoding
    if not idx.ok:
        meta.status, meta.error = "error", dict(idx.error)
        store.save(meta)
        return
    cache.put(meta.id, idx)
    info = xsdval.detect(data, DATA / "schemas")
    if info:
        meta.schema = {"code": info.code, "version": info.version, "namespace": info.namespace,
                       "supported": info.supported}
        if info.supported:
            errs = xsdval.validate(data, DATA / "schemas" / info.code / info.version)
            meta.xsd_valid = not errs
            meta.xsd_error_count = len(errs)
            meta.xsd_errors = [asdict(e) for e in errs]
            stale = xsdval.foreign_namespaces(data, DATA / "schemas" / info.code / info.version)
            if errs and stale:
                meta.schema["note"] = (
                    f"Корень файла объявлен как {info.code} v{info.version}, но вложенные элементы используют "
                    f"пространства имён другой версии: {', '.join(stale[:4])}. Так формирует файлы, например, "
                    "Keden. Данные разобраны и проверены по правилам, а ошибки XSD вызваны этим расхождением.")
    meta.goods_items = len(Declaration(idx, catalog).items())
    store.save(meta)


@app.get("/api/files")
def list_files(owner: str = Depends(session)) -> list[dict]:
    return [m.public() for m in store.list(owner)]


@app.get("/api/files/{file_id}")
def get_file(file_id: str, owner: str = Depends(session)) -> dict:
    return _meta(owner, file_id).public()


@app.get("/api/files/{file_id}/text", response_class=PlainTextResponse)
def get_text(file_id: str, owner: str = Depends(session)) -> PlainTextResponse:
    meta = _meta(owner, file_id)
    idx = _index(meta)
    if idx.ok:
        text = idx.text
    else:  # still show what we can of a broken file
        text = store.original(meta).decode(meta.encoding or "utf-8", errors="replace").lstrip("﻿")
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@app.get("/api/files/{file_id}/raw")
def get_raw(file_id: str, owner: str = Depends(session)) -> Response:
    meta = _meta(owner, file_id)
    safe = "".join(c for c in meta.name if c.isascii() and (c.isalnum() or c in "._-")) or "file.xml"
    return Response(store.original(meta), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@app.delete("/api/files/{file_id}", status_code=204)
def delete_file(file_id: str, owner: str = Depends(session)) -> Response:
    meta = _meta(owner, file_id)
    store.delete(meta)
    cache.drop(meta.id)
    _dialogs.pop((owner, meta.id), None)
    return Response(status_code=204)


@app.get("/api/files/{file_id}/validation")
def validation(file_id: str, owner: str = Depends(session)) -> dict:
    meta = _meta(owner, file_id)
    return {"wellformed_error": meta.error, "xsd": meta.xsd_errors}


# ---- facts ----------------------------------------------------------------
@app.get("/api/catalog")
def get_catalog() -> list[dict]:
    return [{"key": c.key, "title": c.title, "graphs": c.graphs, "level": c.level, "synonyms": c.synonyms}
            for c in catalog.concepts.values()]


@app.get("/api/files/{file_id}/facts/goods")
def facts_goods(file_id: str, owner: str = Depends(session)) -> dict:
    meta = _meta(owner, file_id)
    res = _tool(_decl(meta).count_goods())
    store.add_history(meta, "goods", "Сколько товаров?", {}, res)
    return res


@app.get("/api/files/{file_id}/facts/marking")
def facts_marking(file_id: str, owner: str = Depends(session)) -> dict:
    meta = _meta(owner, file_id)
    res = _tool(_decl(meta).marking_stats())
    store.add_history(meta, "marking", "Коды маркировки", {}, res)
    return res


@app.get("/api/files/{file_id}/concepts/{key}")
def concept(file_id: str, key: str, owner: str = Depends(session)) -> dict:
    meta = _meta(owner, file_id)
    if key not in catalog.concepts:
        raise HTTPException(404, "Неизвестное понятие")
    res = _tool(_decl(meta).get_concept(key))
    store.add_history(meta, "concept", catalog.concepts[key].title, {"key": key}, res)
    return res


# ---- checks ---------------------------------------------------------------
@app.get("/api/files/{file_id}/checks")
def checks(file_id: str, jurisdiction: str | None = None, all: int = 0, owner: str = Depends(session)) -> dict:
    meta = _meta(owner, file_id)
    if jurisdiction is not None and jurisdiction not in JURISDICTIONS:
        raise HTTPException(422, "Неизвестная юрисдикция")
    rep = engine.run(_decl(meta), jurisdiction=jurisdiction, check_date=date.today().isoformat())
    results = [asdict(r) for r in rep.results if all or r.status in ("violation", "needs_info")]
    for r in results:  # show the expert's decision where there is one
        decided = review.status_of(f"rule:{r['rule_id']}")
        if decided:
            r["legal_ref"]["review"] = decided
    out = {"jurisdiction": rep.jurisdiction, "check_date": rep.check_date, "summary": rep.summary(), "results": results}
    title = "Проверка по правилам" + (f" ({jurisdiction})" if jurisdiction else "")
    store.add_history(meta, "checks", title, {"jurisdiction": jurisdiction}, out)
    return out


@app.get("/api/files/{file_id}/history")
def history(file_id: str, owner: str = Depends(session)) -> list[dict]:
    return store.history(_meta(owner, file_id))


# ---- questions and AI settings -------------------------------------------
LOCAL_ALLOWED = os.environ.get("XMLDT_ALLOW_LOCAL_LLM") == "1"  # only for on-prem/dev installs
# Operator-provided key (from the environment, never from code or files under version control)
SERVER_OPENAI_KEY = os.environ.get("XMLDT_OPENAI_API_KEY") or None
DEFAULT_MODEL = os.environ.get("XMLDT_DEFAULT_MODEL", "gpt-5.4-mini")
PROVIDERS = {"openai", "anthropic", "openai_compatible", "ollama"}
_settings: dict[str, dict] = {}  # owner -> settings incl. api_key (memory only, never persisted)
_dialogs: dict[tuple[str, str], DialogState] = {}
_settings_lock = Lock()


_keys: dict[str, str] = {}  # owner -> user's own API key: memory only, never persisted


def _default_ai(owner: str) -> dict:
    return {"_owner": owner, "mode": "api" if SERVER_OPENAI_KEY else "off", "provider": "openai",
            "base_url": None, "model": DEFAULT_MODEL if SERVER_OPENAI_KEY else None,
            "api_key": None, "consent_external": False}


def _ai(owner: str) -> dict:
    with _settings_lock:
        if PERSIST_STATE:
            cfg = {**_default_ai(owner), **(store.kv_get("ai", owner) or {})}
            cfg["api_key"] = _keys.get(owner)
            return cfg
        return _settings.setdefault(owner, _default_ai(owner))


def _save_ai(owner: str, cfg: dict) -> None:
    if PERSIST_STATE:
        if cfg.get("api_key"):
            _keys[owner] = cfg["api_key"]
        else:
            _keys.pop(owner, None)
        store.kv_put("ai", owner, {k: v for k, v in cfg.items() if k not in ("api_key", "_owner")})


def _dialog(owner: str, file_id: str) -> DialogState:
    if PERSIST_STATE:
        return DialogState(turns=(store.kv_get("dialog", f"{owner}:{file_id}") or {}).get("turns", []))
    return _dialogs.setdefault((owner, file_id), DialogState())


def _save_dialog(owner: str, file_id: str, state: DialogState) -> None:
    if PERSIST_STATE:
        store.kv_put("dialog", f"{owner}:{file_id}", {"turns": state.turns})


def _public_ai(cfg: dict) -> dict:
    return {**{k: v for k, v in cfg.items() if k not in ("api_key", "_owner")}, "has_api_key": bool(cfg.get("api_key")),
            "relay_connected": relay_hub.connected(cfg.get("_owner", "")),
            "has_server_key": bool(SERVER_OPENAI_KEY) and cfg["provider"] == "openai",
            "local_allowed": LOCAL_ALLOWED, "relay_available": RELAY_AVAILABLE}


_KEY_RE = re.compile(r"^(sk-|sk_|pk-|xai-|gsk_|AIza|key-)|^[A-Za-z0-9_-]{40,}$")


def _looks_like_key(v: str) -> bool:
    return bool(_KEY_RE.search(v.strip()))


_FIELD_LABELS = {"model": "Модель", "base_url": "Адрес сервера", "api_key": "Ключ API", "question": "Вопрос",
                 "mode": "Режим", "provider": "Провайдер", "email": "E-mail", "code": "Код"}


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    """Readable Russian message; never echo the submitted value (it may be a secret pasted by mistake)."""
    msgs = []
    for e in exc.errors()[:5]:
        field = next((str(x) for x in reversed(e.get("loc", ())) if isinstance(x, str) and x != "body"), "")
        label = _FIELD_LABELS.get(field, field or "запрос")
        if e.get("type") == "string_too_long":
            msgs.append(f"Поле «{label}» слишком длинное (не больше {e.get('ctx', {}).get('max_length')} символов).")
        elif e.get("type") == "missing":
            msgs.append(f"Не заполнено поле «{label}».")
        else:
            msgs.append(f"Поле «{label}» заполнено неверно.")
    if any("Модель" in m for m in msgs):
        msgs.append("Если вы вставили туда ключ API, перенесите его в поле «Ключ API».")
    return JSONResponse({"detail": " ".join(msgs) or "Неверный запрос"}, status_code=422)


class AiSettingsIn(BaseModel):
    mode: str | None = None
    provider: str | None = None
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default="__keep__", max_length=400)
    consent_external: bool | None = None


@app.get("/api/settings/ai")
def get_ai(owner: str = Depends(session)) -> dict:
    return _public_ai(_ai(owner))


@app.put("/api/settings/ai")
def put_ai(body: AiSettingsIn, owner: str = Depends(session)) -> dict:
    cfg = _ai(owner)
    if body.mode is not None:
        if body.mode not in ("off", "api", "local"):
            raise HTTPException(422, "Неизвестный режим")
        cfg["mode"] = body.mode
    if body.model and _looks_like_key(body.model):
        raise HTTPException(422, "В поле «Модель» вставлен ключ API. Ключ вводится в поле «Ключ API», "
                                 "а в поле «Модель» укажите название модели, например gpt-5.4-mini.")
    if body.provider is not None:
        if body.provider not in PROVIDERS:
            raise HTTPException(422, "Неизвестный провайдер")
        cfg["provider"] = body.provider
    for k in ("base_url", "model", "consent_external"):
        v = getattr(body, k)
        if v is not None:
            cfg[k] = v or None if k != "consent_external" else v
    if body.api_key != "__keep__":
        cfg["api_key"] = body.api_key or None
    _save_ai(owner, cfg)
    return _public_ai(cfg)


def _provider_for(cfg: dict, owner: str):
    if cfg["mode"] == "off":
        return None, None
    if cfg["mode"] == "api" and not cfg.get("consent_external"):
        raise HTTPException(409, "Нужно согласие на передачу фрагментов XML внешнему ИИ (настройки ИИ)")
    key = cfg.get("api_key") or (SERVER_OPENAI_KEY if cfg["provider"] == "openai" else None)
    if cfg["mode"] == "local" and cfg["provider"] not in ("ollama", "openai_compatible"):
        raise HTTPException(409, "Для локального режима выберите Ollama или OpenAI-совместимый сервер")
    if cfg["mode"] == "local" and not LOCAL_ALLOWED and not RELAY_AVAILABLE:
        raise HTTPException(409, "На этом хостинге нет WebSocket, поэтому локальный ИИ через браузер недоступен. "
                                 "Установите сервис у себя (Docker) или используйте ИИ через API.")
    if cfg["mode"] == "local" and not LOCAL_ALLOWED:
        # cloud deployment: the user's browser relays requests to the model on their machine
        if not cfg.get("model"):
            raise HTTPException(422, "Не выбрана модель")
        base = cfg.get("base_url") or "http://localhost:11434/v1"
        return RelayProvider(relay_hub, owner, base, cfg["model"]), f"local-browser:{cfg['model']}"
    try:
        model = cfg.get("model") or (DEFAULT_MODEL if cfg["provider"] == "openai" else None)
        prov = make_provider(cfg["provider"], base_url=cfg.get("base_url"), model=model,
                             api_key=key, allow_private=cfg["mode"] == "local" and LOCAL_ALLOWED)
    except ProviderError as exc:
        raise HTTPException(422, str(exc)) from None
    if cfg["provider"] == "openai" and hasattr(prov, "fallback_model"):
        prov.fallback_model = DEFAULT_MODEL  # a mistyped model name must not switch the AI off
    return prov, f"{cfg['provider']}:{model or ''}"


@app.post("/api/settings/ai/test")
def test_ai(owner: str = Depends(session)) -> dict:
    cfg = _ai(owner)
    if cfg["mode"] == "off":
        return {"ok": True, "message": "ИИ выключен: вопросы разбираются встроенными правилами."}
    try:
        prov, _ = _provider_for(cfg, owner)
        models = prov.list_models()
    except HTTPException as exc:
        return {"ok": False, "message": exc.detail}
    except ProviderError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception:  # network errors etc.
        return {"ok": False, "message": "Не удалось подключиться к провайдеру"}
    if cfg.get("model") and models and cfg["model"] not in models:
        return {"ok": False, "message": f"Модель «{cfg['model']}» не найдена у провайдера", "models": models[:200]}
    return {"ok": True, "message": "Подключение работает", "models": models[:200]}


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    jurisdiction: str | None = None


@app.post("/api/files/{file_id}/ask")
def ask(file_id: str, body: AskIn, owner: str = Depends(session)) -> dict:
    meta = _meta(owner, file_id)
    jur = body.jurisdiction if body.jurisdiction in JURISDICTIONS else None
    cfg = _ai(owner)
    consent_missing = cfg["mode"] == "api" and not cfg.get("consent_external")
    prov, label = (None, None) if consent_missing else _provider_for(cfg, owner)
    tb = Toolbox(_decl(meta), engine, jurisdiction=jur, norms=norms)
    state = _dialog(owner, meta.id)  # per file: no cross-file mixing
    ans = ai_answer(body.question.strip(), tb, state, prov, label)
    if getattr(prov, "switched_from", None):
        ans.model = f"{cfg['provider']}:{prov.model}"
        ans.warnings.insert(0, f"Модель «{prov.switched_from}» у провайдера не найдена, поэтому ответ дала "
                               f"{prov.model}. Исправьте название модели в настройках ИИ.")
    _save_dialog(owner, meta.id, state)
    if consent_missing:
        ans.warnings.insert(0, "ИИ не использовался: в настройках ИИ не дано согласие на передачу фрагментов XML "
                               "внешнему провайдеру. Ответ сформирован без ИИ.")
    out = ans.to_dict()
    store.add_history(meta, "ask", body.question.strip()[:200], {"question": body.question, "mode": ans.mode}, out)
    return out


relay_hub = RelayHub()


def _on_login(anon_owner: str, user_owner: str) -> None:
    """AI settings (incl. a session API key) follow the user into the account on login."""
    if PERSIST_STATE:
        cfg = store.kv_get("ai", anon_owner)
        if cfg and store.kv_get("ai", user_owner) is None:
            store.kv_put("ai", user_owner, cfg)
        if anon_owner in _keys:
            _keys[user_owner] = _keys.pop(anon_owner)
        return
    with _settings_lock:
        if anon_owner in _settings and user_owner not in _settings:
            cfg = dict(_settings.pop(anon_owner))
            cfg["_owner"] = user_owner
            _settings[user_owner] = cfg


auth.on_login[:] = [cb for cb in auth.on_login if cb.__name__ != "_on_login"]  # module reloads in tests
auth.on_login.append(_on_login)


@app.websocket("/api/relay")
async def relay(ws: WebSocket) -> None:
    """Browser side of the local-model relay (see xmldt/ai/relay.py)."""
    origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
    if not origin or origin.split("://", 1)[-1] != host:  # refuse cross-site WebSocket hijacking
        await ws.close(code=4403)
        return
    user = auth.user_from_request(ws)
    if user:
        owner = auth.user_owner(user[0])
    else:
        token = ws.cookies.get(auth.ANON_COOKIE)
        if not token or len(token) > 64:
            await ws.close(code=4401)
            return
        owner = auth.owner_key(token)
    await ws.accept()
    conn = Connection(send=ws.send_json, loop=asyncio.get_running_loop())
    relay_hub.attach(owner, conn)
    try:
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") != "ping":
                relay_hub.resolve(owner, conn, msg)
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        relay_hub.detach(owner, conn)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # never leak internals
    return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)


store.purge_expired()
