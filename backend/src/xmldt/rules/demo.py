"""Run all checks on a declaration and print a report.

    uv run python -m xmldt.rules.demo ../data/samples/synthetic_dt_r055_1.2.0.xml ../data KZ
"""
import sys
from pathlib import Path

from xmldt.facts.tools import Declaration
from xmldt.indexer import index_xml
from xmldt.kb.classifiers import load_all
from xmldt.kb.concepts import load_catalog
from xmldt.rules.compiler import load_bindings, load_expert, load_rules
from xmldt.rules.engine import Engine

STATUS = {"violation": "НАРУШЕНИЕ", "needs_info": "НУЖНЫ ДАННЫЕ", "pass": "ОК",
          "not_applicable": "НЕ ПРИМЕНИМО", "not_implemented": "НЕ АВТОМАТИЗИРОВАНО"}


def main() -> None:
    xml, data = Path(sys.argv[1]), Path(sys.argv[2])
    jur = sys.argv[3] if len(sys.argv) > 3 else None
    d = Declaration(index_xml(xml.read_bytes()), load_catalog(data / "kb/concepts_R.055_1.2.0.yaml"))
    eng = Engine(load_rules(data / "derived/r055_1.2.0_rules_compiled.json"),
                 load_bindings(data / "kb/classifier_bindings_R.055_1.2.0.yaml"),
                 load_all(data / "derived"), expert=load_expert(data / "kb" / "expert_rules_R.055_1.2.0.yaml"))
    rep = eng.run(d, jurisdiction=jur, check_date="2026-11-07")
    s = rep.summary()
    print(f"Юрисдикция: {jur or 'не задана'}. Правил: {s['rules']}, автоматизировано: {s['automated']}.")
    print("Статусы:", ", ".join(f"{STATUS[k]} {v}" for k, v in sorted(s["by_status"].items())))
    for r in rep.results:
        if r.status in ("violation", "needs_info"):
            ref = r.legal_ref
            src = (f"{ref['doc']}, табл. {ref['table']}, поз. {ref['position']}" if "table" in ref
                   else ref.get("doc") or ref.get("basis"))
            print(f"\n[{STATUS[r.status]} · {r.severity}] {r.rule_id}  ({src})")
            print("   правило:", r.text[:160])
            for f in r.findings[:3]:
                lines = ", ".join(f"{e.line_start}" + (f"–{e.line_end}" if e.line_end != e.line_start else "") for e in f.evidence[:3])
                print(f"   - {f.message}  [строки {lines}]")


if __name__ == "__main__":
    main()
