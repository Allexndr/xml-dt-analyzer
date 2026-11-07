"""Conditional (cross-requisite) rules: a small declarative DSL, its evaluator, and a parser
that turns the conditional sentences of table 3 (Decision No. 75) into DSL rules.

Rule shape (YAML / dict):

    id: C-T3-19.18.28.4.1-1
    source: T3-19.18.28.4.1-1         # the table-3 sentence this rule formalises
    scope: /GoodsDeclaration/.../DTIdentificationMeansDetails   # evaluated per instance
    countries: [KZ, RU]               # optional: applies only to these jurisdictions
    when: [{eq: [InformationKindCode, "10"]}]
    then: [{absent: IdentificationMeansDetails/AggregationKindCode}]
    else: []                          # optional
    severity: error | warning
    review: ai-expert | approved:<who>
    note: "…"

Paths in conditions are relative to the scope instance ("." is the instance itself);
a path starting with "/" is absolute (document level).

Predicates: present, absent, eq [p, v], in [p, [v…]], matches [p, regex], not_equal [p1, p2],
max_count [p, n], any_present [p…], digits_max [p, n], checksum [p, algo], classifier_name
[code_p, name_p, classifier]. matches/digits_max/checksum are vacuously true when p is absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from xmldt.facts.tools import Declaration, Evidence
from xmldt.indexer import Node

# ---- checksums known to the customs domain -------------------------------------


def kz_bin_iin_valid(v: str) -> bool:
    """Kazakhstan BIN/IIN (12 digits) control digit."""
    if not re.fullmatch(r"\d{12}", v):
        return False
    d = [int(c) for c in v]
    s = sum(d[i] * (i + 1) for i in range(11)) % 11
    if s == 10:
        w = [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]
        s = sum(d[i] * w[i] for i in range(11)) % 11
        if s == 10:
            return False
    return s == d[11]


def ru_inn_valid(v: str) -> bool:
    """Russian INN: 10 digits (organisation) or 12 digits (individual), with control digits."""
    def ctrl(digits, weights):
        return sum(int(a) * b for a, b in zip(digits, weights)) % 11 % 10

    if re.fullmatch(r"\d{10}", v):
        return ctrl(v, [2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(v[9])
    if re.fullmatch(r"\d{12}", v):
        return (ctrl(v, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(v[10])
                and ctrl(v, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(v[11]))
    return False


CHECKSUMS = {"kz_bin_iin": kz_bin_iin_valid, "ru_inn": ru_inn_valid}
DATE = r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])"
DATETIME = DATE + r"T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d{1,3})?([+-]([01]\d|2[0-3]):[0-5]\d|Z)"


# ---- evaluation --------------------------------------------------------------------
@dataclass
class CondRule:
    id: str
    source: str | None
    scope: str
    when: list[dict] = field(default_factory=list)
    then: list[dict] = field(default_factory=list)
    else_: list[dict] = field(default_factory=list)
    countries: list[str] | None = None
    severity: str = "error"
    review: str = "ai-expert"
    note: str = ""
    text: str = ""

    @staticmethod
    def from_dict(d: dict) -> "CondRule":
        return CondRule(id=d["id"], source=d.get("source"), scope=d["scope"], when=d.get("when", []),
                        then=d.get("then", []), else_=d.get("else", []), countries=d.get("countries"),
                        severity=d.get("severity", "error"), review=d.get("review", "ai-expert"),
                        note=d.get("note", ""), text=d.get("text", ""))

    def to_dict(self) -> dict:
        d = {"id": self.id, "source": self.source, "scope": self.scope, "when": self.when, "then": self.then,
             "else": self.else_, "countries": self.countries, "severity": self.severity, "review": self.review,
             "note": self.note, "text": self.text}
        return {k: v for k, v in d.items() if v not in (None, [], "")}


class Ctx:
    def __init__(self, decl: Declaration, classifiers: dict):
        self.d = decl
        self.classifiers = classifiers

    def nodes(self, base: Node, rel: str) -> list[Node]:
        if rel.startswith("/"):
            return list(self.d.by_spath.get(rel, []))
        if rel in (".", ""):
            return [base]
        cur = [base]
        for part in rel.split("/"):
            nxt = []
            for n in cur:
                if part == "..":
                    if n.parent is not None:
                        nxt.append(n.parent)
                else:
                    nxt.extend(self.d.childs(n, part))
            cur = nxt
        return cur


def _val(n: Node) -> str:
    return (n.value or "").strip()


def holds(ctx: Ctx, base: Node, cond: dict, hits: list[Node]) -> bool:
    """Evaluate one predicate; append nodes that explain a failure to ``hits``."""
    (op, arg), = cond.items()
    if op == "present":
        return bool(ctx.nodes(base, arg))
    if op == "absent":
        ns = ctx.nodes(base, arg)
        hits.extend(ns)
        return not ns
    if op == "any_present":
        return any(ctx.nodes(base, p) for p in arg)
    if op in ("eq", "in"):
        p, v = arg
        allowed = [v] if op == "eq" else v
        ns = ctx.nodes(base, p)
        if not ns:
            return False
        bad = [n for n in ns if _val(n) not in allowed]
        hits.extend(bad)
        return not bad
    if op == "matches":
        p, rx = arg
        bad = [n for n in ctx.nodes(base, p) if not re.fullmatch(rx, _val(n))]
        hits.extend(bad)
        return not bad
    if op == "digits_max":
        p, k = arg
        bad = [n for n in ctx.nodes(base, p) if len(re.sub(r"\D", "", _val(n))) > k]
        hits.extend(bad)
        return not bad
    if op == "checksum":
        p, algo = arg
        bad = [n for n in ctx.nodes(base, p) if not CHECKSUMS[algo](_val(n))]
        hits.extend(bad)
        return not bad
    if op == "not_equal":
        a, b = (ctx.nodes(base, x) for x in arg)
        if a and b and _val(a[0]).casefold() == _val(b[0]).casefold():
            hits.extend(a[:1])
            return False
        return True
    if op == "max_count":
        p, k = arg
        ns = ctx.nodes(base, p)
        if len(ns) > k:
            hits.extend(ns)
            return False
        return True
    if op == "classifier_name":
        code_p, name_p, key = arg
        codes, names = ctx.nodes(base, code_p), ctx.nodes(base, name_p)
        cls = ctx.classifiers.get(key)
        if not codes or not names or cls is None:
            return True
        it = cls.lookup(_val(codes[0]))
        if it and it.name.casefold() != _val(names[0]).casefold():
            hits.extend(names[:1])
            return False
        return True
    if op == "not":
        return not holds(ctx, base, arg, [])
    raise ValueError(f"unknown predicate {op}")


def describe(cond: dict) -> str:
    (op, arg), = cond.items()
    name = lambda p: p.rsplit("/", 1)[-1]  # noqa: E731
    return {
        "present": lambda: f"{name(arg)} заполнен",
        "absent": lambda: f"{name(arg)} не заполнен",
        "any_present": lambda: "заполнен один из: " + ", ".join(name(p) for p in arg),
        "eq": lambda: f"{name(arg[0])} = «{arg[1]}»",
        "in": lambda: f"{name(arg[0])} ∈ {{{', '.join(arg[1])}}}",
        "matches": lambda: f"{name(arg[0])} соответствует формату",
        "digits_max": lambda: f"в {name(arg[0])} не более {arg[1]} цифр",
        "checksum": lambda: f"контрольная сумма {name(arg[0])} верна",
        "not_equal": lambda: f"{name(arg[0])} отличается от {name(arg[1])}",
        "max_count": lambda: f"{name(arg[0])} не более {arg[1]} раз",
        "classifier_name": lambda: f"{name(arg[1])} соответствует классификатору",
        "not": lambda: "не (" + describe(arg) + ")",
    }[op]()


@dataclass
class CondResult:
    status: str
    findings: list[tuple[str, list[Evidence]]]


def evaluate(rule: CondRule, decl: Declaration, classifiers: dict, jurisdiction: str | None,
             max_findings: int = 50) -> CondResult:
    if rule.countries:
        if jurisdiction is None:
            if decl.by_spath.get(rule.scope):
                return CondResult("needs_info", [("Правило зависит от государства; страна проверки не задана.", [])])
            return CondResult("not_applicable", [])
        if jurisdiction not in rule.countries:
            return CondResult("not_applicable", [])
    ctx = Ctx(decl, classifiers)
    instances = decl.by_spath.get(rule.scope, [])
    if not instances:
        return CondResult("not_applicable", [])
    evaluated, findings = 0, []
    for inst in instances:
        if all(holds(ctx, inst, c, []) for c in rule.when):
            checks = rule.then
        elif rule.else_:
            checks = rule.else_
        else:
            continue
        evaluated += 1
        for c in checks:
            hits: list[Node] = []
            if not holds(ctx, inst, c, hits):
                where = (f", товар № {inst.goods_no}" if inst.goods_no else "")
                msg = f"Не выполнено требование «{describe(c)}»" + (f" (при условии: {'; '.join(describe(w) for w in rule.when)})"
                                                    if rule.when and checks is rule.then else "") + where + "."
                ev = [decl.ev(n, n.qname + where) for n in (hits or [inst])[:5]]
                findings.append((msg, ev))
    if findings:
        return CondResult("violation", findings[:max_findings])
    return CondResult("pass" if evaluated else "not_applicable", [])


# ---- parser for table-3 conditional sentences ---------------------------------------
_REF = r"[\"“«]([^\"”»]*?)\s*\(([a-z]+:\s*\w+)\)[\"”»]"
_REF_CHAIN = _REF + r"((?:\s+в составе реквизита\s+" + _REF + r")*)"
_Q = r"[\"“«]([^\"”»]+)[\"”»]"


def _local(qn: str) -> str:
    return re.sub(r"\s+", "", qn).split(":", 1)[1]


def _chain(m: re.Match) -> list[str]:
    """Local names of a reference with its "в составе реквизита …" chain, innermost first."""
    names = [_local(m.group(2))]
    names += [_local(q) for q in re.findall(r"\(([a-z]+:\s*\w+)\)", m.group(3) or "")]
    return names


class PathResolver:
    def __init__(self, paths: list[str]):
        self.paths = paths
        self.by_local: dict[str, list[str]] = {}
        for p in paths:
            self.by_local.setdefault(p.rsplit("/", 1)[1].lower(), []).append(p)

    def resolve(self, chain: list[str], near: str) -> str | None:
        cands = self.by_local.get(chain[0].lower(), [])
        # honour the explicit "в составе" chain: candidate must contain the outer names in order
        if len(chain) > 1:
            cands = [c for c in cands if all(f"/{n}/".lower() in c.lower() + "/" for n in chain[1:])]
        if not cands:
            return None

        def common(a: str, b: str) -> int:
            n = 0
            for x, y in zip(a.split("/"), b.split("/")):
                if x != y:
                    break
                n += 1
            return n

        return max(cands, key=lambda c: (common(c, near), -len(c)))


def _rel(scope: str, path: str) -> str:
    """Path relative to scope, going up with '..' when needed."""
    if path == scope:
        return "."
    a, b = scope.split("/"), path.split("/")
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    if i <= 1:
        return path  # nothing in common below the root: keep absolute
    return "/".join([".."] * (len(a) - i) + b[i:])


def _common_scope(paths: list[str]) -> str:
    parts = [p.split("/") for p in paths]
    out = []
    for xs in zip(*parts):
        if len(set(xs)) != 1:
            break
        out.append(xs[0])
    return "/".join(out)


COUNTRY_WORDS = {"армения": "AM", "беларусь": "BY", "казахстан": "KZ", "кыргызск": "KG", "российск": "RU"}


def _countries(prefix: str) -> list[str] | None:
    low = prefix.lower()
    cs = sorted({c for k, c in COUNTRY_WORDS.items() if k in low})
    return cs or None


def parse_sentence(rule_id: str, self_path: str, sentence: str, resolver: PathResolver) -> dict | None:
    """Return a DSL rule dict for a recognised conditional sentence, else None."""
    s = " ".join(sentence.split()).rstrip(".")
    countries = None
    m = re.match(r"^в\s+(?:Республике|Кыргызской|Российской).*?(?=\s+(?:если|в случае)\b)\s+", s, re.I)
    if m:
        countries = _countries(m.group(0))
        s = s[m.end():]

    def ref_path(mm: re.Match) -> str | None:
        return resolver.resolve(_chain(mm), self_path)

    # simple self rules
    m = re.match(r"^(?:в случае заполнения реквизита|реквизит должен быть заполнен в случае формирования реквизита)\s+" + _REF_CHAIN + r"(.*)$", s, re.I)
    if m:
        other = ref_path(m)
        if not other:
            return None
        rest = m.group(m.lastindex)
        then = [{"present": "@self"}]
        if re.search(r"шаблоном:\s*YYYY-MM-DD\b", rest):
            then.append({"matches": ["@self", DATE]})
        return _finish(rule_id, self_path, countries, [{"present": other}], then, [], sentence)

    m = re.match(r"^(?:в случае\s+)?если\s+(.+?),\s+то\s+(.+?)(?:,\s+иначе\s+(.+))?$", s, re.I)
    if not m:
        return None
    cond_s, then_s, else_s = m.group(1), m.group(2), m.group(3)
    when = _parse_conds(cond_s, self_path, resolver)
    then = _parse_conseq(then_s, self_path, resolver)
    els = _parse_conseq(else_s, self_path, resolver) if else_s else []
    if when is None or then is None or (else_s and els is None):
        return None
    return _finish(rule_id, self_path, countries, when, then, els or [], sentence)


def _parse_conds(s: str, self_path: str, resolver: PathResolver) -> list[dict] | None:
    out = []
    for part in re.split(r"\s+и\s+(?=реквизит)", s):
        m = re.match(r"^реквизит\s+" + _REF_CHAIN + r"\s+(.+)$", part.strip(), re.I)
        if not m:
            return None
        p = resolver.resolve(_chain(m), self_path)
        if not p:
            return None
        pred = m.group(m.lastindex).strip()
        vals = re.findall(_Q, pred)
        if re.match(r"^содержит значение", pred) and vals:
            out.append({"in": [p, vals]} if len(vals) > 1 else {"eq": [p, vals[0]]})
        elif re.match(r"^заполнен$", pred):
            out.append({"present": p})
        elif re.match(r"^не заполнен$", pred):
            out.append({"absent": p})
        else:
            return None
    return out


_REF_NC = r"[\"“«][^\"”»]*?\s*\([a-z]+:\s*\w+\)[\"”»](?:\s+в составе реквизита\s+[\"“«][^\"”»]*?\s*\([a-z]+:\s*\w+\)[\"”»])*"
_CONSEQ = re.compile(
    r"^реквизит(?:\s+(?P<ref>" + _REF_NC + r"))?\s+"
    r"(?P<verb>не должен быть заполнен|должен быть заполнен|может быть заполнен|должен содержать значение\s+" + _Q + r")$",
    re.I)


def _ref_target(text: str | None, self_path: str, resolver: PathResolver) -> str | None:
    if not text:
        return "@self"
    m = re.match(_REF_CHAIN, text)
    return resolver.resolve(_chain(m), self_path) if m else None


def _parse_conseq(s: str, self_path: str, resolver: PathResolver) -> list[dict] | None:
    s = s.strip()
    m = _CONSEQ.match(s)
    if m:
        target = _ref_target(m.group("ref"), self_path, resolver)
        if not target:
            return None
        verb = m.group("verb")
        if verb.startswith("должен содержать"):
            return [{"eq": [target, re.findall(_Q, verb)[0]]}]
        if verb.startswith("не должен"):
            return [{"absent": target}]
        if verb.startswith("должен быть"):
            return [{"present": target}]
        return []  # "может быть заполнен": no constraint
    if re.match(r"^реквизиты\s+.+?\s+могут быть заполнены$", s, re.I):
        return []
    m = re.match(r"^реквизиты\s+(.+?)\s+не должны быть заполнены$", s, re.I)
    if m:
        refs = list(re.finditer(_REF_CHAIN, m.group(1)))
        paths = [resolver.resolve(_chain(r), self_path) for r in refs]
        if not paths or None in paths:
            return None
        return [{"absent": p} for p in paths]
    m = re.match(r"^должно быть заполнено не менее одного из реквизитов:\s*(.+)$", s, re.I)
    if m:
        refs = list(re.finditer(_REF_CHAIN, m.group(1)))
        paths = [resolver.resolve(_chain(r), self_path) for r in refs]
        if not paths or None in paths:
            return None
        return [{"any_present": paths}]
    m = re.match(r"^должен быть сформирован строго один экземпляр реквизита\s+" + _REF_CHAIN + r"$", s, re.I)
    if m:
        p = resolver.resolve(_chain(m), self_path)
        return [{"max_count": [p, 1]}, {"present": p}] if p else None
    return None


def _finish(rule_id, self_path, countries, when, then, els, sentence) -> dict:
    def paths_of(conds):
        for c in conds:
            (op, arg), = c.items()
            if op in ("present", "absent"):
                yield arg
            elif op == "any_present":
                yield from arg
            elif op in ("eq", "in", "matches", "max_count"):
                yield arg[0]

    targets = [p if p != "@self" else self_path for p in paths_of(then + els)] or [self_path]
    # evaluate per instance of the consequence target's parent
    scope = targets[0].rsplit("/", 1)[0]

    def fix(conds):
        out = []
        for c in conds:
            (op, arg), = c.items()
            conv = lambda p: _rel(scope, self_path if p == "@self" else p)  # noqa: E731
            if op in ("present", "absent"):
                out.append({op: conv(arg)})
            elif op == "any_present":
                out.append({op: [conv(p) for p in arg]})
            else:
                out.append({op: [conv(arg[0]), *arg[1:]]})
        return out

    return {"id": "C-" + rule_id, "source": rule_id, "scope": scope, "countries": countries,
            "when": fix(when), "then": fix(then), "else": fix(els), "severity": "error",
            "review": "ai-expert (автоматический разбор формулировки)", "text": sentence}
