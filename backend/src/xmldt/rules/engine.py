"""Rule engine: run compiled format rules, classifier checks and consistency checks.

Statuses (per ТЗ 5.4/5.5 — absence is not automatically an error):
    violation        — confirmed by the data and the rule
    needs_info       — the data present is not enough to decide
    pass             — the rule applied and was satisfied
    not_applicable   — the rule's subject is absent (e.g. parent element not filled)
    not_implemented  — the rule exists in the source but is not automated yet
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from xmldt.facts.tools import Declaration, Evidence
from xmldt.indexer import Node
from xmldt.kb.classifiers import Classifier
from xmldt.rules.compiler import Binding, Rule
from xmldt.rules.conditional import CondRule, evaluate

_IDX = re.compile(r"\[\d+\]")
MAX_FINDINGS = 50


@dataclass
class Finding:
    message: str
    value: str | None
    evidence: list[Evidence]


@dataclass
class Result:
    rule_id: str
    kind: str
    status: str
    severity: str
    path: str
    legal_ref: dict
    text: str
    findings: list[Finding] = field(default_factory=list)


@dataclass
class Report:
    jurisdiction: str | None
    check_date: str
    results: list[Result]

    def summary(self) -> dict:
        st = Counter(r.status for r in self.results)
        viol = Counter(r.severity for r in self.results if r.status == "violation")
        checks = [r for r in self.results if r.kind not in ("informational", "covered")]
        return {"rules": len(checks), "by_status": dict(Counter(r.status for r in checks)),
                "violations_by_severity": dict(viol),
                "automated": sum(1 for r in checks if r.status != "not_implemented"),
                "informational": sum(1 for r in self.results if r.kind == "informational"),
                "ai_expert_rules": sum(1 for r in checks if r.kind in ("conditional", "expert"))}


class Engine:
    def __init__(self, rules: list[Rule], bindings: list[Binding], classifiers: dict[str, Classifier],
                 expert: list[dict] | None = None):
        self.rules = rules
        self.expert = [CondRule.from_dict(d) for d in (expert or [])]
        self.legal_by_id = {r.id: r.legal_ref for r in rules}
        self.bindings = bindings
        self.classifiers = classifiers
        self.extra_allow: dict[str, list[str]] = defaultdict(list)
        for r in rules:
            if r.kind == "classifier_extra":
                self.extra_allow[r.path].extend(r.params["allow"])

    # ---- helpers ------------------------------------------------------
    @staticmethod
    def _spath(n: Node) -> str:
        return n.spath

    def _label(self, d: Declaration, n: Node) -> str:
        return n.qname + (f", товар № {n.goods_no}" if n.goods_no else "")

    def _res(self, r: Rule, status: str, findings=None) -> Result:
        return Result(r.id, r.kind, status, r.severity, r.path, r.legal_ref, r.text, findings or [])

    # ---- format rules (table 3) ---------------------------------------
    def _run_rule(self, d: Declaration, r: Rule, jurisdiction: str | None) -> Result:
        nodes = d.by_spath.get(r.path, [])
        parent_path = r.path.rsplit("/", 1)[0]
        if r.kind == "manual":
            return self._res(r, "not_implemented")
        if r.kind in ("informational", "covered"):
            return self._res(r, "not_applicable")
        if r.kind == "conditional":
            return self._cond(d, CondRule.from_dict(r.params["dsl"]), r.legal_ref, r.text, jurisdiction, r.id)
        if r.kind == "classifier_extra":
            return self._res(r, "pass" if nodes else "not_applicable")  # merged into classifier checks
        if r.kind == "not_filled":
            if not nodes:
                return self._res(r, "pass")
            return self._res(r, "violation", [Finding("Реквизит заполнен, хотя по структуре ДТ он не заполняется.",
                                                      n.value, [d.ev(n, self._label(d, n))]) for n in nodes[:MAX_FINDINGS]])
        if r.kind == "required":
            parents = d.by_spath.get(parent_path, [])
            if not parents:
                return self._res(r, "not_applicable")
            local = r.path.rsplit("/", 1)[1]
            missing = [p for p in parents if d.child(p, local) is None]
            if not missing:
                return self._res(r, "pass")
            return self._res(r, "violation", [Finding(f"Не заполнен обязательный реквизит {local}.", None,
                                                      [d.ev(p, self._label(d, p))]) for p in missing[:MAX_FINDINGS]])
        if r.kind in ("allowed_values", "pattern"):
            if not nodes:
                return self._res(r, "not_applicable")
            bad = []
            for n in nodes:
                v = (n.value or "").strip()
                ok = v in r.params["values"] if r.kind == "allowed_values" else re.fullmatch(r.params["regex"], v) is not None
                if not ok:
                    bad.append(n)
            if not bad:
                return self._res(r, "pass")
            expected = (", ".join(f"«{x}»" for x in r.params["values"]) if r.kind == "allowed_values"
                        else f"шаблон {r.params['regex']}")
            return self._res(r, "violation", [Finding(f"Значение «{n.value}» недопустимо, ожидается {expected}.",
                                                      n.value, [d.ev(n, self._label(d, n))]) for n in bad[:MAX_FINDINGS]])
        if r.kind == "country_scope":
            if not nodes:
                return self._res(r, "not_applicable")
            if jurisdiction is None:
                return self._res(r, "needs_info", [Finding(
                    "Реквизит используется не во всех государствах-членах; не задана страна, для которой выполняется проверка.",
                    None, [d.ev(nodes[0], self._label(d, nodes[0]))])])
            if jurisdiction in r.params["countries"]:
                return self._res(r, "pass")
            return self._res(r, "violation", [Finding(
                f"Реквизит используется только в: {', '.join(r.params['countries'])}; проверка выполняется для {jurisdiction}.",
                n.value, [d.ev(n, self._label(d, n))]) for n in nodes[:MAX_FINDINGS]])
        if r.kind == "single_instance":
            parents = d.by_spath.get(parent_path, [])
            local = r.path.rsplit("/", 1)[1]
            over = [p for p in parents if len(d.childs(p, local)) > 1]
            if not nodes:
                return self._res(r, "not_applicable")
            if not over:
                return self._res(r, "pass")
            return self._res(r, "violation", [Finding(f"Реквизит {local} указан более одного раза.", None,
                                                      [d.ev(c, self._label(d, c)) for c in d.childs(p, local)]) for p in over])
        if r.kind == "one_of_children":
            if not nodes:
                return self._res(r, "not_applicable")
            bad = [n for n in nodes if not any(d.child(n, c) is not None for c in r.params["children"])]
            if not bad:
                return self._res(r, "pass")
            # A subject marked "same as another person" legitimately has no name.
            really_bad = [n for n in bad if d.child(n, "EqualIndicator") is None]
            if not really_bad:
                return self._res(r, "not_applicable")
            return self._res(r, "violation", [Finding(f"Не указано ни одно из: {', '.join(r.params['children'])}.", None,
                                                      [d.ev(n, self._label(d, n))]) for n in really_bad[:MAX_FINDINGS]])
        return self._res(r, "not_implemented")

    # ---- classifier checks --------------------------------------------
    def _classifier_results(self, d: Declaration) -> list[Result]:
        out: list[Result] = []
        groups: dict[tuple, list[tuple[Node, str]]] = defaultdict(list)
        for n in d.nodes:
            sp = self._spath(n)
            for b in self.bindings:
                if b.within and b.within not in sp:
                    continue
                if b.element and n.local_name == b.element and n.value:
                    groups[(b.classifier, sp, None, b.external, tuple(b.allow))].append((n, n.value.strip()))
                elif b.attribute and b.attribute in n.attrs:
                    groups[(b.classifier, sp, b.attribute, b.external, tuple(b.allow))].append((n, n.attrs[b.attribute].strip()))
        for (ckey, sp, attr, external, allow), items in sorted(groups.items(), key=lambda kv: kv[0][1]):
            target = sp + (f"/@{attr}" if attr else "")
            rid = f"CL-{ckey}-{target.rsplit('/', 2)[-2]}/{target.rsplit('/', 1)[-1]}"
            cls = self.classifiers.get(ckey)
            ref = {"doc": "Решение Комиссии Таможенного союза № 378", "classifier": ckey,
                   "as_of": cls.source.get("as_of") if cls else None}
            text = f"Значение должно соответствовать классификатору «{cls.title if cls else ckey}»."
            res = Result(rid, "classifier", "pass", "error", target, ref, text)
            if external or cls is None:
                res.status = "not_implemented"
                res.text = f"Проверка по справочнику «{ckey}» не подключена (справочник вне Решения № 378)."
                out.append(res)
                continue
            extra = set(allow) | set(self.extra_allow.get(sp, []))
            for n, v in items:
                if v in extra:
                    continue
                it = cls.lookup(v)
                if it is None:
                    res.findings.append(Finding(f"Код «{v}» отсутствует в классификаторе «{cls.title}».", v,
                                                [d.ev(n, self._label(d, n))]))
                elif it.excluded:
                    res.findings.append(Finding(f"Код «{v}» исключён из классификатора «{cls.title}».", v,
                                                [d.ev(n, self._label(d, n))]))
            if res.findings:
                res.status = "violation"
                res.findings = res.findings[:MAX_FINDINGS]
            out.append(res)
        return out

    # ---- consistency checks (cross-field) -----------------------------
    def _consistency(self, d: Declaration) -> list[Result]:
        basis = {"basis": "проверка согласованности сведений; нормативное основание подтверждает эксперт"}
        out = []
        g = d.count_goods()
        r = Result("CS-goods-quantity", "consistency", "pass", "error", "/GoodsDeclaration/GoodsQuantity", basis,
                   "Количество товаров в графе 5 должно совпадать с числом товарных позиций.")
        if g.value["declared_goods_quantity"] is None:
            r.status = "not_applicable"
        elif g.warnings:
            r.status = "violation"
            r.findings = [Finding(w, None, g.evidence) for w in g.warnings]
        out.append(r)
        m = d.marking_stats()
        dup = Result("CS-marking-duplicates", "consistency", "pass", "error",
                     "/GoodsDeclaration/GDGoodsShipmentDetails/GDGoodsItemDetails/DTIdentificationMeansDetails",
                     basis, "Коды маркировки не должны повторяться.")
        qty = Result("CS-marking-quantity", "consistency", "pass", "warning", dup.path, basis,
                     "Заявленное количество кодов (CIMQuantity) должно совпадать с перечисленными.")
        sign = Result("CS-marking-sign-without-data", "consistency", "pass", "warning", dup.path, basis,
                      "Для товара с признаком маркировки ожидаются сведения о кодах (если маркировка не после выпуска).")
        if not m.value["items_with_marking_data"] and not any("признак маркировки" in w for w in m.warnings):
            dup.status = qty.status = sign.status = "not_applicable"
        for w in m.warnings:
            ev = [e for e in m.evidence if e.label.split(":")[0] in w][:MAX_FINDINGS]
            if "повторяется" in w:
                dup.status = "violation"
                dup.findings.append(Finding(w, None, ev))
            elif "заявлено" in w:
                qty.status = "violation"
                qty.findings.append(Finding(w, None, ev))
            elif "признак маркировки" in w:
                sign.status = "needs_info"
                sign.findings.append(Finding(w, None, ev))
        out += [dup, qty, sign]
        return out

    def _cond(self, d: Declaration, cr: CondRule, legal_ref: dict, text: str, jurisdiction: str | None,
              rule_id: str, kind: str = "conditional") -> Result:
        res = evaluate(cr, d, self.classifiers, jurisdiction)
        ref = dict(legal_ref or {})
        ref["review"] = cr.review
        if cr.note:
            ref["note"] = cr.note
        return Result(rule_id, kind, res.status, cr.severity, cr.scope, ref, text or cr.text,
                      [Finding(m, None, ev) for m, ev in res.findings])

    def run(self, d: Declaration, *, jurisdiction: str | None, check_date: str) -> Report:
        results = [self._run_rule(d, r, jurisdiction) for r in self.rules]
        for cr in self.expert:
            legal = self.legal_by_id.get(cr.source or "", {"basis": "экспертное правило"})
            results.append(self._cond(d, cr, legal, cr.note or cr.text, jurisdiction, cr.id, "expert"))
        results += self._classifier_results(d)
        results += self._consistency(d)
        return Report(jurisdiction, check_date, results)
