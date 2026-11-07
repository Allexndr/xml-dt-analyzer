"""Question layer: deterministic router, LLM loop with a scripted model, verifier, providers, API."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from xmldt.ai import providers as prov_mod
from xmldt.ai.orchestrator import DialogState, answer
from xmldt.ai.providers import ProviderError, Reply, ToolCall, check_url, make_provider
from xmldt.ai.tools import Toolbox
from xmldt.facts.tools import Declaration
from xmldt.indexer import index_xml
from xmldt.kb.classifiers import load_all
from xmldt.kb.concepts import load_catalog
from xmldt.rules.compiler import load_bindings, load_expert, load_rules
from xmldt.rules.engine import Engine

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"
pytestmark = pytest.mark.skipif(not (DATA / "derived" / "r055_1.2.0_rules_compiled.json").exists(),
                                reason="run xmldt.kb.build first")


@pytest.fixture(scope="module")
def engine():
    return Engine(load_rules(DATA / "derived" / "r055_1.2.0_rules_compiled.json"),
                  load_bindings(DATA / "kb" / "classifier_bindings_R.055_1.2.0.yaml"),
                  load_all(DATA / "derived"), expert=load_expert(DATA / "kb" / "expert_rules_R.055_1.2.0.yaml"))


@pytest.fixture(scope="module")
def decl():
    return Declaration(index_xml(SAMPLE.read_bytes()), load_catalog(DATA / "kb" / "concepts_R.055_1.2.0.yaml"))


def tb(decl, engine):
    return Toolbox(decl, engine, jurisdiction="KZ")


# ---- rules mode -----------------------------------------------------------
def test_reference_question_of_the_spec(decl, engine):
    a = answer("Сколько товаров в декларации и у скольких есть коды маркировки?", tb(decl, engine), DialogState())
    assert a.mode == "rules" and not a.unsupported
    assert [c["tool"] for c in a.tool_calls] == ["count_goods", "marking_stats"]
    assert "3 товарные позиции" in a.summary and "у 2 товаров" in a.summary
    assert any("№ 2" in f.text and "нет" in f.text for f in a.facts)
    assert all(f.evidence for f in a.facts if "уникальных" in f.text)


def test_follow_up_uses_dialog_state(decl, engine):
    st = DialogState()
    answer("Сколько товаров?", tb(decl, engine), st)
    a = answer("А у каких из них указаны коды маркировки?", tb(decl, engine), st)
    assert [c["tool"] for c in a.tool_calls] == ["marking_stats"]


@pytest.mark.parametrize(
    "q,key",
    [("Кто отправитель?", "consignor"), ("Кто получатель?", "consignee"), ("Какие документы в графе 44?", "presented_documents"),
     ("Какая таможенная процедура заявлена?", "customs_procedure"), ("Какие коды ТН ВЭД?", "hs_code"),
     ("Какая валюта и сумма по счёту?", "invoice_amount_currency")],
)
def test_concept_questions(decl, engine, q, key):
    a = answer(q, tb(decl, engine), DialogState())
    assert a.tool_calls and a.tool_calls[0] == {"tool": "get_concept", "args": {"key": key}}


def test_consignee_equal_indicator_warning(decl, engine):
    a = answer("Кто получатель?", tb(decl, engine), DialogState())
    assert any("совпадения" in w for w in a.warnings)


def test_checks_question(decl, engine):
    a = answer("Проверь декларацию на ошибки", tb(decl, engine), DialogState())
    assert a.tool_calls[0]["tool"] == "run_checks" and "Полная проверка ДТ не выполнялась" in a.summary


def test_unsupported(decl, engine):
    a = answer("Какая сегодня погода?", tb(decl, engine), DialogState())
    assert a.unsupported and "Сколько товаров" in a.summary


# ---- LLM mode with a scripted model --------------------------------------
class Scripted:
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools):
        self.seen.append(messages)
        return self.replies.pop(0)

    def list_models(self):
        return ["m"]


def final(d):
    return Reply(json.dumps(d, ensure_ascii=False))


def test_llm_placeholders_resolved_and_verified(decl, engine):
    m = Scripted([
        Reply("", [ToolCall("c1", "count_goods", {}), ToolCall("c2", "marking_stats", {})]),
        final({"summary": "В декларации {{r1.goods_items}} товарных позиции, коды есть у {{r2.items_with_marking_data}}.",
               "facts": [{"text": "Без маркировки: товар № {{r2.items_without_marking_data_list}}", "kind": "xml_fact",
                          "evidence": ["r2"]}], "interpretation": None}),
    ])
    a = answer("сколько товаров и маркировка?", tb(decl, engine), DialogState(), m, "scripted:m")
    assert a.mode == "llm" and a.verified
    assert a.summary == "В декларации 3 товарных позиции, коды есть у 2."
    assert a.facts[0].text.endswith("№ 2") and a.facts[0].evidence
    tool_msg = [x for x in m.seen[1] if x["role"] == "tool"][0]
    assert json.loads(tool_msg["content"])["result_id"] == "r1"


def test_llm_invented_number_is_rejected_then_fallback(decl, engine):
    bad = final({"summary": "В декларации 5 товаров.", "facts": []})
    m = Scripted([Reply("", [ToolCall("c1", "count_goods", {})]), bad, bad])
    a = answer("Сколько товаров?", tb(decl, engine), DialogState(), m, "scripted:m")
    assert not a.verified and a.mode == "rules" and "3 товарные позиции" in a.summary
    assert "не прошёл проверку" in a.warnings[0]
    retry_msg = m.seen[2][-1]["content"]
    assert "число «5»" in retry_msg


def test_llm_invented_tag_and_evidence_rejected(decl, engine):
    bad = final({"summary": "Отправитель указан в элементе SenderCompanyInfo.",
                 "facts": [{"text": "x", "kind": "xml_fact", "evidence": ["r9"]}]})
    good = final({"summary": "Отправитель: {{r1.text_values.0}}.",
                  "facts": [{"text": "{{r1.text_values.0}}", "kind": "xml_fact", "evidence": ["r1:0"]}]})
    m = Scripted([Reply("", [ToolCall("c1", "get_concept", {"key": "consignor"})]), bad, good])
    a = answer("Кто отправитель?", tb(decl, engine), DialogState(), m, "scripted:m")
    assert a.verified and a.summary.startswith("Отправитель: Example Trading")
    assert "SenderCompanyInfo" in m.seen[2][-1]["content"] and "r9" in m.seen[2][-1]["content"]


def test_llm_norm_claim_requires_checks(decl, engine):
    bad = final({"summary": "Ответ.", "facts": [{"text": "Графа обязательна по Решению", "kind": "filling_rule", "evidence": []}]})
    m = Scripted([bad, bad])
    a = answer("Обязательна ли графа 8?", tb(decl, engine), DialogState(), m, "scripted:m")
    assert not a.verified


def test_provider_error_falls_back_to_rules(decl, engine):
    class Down(Scripted):
        def chat(self, messages, tools):
            raise ProviderError("timeout")

    a = answer("Сколько товаров?", tb(decl, engine), DialogState(), Down([]), "x")
    assert a.mode == "rules" and "ИИ недоступен" in a.warnings[0]


# ---- providers ------------------------------------------------------------
def test_ssrf_guard():
    with pytest.raises(ProviderError):
        check_url("http://127.0.0.1:11434/v1", allow_private=False)
    with pytest.raises(ProviderError):
        check_url("https://localhost/v1", allow_private=False)
    with pytest.raises(ProviderError):
        check_url("file:///etc/passwd", allow_private=True)
    assert check_url("http://127.0.0.1:11434/v1", allow_private=True)


def _mock(handler, monkeypatch):
    monkeypatch.setattr(prov_mod, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def test_openai_compatible_wire_format(monkeypatch):
    sent = {}

    def handler(req):
        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "count_goods", "arguments": "{}"}}]}}]})

    _mock(handler, monkeypatch)
    p = make_provider("ollama", base_url=None, model="qwen", api_key=None, allow_private=True)
    r = p.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "t0", "name": "list_goods", "args": {}}]},
                {"role": "tool", "tool_call_id": "t0", "content": "{}"}], [{"name": "count_goods", "description": "d",
                                                                            "parameters": {"type": "object"}}])
    assert r.tool_calls[0].name == "count_goods"
    assert sent["messages"][2]["tool_calls"][0]["function"]["name"] == "list_goods"
    assert sent["tools"][0]["type"] == "function" and sent["temperature"] == 0


def test_anthropic_wire_format(monkeypatch):
    sent = {}

    def handler(req):
        sent.update(json.loads(req.content))
        assert req.headers["x-api-key"] == "k"
        return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "u1", "name": "marking_stats", "input": {}}]})

    _mock(handler, monkeypatch)
    monkeypatch.setattr(prov_mod, "check_url", lambda url, allow_private: url)
    p = make_provider("anthropic", base_url=None, model="claude-sonnet-5-5", api_key="k", allow_private=False)
    r = p.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "u0", "name": "count_goods", "args": {}}]},
                {"role": "tool", "tool_call_id": "u0", "content": "{}"}],
               [{"name": "count_goods", "description": "d", "parameters": {"type": "object"}}])
    assert r.tool_calls[0].name == "marking_stats"
    assert sent["system"] == "s" and sent["messages"][2]["content"][0]["type"] == "tool_result"
    assert sent["tools"][0]["input_schema"] == {"type": "object"}


# ---- API ------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XMLDT_VAR", str(tmp_path))
    import xmldt.api.app as appmod
    return TestClient(importlib.reload(appmod).app)


def test_api_ask_rules_mode_and_history(client):
    fid = client.post("/api/files", files={"file": ("dt.xml", SAMPLE.read_bytes(), "text/xml")}).json()["id"]
    r = client.post(f"/api/files/{fid}/ask", json={"question": "Сколько товаров?"}).json()
    assert r["mode"] == "rules" and "3 товарные позиции" in r["summary"]
    assert r["facts"][0]["evidence"][0]["line_start"] == 40
    assert client.get(f"/api/files/{fid}/history").json()[0]["kind"] == "ask"


def test_api_settings_never_return_key_and_require_consent(client):
    s = client.put("/api/settings/ai", json={"mode": "api", "provider": "openai", "model": "m", "api_key": "sk-secret"}).json()
    assert s["has_api_key"] is True and "api_key" not in s and "sk-secret" not in json.dumps(s)
    fid = client.post("/api/files", files={"file": ("dt.xml", SAMPLE.read_bytes(), "text/xml")}).json()["id"]
    r = client.post(f"/api/files/{fid}/ask", json={"question": "Сколько товаров?"}).json()
    assert r["mode"] == "rules" and "согласие" in r["warnings"][0]
    s = client.put("/api/settings/ai", json={"api_key": None}).json()
    assert s["has_api_key"] is False


def test_api_local_mode_refused_in_cloud(client):
    client.put("/api/settings/ai", json={"mode": "local", "provider": "ollama"})
    assert client.post("/api/settings/ai/test").json()["ok"] is False


def test_placeholder_variants_and_leftovers(decl, engine):
    from xmldt.ai.tools import resolve_path

    v = {"goods": [{"no": "1"}], "dups": {"(01)0460.x02": 2}}
    assert resolve_path(v, "goods[0].no") == "1" and resolve_path(v, "value.goods.0.no") == "1"
    assert resolve_path(v, "dups.(01)0460.x02") == 2
    bad = final({"summary": "Отправитель — {{r1.text_values.0}, ул.", "facts": []})
    good = final({"summary": "Отправитель — {{r1.text_values.0}}.", "facts": []})
    m = Scripted([Reply("", [ToolCall("c1", "get_concept", {"key": "consignor"})]), bad, good])
    a = answer("Кто отправитель?", tb(decl, engine), DialogState(), m, "s")
    assert a.verified and "{" not in a.summary and "незакрытый" in m.seen[2][-1]["content"]


# ---- normative answers (Decision No. 257) ------------------------------------
@pytest.fixture(scope="module")
def norms():
    from xmldt.kb.provisions import NormIndex

    return NormIndex.load(sorted((DATA / "derived").glob("decision257_provisions_*.json"))[-1])


def test_norm_search_finds_graph_subpoint(norms):
    hits = norms.search("Как заполняется графа 31?", k=1)
    assert hits[0][1].point == "15" and hits[0][1].graph == "31"
    assert "подп. 29" in hits[0][1].ref


def test_rules_mode_norm_question(decl, engine, norms):
    a = answer("Как правильно заполняется графа 8?", Toolbox(decl, engine, "KZ", norms), DialogState())
    assert a.tool_calls[0]["tool"] == "search_norms"
    assert a.facts and a.facts[0].kind == "filling_rule" and "графа 8" in a.facts[0].sources[0]["ref"]


def test_llm_fake_quote_is_rejected(decl, engine, norms):
    bad = final({"summary": "См. пункт.", "facts": [{"text": "В Порядке сказано: «графа 8 заполняется только красными чернилами»",
                                                     "kind": "filling_rule", "evidence": ["r1:0"]}]})
    m = Scripted([Reply("", [ToolCall("c1", "search_norms", {"query": "графа 8", "graph": "8"})]), bad, bad])
    a = answer("Как заполняется графа 8?", Toolbox(decl, engine, "KZ", norms), DialogState(), m, "s")
    assert not a.verified and "цитата" in m.seen[2][-1]["content"]


def test_llm_norm_fact_with_real_quote_passes(decl, engine, norms):
    hit = norms.search("графа 8", graph="8", k=3)[0][1]
    quote = next(line for line in hit.text.split("\n")[1:] if len(line) > 30)[:60]
    good = final({"summary": "Ответ по Порядку.", "facts": [{"text": f"В Порядке: «{quote}»", "kind": "filling_rule",
                                                             "evidence": ["r1:0"]}]})
    m = Scripted([Reply("", [ToolCall("c1", "search_norms", {"query": "графа 8", "graph": "8"})]), good])
    a = answer("Как заполняется графа 8?", Toolbox(decl, engine, "KZ", norms), DialogState(), m, "s")
    assert a.verified and a.facts[0].sources and a.facts[0].sources[0]["ref"].startswith("Решение Комиссии")
