"""Demo: answer the reference question of the spec (section 9.1) without any LLM.

    uv run python -m xmldt.facts.demo ../data/samples/synthetic_dt_r055_1.2.0.xml ../data
"""
import sys
from pathlib import Path

from xmldt.facts.tools import Declaration
from xmldt.indexer import index_xml
from xmldt.kb.concepts import load_catalog


def main() -> None:
    xml, data = Path(sys.argv[1]), Path(sys.argv[2])
    idx = index_xml(xml.read_bytes())
    if not idx.ok:
        print("Ошибка:", idx.error)
        return
    d = Declaration(idx, load_catalog(data / "kb" / "concepts_R.055_1.2.0.yaml"))
    g, m = d.count_goods(), d.marking_stats()
    v = m.value
    print("Вопрос: Сколько товаров в декларации и у скольких есть коды маркировки?\n")
    print(f"В декларации {g.value['goods_items']} товарных позиции (в графе 5 заявлено {g.value['declared_goods_quantity']}).")
    print(f"Сведения о маркировке есть у {v['items_with_marking_data']} товаров: № {', '.join(v['items_with_marking_data_list'])}.")
    print(f"Перечислено кодов: {v['codes_listed']}, из них уникальных {v['codes_listed_unique']}; "
          f"ещё {v['codes_in_ranges']} кодов заданы диапазоном.")
    print(f"У товаров № {', '.join(v['items_without_marking_data_list'])} сведений о маркировке в XML нет.")
    for w in g.warnings + m.warnings:
        print("⚠", w)
    print("\nИсточники:")
    for e in g.evidence + m.evidence:
        rng = f"{e.line_start}" if e.line_start == e.line_end else f"{e.line_start}–{e.line_end}"
        print(f"  строки {rng:>9}  <{e.tag}>  {e.label}")
    print()
    for key in ("consignor", "consignee", "customs_procedure"):
        r = d.get_concept(key)
        vals = [f["value"] for v in r.value["values"] for f in v["fields"] if f["value"]]
        lines = ", ".join(f"{e.line_start}–{e.line_end}" for e in r.evidence)
        print(f"{r.value['title']} (графа {', '.join(r.value['graphs'])}): {'; '.join(vals) or '—'}  [строки {lines}]")
        for w in r.warnings:
            print("   ⚠", w)


if __name__ == "__main__":
    main()
