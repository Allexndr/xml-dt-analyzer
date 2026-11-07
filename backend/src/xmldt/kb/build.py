"""Build derived knowledge-base artifacts for one schema version and validate them.

Usage:
    uv run python -m xmldt.kb.build --data ../data --version 1.2.0 --date 2026-11-06

Outputs (in <data>/derived):
    r055_<v>_tree.json                 flattened XSD tree
    r055_<v>_table3_<date>.json        requisites of Decision No. 75 table 3 as of <date>
    r055_<v>_reconciliation.json       table 3 <-> XSD reconciliation report
    r055_<v>_rule_seeds.json           machine-classified notes (seeds for the rule engine)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from xmldt.kb.alta import load, resolve_on_date
from xmldt.kb import classifiers as clf
from xmldt.kb.concepts import load_catalog, validate
from xmldt.rules.compiler import compile_all, load_bindings
from xmldt.rules.compiler import save as save_rules
from xmldt.kb.requisites import parse_table
from xmldt.schema.xsd_tree import load_bundle

COUNTRIES = {
    "Республике Армения": "AM",
    "Республике Беларусь": "BY",
    "Республике Казахстан": "KZ",
    "Кыргызской Республике": "KG",
    "Российской Федерации": "RU",
}


def _norm_name(s: str) -> str:
    """Tolerate known typos of the official text: case and I/l confusion."""
    return s.lower().replace("i", "l")


def reconcile(reqs, tree) -> dict:
    xsd_paths = {n.path for n in tree}
    by_norm = {_norm_name(p): p for p in xsd_paths}
    exact, fuzzy, missing = 0, [], []
    for r in reqs:
        if r.path in xsd_paths:
            exact += 1
        elif _norm_name(r.path) in by_norm:
            fuzzy.append({"table": r.path, "xsd": by_norm[_norm_name(r.path)], "number": r.number})
            r.path = by_norm[_norm_name(r.path)]
        else:
            missing.append({"table": r.path, "number": r.number})
    covered = {r.path for r in reqs}
    return {
        "table_rows": len(reqs),
        "exact": exact,
        "fuzzy_typos": fuzzy,
        "table_not_in_xsd": missing,
        "xsd_not_in_table": sorted(p for p in xsd_paths if p not in covered and p != "/GoodsDeclaration"),
    }


def classify_note(note: str) -> dict:
    n = " ".join(note.split())
    low = n.lower()
    countries = [code for name, code in COUNTRIES.items() if name.lower() in low]
    kinds = []
    if re.search(r"реквизит не заполняется", low):
        kinds.append("not_filled")
    if re.search(r"реквизит должен быть заполнен", low):
        kinds.append("required")
    if re.search(r"шаблон", low):
        kinds.append("pattern")
    if re.search(r"одно из следующих значений|должен содержать значение|должен принимать значение", low):
        kinds.append("enumeration")
    if re.search(r"классификатор", low):
        kinds.append("classifier")
    if re.search(r"\bесли\b|в случае", low):
        kinds.append("conditional")
    if "используется в" in low:
        kinds.append("country_scope")
    return {"kinds": kinds or (["other"] if n else []), "countries": countries, "text": n}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--version", default="1.2.0")
    ap.add_argument("--date", default=date.today().isoformat())
    a = ap.parse_args(argv)
    on = date.fromisoformat(a.date)
    out = a.data / "derived"
    out.mkdir(exist_ok=True)

    from xmldt.kb import acts, alta as alta_mod
    alta_mod.ACT_DATES.update(acts.load_dates(a.data / "norms" / "act_dates.json"))
    print(f"amending acts with known entry dates: {len(alta_mod.ACT_DATES)}")

    manifest, tree = load_bundle(a.data / "schemas" / "R.055" / a.version)
    (out / f"r055_{a.version}_tree.json").write_text(
        json.dumps([asdict(n) for n in tree], ensure_ascii=False, indent=1), encoding="utf-8"
    )

    doc, editions = resolve_on_date(load(str(a.data / "norms" / "alta_23kr0075.html")), on)
    tables = [t for t in doc.xpath(".//table") if "Имя реквизита" in t.text_content()[:400]]
    if len(tables) != 1:
        print(f"expected exactly one requisite table on {on}, got {len(tables)}", file=sys.stderr)
        return 2
    reqs, problems = parse_table(tables[0])
    rec = reconcile(reqs, tree)
    rec["parse_problems"] = problems
    rec["editions_resolved"] = [
        {"act": e.amending_act, "from": str(e.valid_from), "to": str(e.valid_to), "fallback": e.fallback}
        for e in editions
    ]
    (out / f"r055_{a.version}_table3_{on}.json").write_text(
        json.dumps([asdict(r) for r in reqs], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / f"r055_{a.version}_reconciliation.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    seeds = []
    for r in reqs:
        if not r.note:
            continue
        c = classify_note(r.note)
        seeds.append({"path": r.path, "number": r.number, "title": r.title, "multiplicity": r.multiplicity,
                      "legal_ref": {"doc": "Решение Коллегии ЕЭК № 75", "table": 3, "position": r.number,
                                    "as_of": str(on)}, **c})
    (out / f"r055_{a.version}_rule_seeds.json").write_text(
        json.dumps(seeds, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    rules = compile_all([asdict(r) for r in reqs], str(on))
    save_rules(rules, out / f"r055_{a.version}_rules_compiled.json")
    cls, cls_log = clf.build(a.data / "norms" / "alta_10sr0378.html", on)
    clf.save(cls, out / f"classifiers_378_{on}.json")
    from xmldt.kb import references as ref
    refs = ref.build_units(a.data / "norms" / "alta_20kr0145.html", on)
    tn_files = sorted((a.data / "ref").glob("tnved_*.xlsx")) + sorted((a.data / "ref").glob("tnved_*.csv"))
    if tn_files:
        refs.append(ref.build_tnved(tn_files[-1], f"выгрузка {tn_files[-1].name}", str(on)))
    clf.save(refs, out / f"classifiers_ref_{on}.json")
    print("reference classifiers: " + ", ".join(f"{c.key} {len(c.items)}" for c in refs))
    known = {c.key for c in cls} | {c.key for c in refs}
    bindings = load_bindings(a.data / "kb" / f"classifier_bindings_R.055_{a.version}.yaml")
    bind_problems = [f"binding to unknown classifier {b.classifier}" for b in bindings
                     if not b.external and b.classifier not in known]
    rk = Counter(r.kind for r in rules)
    print(f"rules compiled: {len(rules) - rk['manual']} automated, {rk['manual']} manual; kinds: {dict(rk)}")
    print(f"classifiers: {len(cls)} ({sum(len(c.items) for c in cls)} codes); "
          f"ambiguous editions: {sum(r.ambiguous for r in cls_log)}/{len(cls_log)}; binding problems: {len(bind_problems)}")
    for p in bind_problems:
        print("  ", p)

    from xmldt.kb import provisions as prov
    provs = prov.build(a.data / "norms" / "alta_10sr0257.html", on)
    prov.save(provs, out / f"decision257_provisions_{on}.json")
    print(f"decision 257: {len(provs)} provisions, {sum(1 for p in provs if p.graph)} bound to graphs")

    # editions whose start date is still unknown: listed for expert review
    review_editions = []
    for fname, title in [("alta_23kr0075.html", "Решение Коллегии ЕЭК № 75"), ("alta_10sr0257.html", "Решение КТС № 257"),
                         ("alta_10sr0378.html", "Решение КТС № 378"), ("alta_20kr0145.html", "Решение Коллегии ЕЭК № 145")]:
        _, elog = resolve_on_date(load(str(a.data / "norms" / fname)), on)
        for i, r in enumerate(elog):
            if r.ambiguous:
                review_editions.append({"id": f"{fname}#{i}", "doc": title, "amending_act": r.amending_act,
                                        "valid_from": str(r.valid_from), "valid_to": str(r.valid_to)})
    (out / f"editions_review_{on}.json").write_text(json.dumps(review_editions, ensure_ascii=False, indent=1),
                                                    encoding="utf-8")
    print(f"editions to confirm: {len(review_editions)}")

    cat = load_catalog(a.data / "kb" / f"concepts_R.055_{a.version}.yaml")
    cat_problems = validate(cat, {n.path for n in tree})

    kinds = Counter(k for s in seeds for k in s["kinds"])
    countries = Counter(c for s in seeds for c in s["countries"])
    print(f"XSD nodes: {len(tree)}; table 3 rows: {rec['table_rows']} "
          f"(exact {rec['exact']}, typos {len(rec['fuzzy_typos'])}, missing {len(rec['table_not_in_xsd'])}); "
          f"XSD not in table: {len(rec['xsd_not_in_table'])}")
    print(f"rule seeds: {len(seeds)}; kinds: {dict(kinds)}; country-scoped: {dict(countries)}")
    print(f"concepts: {len(cat.concepts)}; catalog problems: {len(cat_problems)}")
    for p in cat_problems:
        print("  ", p)
    return 1 if cat_problems or rec["table_not_in_xsd"] or bind_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
