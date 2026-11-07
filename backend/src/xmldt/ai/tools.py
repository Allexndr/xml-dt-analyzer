"""Read-only tools the question layer may call, and the registry of their results.

Each call result gets an id (r1, r2, …). The model refers to values only through
placeholders like ``{{r1.goods_items}}`` and to sources through result ids; the
server resolves both, so numbers and line references never come from the model.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from xmldt.facts.tools import Declaration, Evidence, ToolResult
from xmldt.rules.engine import Engine

JURISDICTIONS = ["KZ", "RU", "BY", "AM", "KG"]


@dataclass
class CallRecord:
    rid: str
    tool: str
    args: dict
    value: dict
    evidence: list[Evidence]
    warnings: list[str] = field(default_factory=list)


class Toolbox:
    def __init__(self, decl: Declaration, engine: Engine, jurisdiction: str | None = None, norms=None):
        self.norms = norms  # xmldt.kb.provisions.NormIndex or None
        self.decl = decl
        self.engine = engine
        self.jurisdiction = jurisdiction
        self.calls: list[CallRecord] = []
        self._impl: dict[str, Callable[..., ToolResult]] = {
            "count_goods": lambda: decl.count_goods(),
            "marking_stats": lambda: decl.marking_stats(),
            "get_concept": self._get_concept,
            "list_goods": self._list_goods,
            "search_catalog": self._search_catalog,
            "run_checks": self._run_checks,
            "search_norms": self._search_norms,
        }

    # ---- specs for function calling (JSON Schema) ----------------------
    def specs(self) -> list[dict]:
        keys = sorted(self.decl.catalog.concepts)
        return [
            {"name": "count_goods", "description": "Точное число товарных позиций ДТ и количество, заявленное в графе 5.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "marking_stats",
             "description": "Коды маркировки: у каких товаров есть сведения, сколько кодов перечислено, уникальных, в диапазонах, дубли, по каждому товару.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "get_concept",
             "description": "Значения понятия из каталога (отправитель, получатель, процедура, документы графы 44 и т. д.) с источниками в XML. "
                            "Если задан goods_no — только для этого товара.",
             "parameters": {"type": "object", "properties": {
                 "key": {"type": "string", "enum": keys},
                 "goods_no": {"type": "integer", "minimum": 1}}, "required": ["key"], "additionalProperties": False}},
            {"name": "list_goods", "description": "Список товаров: номер, код ТН ВЭД, описание.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "search_catalog",
             "description": "Найти понятия каталога по словам пользователя или номеру графы (например «графа 31», «фактурная стоимость»).",
             "parameters": {"type": "object", "properties": {"query": {"type": "string", "maxLength": 200}},
                            "required": ["query"], "additionalProperties": False}},
            {"name": "search_norms",
             "description": "Найти пункты Порядка заполнения ДТ (Решение КТС № 257) по вопросу о заполнении граф: "
                            "как заполняется графа, что в ней указывается, обязательна ли, особенности процедуры. "
                            "Возвращает ссылки на пункты и их текст.",
             "parameters": {"type": "object", "properties": {
                 "query": {"type": "string", "maxLength": 300},
                 "graph": {"type": "string", "maxLength": 3, "description": "номер графы, если вопрос о графе"}},
                 "required": ["query"], "additionalProperties": False}},
            {"name": "run_checks",
             "description": "Проверить ДТ по автоматизированным правилам (структура ДТ, классификаторы, согласованность). "
                            "Возвращает сводку и нарушения с нормативными ссылками.",
             "parameters": {"type": "object", "properties": {"jurisdiction": {"type": "string", "enum": JURISDICTIONS}},
                            "additionalProperties": False}},
        ]

    # ---- execution ------------------------------------------------------
    def call(self, name: str, args: dict | None) -> CallRecord:
        args = dict(args or {})
        if name not in self._impl:
            raise KeyError(f"неизвестный инструмент {name}")
        for prev in self.calls:  # identical call: reuse the existing result id (deterministic tools)
            if prev.tool == name and prev.args == args:
                return prev
        res = self._impl[name](**args)
        rec = CallRecord(f"r{len(self.calls) + 1}", name, args, res.value, res.evidence, res.warnings)
        self.calls.append(rec)
        return rec

    def get(self, rid: str) -> CallRecord | None:
        return next((c for c in self.calls if c.rid == rid), None)

    @staticmethod
    def for_model(rec: CallRecord, max_chars: int = 6000) -> dict:
        """Compact view of a result for the model: values and evidence labels (data, not instructions)."""
        import json

        out = {"result_id": rec.rid, "tool": rec.tool, "value": rec.value,
               "evidence": [{"index": i, "label": e.label, "lines": f"{e.line_start}-{e.line_end}"}
                            for i, e in enumerate(rec.evidence[:40])],
               "warnings": rec.warnings}
        s = json.dumps(out, ensure_ascii=False)
        if len(s) > max_chars:  # keep within context; values stay addressable by placeholders
            out["value"] = {k: v for k, v in rec.value.items() if not isinstance(v, (list, dict))}
            out["truncated"] = True
        return out

    # ---- tool implementations ----------------------------------------
    def _get_concept(self, key: str, goods_no: int | None = None) -> ToolResult:
        if key not in self.decl.catalog.concepts:
            raise KeyError(f"неизвестное понятие {key}")
        r = self.decl.get_concept(key, goods_no)
        # flat, placeholder-friendly view of the values
        r.value["text_values"] = [
            "; ".join(f["value"] for f in v["fields"] if f["value"]) for v in r.value["values"]
        ]
        return r

    def _list_goods(self) -> ToolResult:
        d = self.decl
        rows, ev = [], []
        for it in d.items():
            ordn = d.child(it, "ConsignmentItemOrdinal")
            hs = d.child(it, "CommodityCode")
            desc = " ".join(x.value or "" for x in d.childs(it, "GoodsDescriptionText"))
            no = ordn.value if ordn else str(it.goods_no)
            rows.append({"goods_no": no, "hs_code": hs.value if hs else None, "description": desc})
            ev.append(d.ev(it, f"Товар № {no}: {desc[:60]}"))
        return ToolResult("list_goods", {"count": len(rows), "goods": rows}, ev)

    def _search_catalog(self, query: str) -> ToolResult:
        hits = search_catalog(self.decl.catalog, query)[:8]
        matches, ev = [], []
        for c in hits:
            nodes = [n for p in c.paths for n in self.decl.by_spath.get(p, [])]
            tags = sorted({n.qname for n in nodes})
            matches.append({"key": c.key, "title": c.title, "graphs": c.graphs, "level": c.level,
                            "xml_paths": c.paths, "tags_in_file": tags, "occurrences_in_file": len(nodes),
                            "pitfalls": c.pitfalls})
            # where the best match occurs in the uploaded file; weaker matches stay listed
            # without sources so they do not leak unrelated lines into the answer
            if c is hits[0]:
                ev += [self.decl.ev(n, f"{c.title}: элемент {n.qname} в файле") for n in nodes[:3]]
        return ToolResult("search_catalog", {"query": query, "matches": matches}, ev)

    def _search_norms(self, query: str, graph: str | None = None) -> ToolResult:
        from xmldt.kb.provisions import excerpt

        if self.norms is None:
            return ToolResult("search_norms", {"query": query, "provisions": [], "note": "нормативная база не подключена"}, [])
        hits = self.norms.search(query, graph=graph, k=3)
        provs = [{"id": p.id, "ref": p.ref, "graph": p.graph, "as_of": p.as_of,
                  "edition_note": "дата редакции требует подтверждения" if p.ambiguous_edition else None,
                  "text": excerpt(p, query)} for _, p in hits]
        return ToolResult("search_norms", {"query": query, "graph": graph, "provisions": provs}, [])

    def _run_checks(self, jurisdiction: str | None = None) -> ToolResult:
        jur = jurisdiction or self.jurisdiction
        rep = self.engine.run(self.decl, jurisdiction=jur, check_date=_today())
        problems = [r for r in rep.results if r.status in ("violation", "needs_info")]
        ev = [e for r in problems for f in r.findings for e in f.evidence[:3]][:40]
        return ToolResult("run_checks", {
            "jurisdiction": jur, "summary": rep.summary(),
            "violations": [{"rule_id": r.rule_id, "status": r.status, "severity": r.severity, "rule": r.text,
                            "legal_ref": r.legal_ref, "findings": [f.message for f in r.findings[:5]]} for r in problems],
        }, ev)


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)


def _stem(w: str) -> str:
    return w[:6] if len(w) > 6 else w


def search_catalog(catalog, query: str):
    """Keyword search over titles and synonyms with crude Russian stemming; 'графа N' matches graphs."""
    q = query.lower()
    scored = []
    graph = re.search(r"граф\w*\s*(\d{1,2}|[абв])\b", q)
    qstems = {_stem(w) for w in _WORD.findall(q) if len(w) > 2}
    for c in catalog.concepts.values():
        s = 0.0
        if graph and any(g.split(".")[0] == graph.group(1).upper() or g.split(".")[0] == graph.group(1) for g in c.graphs):
            s += 3
        for syn in [c.title, *c.synonyms]:
            sl = syn.lower()
            if sl in q:
                s += 2 + len(sl) / 40
            st = {_stem(w) for w in _WORD.findall(sl) if len(w) > 2}
            if st and st <= qstems:
                s += 1
        if s:
            scored.append((s, c))
    return [c for s, c in sorted(scored, key=lambda x: -x[0])]


def result_dict(rec: CallRecord) -> dict:
    return {"rid": rec.rid, "tool": rec.tool, "args": rec.args, "value": rec.value,
            "evidence": [asdict(e) for e in rec.evidence], "warnings": rec.warnings}


def resolve_path(value: Any, path: str) -> Any:
    """Resolve a.b.0.c (also a[0].b); dict keys may themselves contain dots, e.g. a marking code."""
    path = re.sub(r"\[(\d+)\]", r".\1", path.strip())
    if path.startswith("value."):  # models often mirror the tool output shape {"value": {...}}
        path = path[len("value."):]
    parts = [p for p in path.split(".") if p != ""]
    cur = value
    i = 0
    while i < len(parts):
        part = parts[i]
        if isinstance(cur, dict):
            # longest key made of the remaining parts that exists (keys like "(01)0460….x02")
            for j in range(len(parts), i, -1):
                key = ".".join(parts[i:j])
                if key in cur:
                    cur, i = cur[key], j
                    break
            else:
                raise KeyError(path)
            continue
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, list) and part == "count":
            cur = len(cur)
        else:
            raise KeyError(path)
        i += 1
    return cur


def fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, list):
        return ", ".join(fmt(x) for x in v) if v else "—"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {fmt(x)}" for k, x in v.items()) if v else "—"
    if v is None:
        return "—"
    return str(v)
