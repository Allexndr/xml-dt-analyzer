"""Compile notes of table 3 (Decision No. 75) into executable format-level rules.

Only sentences that match a strict template are compiled. Every other sentence
becomes a ``manual`` rule: it is listed in coverage as "not implemented"
instead of being guessed. Compiled rules keep the exact legal reference
(document, table, position, edition date) and the original sentence.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from xmldt.rules.conditional import DATE, DATETIME, PathResolver, parse_sentence

COUNTRIES = {
    "армения": "AM",
    "беларусь": "BY",
    "казахстан": "KZ",
    "кыргызск": "KG",
    "российск": "RU",
}

_SENT = re.compile(r"(?<=\.)\s+(?=[А-ЯA-Z])")  # split on sentence ends only; ";" separates list items
_Q = r"[\"“«]([^\"”»]+)[\"”»]"
T_NOT_FILLED = re.compile(r"^реквизит не заполняется\.?$", re.I)
T_REQUIRED = re.compile(r"^реквизит должен быть заполнен\.?$", re.I)
T_FIXED = re.compile(r"^реквизит (?:при его заполнении )?должен (?:содержать|принимать) значение " + _Q + r"(?:\s*[-–]\s*[^;]*)?\.?$", re.I)
T_ONEOF = re.compile(r"^реквизит (?:при его заполнении )?должен (?:содержать|принимать) одно из (?:следующих )?значений:\s*(.+?)\.?$", re.I | re.S)
T_PATTERN = re.compile(r"^значение реквизита (?:при его заполнении )?должно соответствовать шаблону:\s*(\S+?)\.?$", re.I)
T_COUNTRY = re.compile(r"^реквизит используется в (.+?)\.?$", re.I)
T_SINGLE = re.compile(r"^при заполнении реквизита .+ должен формироваться строго один экземпляр реквизита\.?$", re.I)
T_ONE_OF_CHILDREN = re.compile(r"^для указания .+ должен быть заполнен один из реквизитов:\s*(.+?)\.?$", re.I)
T_TEMPLATE = re.compile(r"^значение реквизита (?:при его заполнении )?должно (приводиться|содержать)[^:]*?шаблоном:\s*(YYYY-MM-DDThh:mm:ss\.ccc\+/-hh:mm|YYYY-MM-DD)\b", re.I)
TEMPLATES = {"YYYY-MM-DD": DATE, "YYYY-MM-DDThh:mm:ss.ccc+/-hh:mm": DATETIME}
T_NOT_CITY = re.compile(r"^реквизит при его заполнении должен содержать наименование населенного пункта, отличного от значения реквизита", re.I)
T_SHORT_COUNTRY = re.compile(r"^реквизит при его заполнении должен содержать краткое название страны, код которой содержится в реквизите .*Код страны", re.I)
T_REQUIRED_IN = re.compile(r"^реквизит должен быть заполнен в (Республике .+|Кыргызской .+|Российской .+)$", re.I)
# Descriptions of purpose rather than checkable requirements
T_PHONE = re.compile(r"^в случае если реквизит .*Код вида связи.* содержит значение \"TE\" или \"FX\", реквизит .* должен содержать значение, приводимое в соответствии с шаблоном: \+CCC PP HHHH", re.I)
# follow-up sentences of the phone template, enforced by the phone rule itself
T_PHONE_TAIL = re.compile(r"^(Разделителем между группами символов является знак пробела|Длина значения реквизита должна составлять не более 15 цифр|Иные символы и разделители не допускаются)", re.I)
T_REQUIRED_CHANNEL = re.compile(r"^реквизит должен быть заполнен и содержать код вида средства \(канала\) связи", re.I)
T_ALNUM_N = re.compile(r"^реквизит должен содержать (\d+) буквенно-числовых символов", re.I)
PHONE = r"\+\d{1,3}( \d+)+"
# unit symbols are checked by the classifier binding (units_eaeu_abbr), so the sentence is covered
T_UNITS = re.compile(r"^реквизит (?:при его заполнении )?должен содержать условное обозначение единицы измерения", re.I)
T_INFORMATIONAL = re.compile(r"^(реквизит предназначен для указания|реквизит используется для указания|в реквизите указыва|"
                             r"реквизит может быть заполнен информационной системой|в республике .+ реквизит предназначен|"
                             r"реквизит предназначен)", re.I)
T_CLASSIFIER_EXTRA = re.compile(r"в соответствии с классификатором .+? (?:или|либо) (?:значение " + _Q + r"|одно из следующих значений:\s*(.+))", re.I)


@dataclass
class Rule:
    id: str
    kind: str  # not_filled | required | allowed_values | pattern | country_scope | single_instance | one_of_children | manual
    path: str
    params: dict = field(default_factory=dict)
    severity: str = "error"  # error | warning
    legal_ref: dict = field(default_factory=dict)
    text: str = ""


def _values(spec: str) -> list[str] | None:
    quoted = re.findall(_Q, spec)
    if quoted:
        return quoted
    # "ЭД - если …; ОО - в остальных случаях" / "1 - …; 0 - …"
    parts = [p.strip() for p in re.split(r";\s*", spec) if p.strip()]
    vals = []
    for p in parts:
        m = re.match(r"^([0-9A-ZА-ЯЁ]{1,10})\s*[-–]\s+", p)
        if not m:
            return None
        vals.append(m.group(1))
    return vals or None


def _countries(spec: str) -> list[str]:
    low = spec.lower()
    return sorted({code for k, code in COUNTRIES.items() if k in low})


def compile_requisite(req: dict, as_of: str, resolver: PathResolver | None = None) -> list[Rule]:
    note = " ".join((req.get("note") or "").split())
    if not note:
        return []
    ref = {"doc": "Решение Коллегии ЕЭК № 75", "table": 3, "position": req["number"], "as_of": as_of}
    rules: list[Rule] = []
    sentences = [s.strip() for s in _SENT.split(note) if s.strip()]
    for i, s in enumerate(sentences, 1):
        rid = f"T3-{req['number']}-{i}"
        mk = lambda kind, sev="error", **p: Rule(rid, kind, req["path"], p, sev, ref, s)  # noqa: E731
        if T_NOT_FILLED.match(s):
            rules.append(mk("not_filled"))
        elif T_REQUIRED.match(s):
            rules.append(mk("required"))
        elif m := T_FIXED.match(s):
            rules.append(mk("allowed_values", values=[m.group(1)]))
        elif (m := T_ONEOF.match(s)) and (vals := _values(m.group(1))):
            rules.append(mk("allowed_values", values=vals))
        elif (m := T_PATTERN.match(s)) and "YYYY" not in m.group(1):
            try:
                re.compile(m.group(1))
                rules.append(mk("pattern", regex=m.group(1)))
            except re.error:
                rules.append(mk("manual", "warning"))
        elif (m := T_COUNTRY.match(s)) and (cs := _countries(m.group(1))):
            rules.append(mk("country_scope", "warning", countries=cs))
        elif T_SINGLE.match(s):
            rules.append(mk("single_instance"))
        elif (m := T_ONE_OF_CHILDREN.match(s)) and (names := re.findall(r"\(([a-z]+:\w+)\)", m.group(1))):
            rules.append(mk("one_of_children", children=[n.split(":")[1] for n in names]))
        elif (m := T_CLASSIFIER_EXTRA.search(s)) and (
            extra := [m.group(1)] if m.group(1) else (_values(m.group(2)) or [])
        ) and all(re.fullmatch(r"[0-9A-ZА-ЯЁ]{1,4}", v) for v in extra):
            rules.append(mk("classifier_extra", "error", allow=extra))
        elif (m := T_TEMPLATE.match(s)) and m.group(2) in TEMPLATES:
            rules.append(mk("pattern", regex=TEMPLATES[m.group(2)], template=m.group(2)))
        elif resolver is not None and (dsl := parse_sentence(rid, req["path"], s, resolver)):
            rules.append(mk("conditional", dsl=dsl))
        elif T_PHONE.match(s):
            parent = req["path"].rsplit("/", 1)[0]
            leaf = req["path"].rsplit("/", 1)[1]
            rules.append(mk("conditional", dsl={
                "id": "C-" + rid, "source": rid, "scope": parent,
                "when": [{"in": ["CommunicationChannelCode", ["TE", "FX"]]}],
                "then": [{"matches": [leaf, PHONE]}, {"digits_max": [leaf, 15]}], "severity": "error",
                "review": "ai-expert (шаблон +CCC PP HHHH: группы цифр через пробел, не более 15 цифр)", "text": s}))
        elif T_PHONE_TAIL.match(s):
            rules.append(mk("covered", "warning", by=f"T3-{req['number']}-1"))
        elif T_REQUIRED_CHANNEL.match(s):
            rules.append(mk("required"))  # the list of channel codes itself is outside the bundled classifiers
        elif m := T_ALNUM_N.match(s):
            rules.append(mk("pattern", regex=rf"[0-9A-Za-zА-ЯЁа-яё]{{{m.group(1)}}}"))
        elif T_UNITS.match(s):
            rules.append(mk("covered", "warning", by="CL-units_eaeu_abbr"))
        elif T_NOT_CITY.match(s):
            parent = req["path"].rsplit("/", 1)[0]
            rules.append(mk("conditional", "warning", dsl={
                "id": "C-" + rid, "source": rid, "scope": parent, "when": [],
                "then": [{"not_equal": [req["path"].rsplit("/", 1)[1], "CityName"]}], "severity": "warning",
                "review": "ai-expert (автоматический разбор формулировки)", "text": s}))
        elif T_SHORT_COUNTRY.match(s):
            parent = req["path"].rsplit("/", 1)[0]
            rules.append(mk("conditional", "warning", dsl={
                "id": "C-" + rid, "source": rid, "scope": parent, "when": [],
                "then": [{"classifier_name": ["CACountryCode", req["path"].rsplit("/", 1)[1], "countries"]}],
                "severity": "warning", "review": "ai-expert (автоматический разбор формулировки)", "text": s}))
        elif (m := T_REQUIRED_IN.match(s)) and (cs := _countries(m.group(1))):
            parent = req["path"].rsplit("/", 1)[0]
            rules.append(mk("conditional", dsl={
                "id": "C-" + rid, "source": rid, "scope": parent, "countries": cs, "when": [],
                "then": [{"present": req["path"].rsplit("/", 1)[1]}], "severity": "error",
                "review": "ai-expert (автоматический разбор формулировки)", "text": s}))
        elif T_INFORMATIONAL.match(s):
            rules.append(mk("informational", "warning"))
        else:
            rules.append(mk("manual", "warning"))
    return rules


@dataclass
class Binding:
    classifier: str
    element: str | None = None
    attribute: str | None = None
    within: str | None = None
    allow: list[str] = field(default_factory=list)
    external: bool = False


def load_bindings(path: Path) -> list[Binding]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Binding(**b) for b in raw["bindings"]]


def compile_all(table3: list[dict], as_of: str) -> list[Rule]:
    resolver = PathResolver([r["path"] for r in table3])
    out: list[Rule] = []
    for r in table3:
        out.extend(compile_requisite(r, as_of, resolver))
    return out


def save(rules: list[Rule], path: Path) -> None:
    path.write_text(json.dumps([asdict(r) for r in rules], ensure_ascii=False, indent=1), encoding="utf-8")


def load_rules(path: Path) -> list[Rule]:
    return [Rule(**r) for r in json.loads(path.read_text(encoding="utf-8"))]


def load_expert(path: Path) -> list[dict]:
    """Hand-written conditional rules (see data/kb/expert_rules_*.yaml); empty if the file is absent."""
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("rules", [])
