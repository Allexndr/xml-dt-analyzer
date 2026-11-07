"""Tests for the knowledge base (XSD tree, Alta editions, table 3, catalog) and fact tools."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from xmldt.facts.tools import Declaration, _range_size
from xmldt.indexer import index_xml, slice_utf16
from xmldt.kb.alta import cell_text, load, resolve_on_date
from xmldt.kb.build import main as build_main
from xmldt.kb.concepts import load_catalog, validate
from xmldt.kb.requisites import parse_table
from xmldt.schema import validate as xsdval
from xmldt.schema.xsd_tree import load_bundle

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"
BUNDLE = DATA / "schemas" / "R.055" / "1.2.0"

pytestmark = pytest.mark.skipif(not BUNDLE.exists(), reason="data bundle not present")


@pytest.fixture(scope="module")
def tree():
    return load_bundle(BUNDLE)[1]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA / "kb" / "concepts_R.055_1.2.0.yaml")


@pytest.fixture(scope="module")
def decl(catalog):
    return Declaration(index_xml(SAMPLE.read_bytes()), catalog)


# ---- schema ---------------------------------------------------------------
def test_tree_has_goods_item_and_marking(tree):
    paths = {n.path for n in tree}
    assert "/GoodsDeclaration/GDGoodsShipmentDetails/GDGoodsItemDetails" in paths
    item = next(n for n in tree if n.path.endswith("/GDGoodsItemDetails") and n.depth == 2)
    assert item.min_occurs == 0 and item.max_occurs is None
    assert item.model_id == "M.CA.CDE.00431" or item.model_id.startswith("M.CA.CDE")


def test_detect_and_validate_sample():
    info = xsdval.detect(SAMPLE.read_bytes(), DATA / "schemas")
    assert info and info.code == "R.055" and info.version == "1.2.0" and info.supported
    assert xsdval.validate(SAMPLE.read_bytes(), BUNDLE) == []


def test_validate_reports_errors_with_lines():
    bad = SAMPLE.read_bytes().replace(b"<casdo:GoodsQuantity>3<", b"<casdo:GoodsQuantity>three<")
    errs = xsdval.validate(bad, BUNDLE)
    assert errs and errs[0].line == 10 and "GoodsQuantity" in errs[0].message


# ---- legal texts ----------------------------------------------------------
def test_alta_edition_resolution_switches_structure_version():
    root = load(str(DATA / "norms" / "alta_23kr0075.html"))

    def version_on(d):
        doc, _ = resolve_on_date(root, d)
        row = next(r for r in doc.xpath(".//table")[0].xpath(".//tr") if "Версия" in r.text_content())
        return cell_text(row.xpath("./td")[2])

    assert version_on(date(2026, 9, 30)) == "1.1.0"
    assert version_on(date(2026, 10, 1)) == "1.2.0"


def test_table3_matches_xsd_completely(tree):
    doc, _ = resolve_on_date(load(str(DATA / "norms" / "alta_23kr0075.html")), date(2026, 11, 6))
    table = next(t for t in doc.xpath(".//table") if "Имя реквизита" in t.text_content()[:400])
    reqs, problems = parse_table(table)
    assert not problems
    xsd_paths = {n.path for n in tree}
    norm = lambda s: s.lower().replace("i", "l")  # noqa: E731 — official text has I/l typos
    xsd_norm = {norm(p) for p in xsd_paths}
    assert all(r.path in xsd_paths or norm(r.path) in xsd_norm for r in reqs)
    assert len({r.path for r in reqs}) == len(xsd_paths) - 1  # all but the root


def test_catalog_paths_exist(tree, catalog):
    assert validate(catalog, {n.path for n in tree}) == []
    assert {c.key for c in catalog.by_graph("31")} >= {"goods_description", "marking_details"}


def test_build_cli_succeeds(tmp_path, monkeypatch):
    assert build_main(["--data", str(DATA), "--date", "2026-11-07"]) == 0


# ---- fact tools -----------------------------------------------------------
def test_count_goods(decl):
    r = decl.count_goods()
    assert r.value == {"goods_items": 3, "declared_goods_quantity": 3}
    assert [e.line_start for e in r.evidence[:3]] == [40, 111, 132]
    assert not r.warnings


def test_marking_stats(decl):
    v = decl.marking_stats().value
    assert v["items_with_marking_data_list"] == ["1", "3"]
    assert v["items_without_marking_data_list"] == ["2"]
    assert v["codes_listed"] == 4 and v["codes_listed_unique"] == 3
    assert v["codes_in_ranges"] == 300
    assert list(v["duplicate_codes"].values()) == [2]


def test_evidence_ranges_point_at_tags(decl):
    text = decl.idx.text
    for r in (decl.count_goods(), decl.marking_stats(), decl.get_concept("consignor")):
        for e in r.evidence:
            s = slice_utf16(text, e.start, e.end)
            assert s.startswith("<" + e.tag) and s.endswith(">")


def test_same_facts_on_minified_copy(catalog):
    raw = SAMPLE.read_text(encoding="utf-8")
    minified = re.sub(r">\s+<", "><", raw).replace("\n", "")
    d = Declaration(index_xml(minified.encode("utf-8")), catalog)
    v = d.marking_stats().value
    assert v["codes_listed"] == 4 and v["codes_in_ranges"] == 300
    assert {e.line_start for e in d.count_goods().evidence} == {1}


def test_consignee_equal_indicator_is_flagged(decl):
    r = decl.get_concept("consignee")
    assert any("EqualIndicator" in w for w in r.warnings)


def test_missing_concept_is_reported_not_invented(decl):
    r = decl.get_concept("quota")
    assert r.value["found"] == 0 and r.warnings


@pytest.mark.parametrize(
    "first,last,size",
    [("(01)04601234500001", "(01)04601234500300", 300), ("AB0010", "AB0001", None), ("X1", "Y1", None)],
)
def test_range_size(first, last, size):
    assert _range_size(first, last) == size


def test_structure_version_dated_by_amending_acts():
    from xmldt.kb import acts, alta

    alta.ACT_DATES.update(acts.load_dates(DATA / "norms" / "act_dates.json"))
    root = load(str(DATA / "norms" / "alta_23kr0075.html"))

    def version_on(d):
        doc, _ = resolve_on_date(root, d)
        row = next(r for r in doc.xpath(".//table")[0].xpath(".//tr") if "Версия" in r.text_content())
        return cell_text(row.xpath("./td")[2])

    assert version_on(date(2025, 6, 1)) == "1.0.0"   # Decision 90 (v1.1.0) entered into force on 01.04.2026
    assert version_on(date(2026, 6, 1)) == "1.1.0"
    assert version_on(date(2026, 11, 7)) == "1.2.0"
