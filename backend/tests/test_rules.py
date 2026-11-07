"""Classifiers (Decision 378), rule compiler (table 3 of Decision 75) and rule engine."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from xmldt.facts.tools import Declaration
from xmldt.indexer import index_xml
from xmldt.kb import classifiers as clf
from xmldt.kb.concepts import load_catalog
from xmldt.rules.compiler import compile_requisite, load_bindings, load_expert, load_rules
from xmldt.rules.engine import Engine

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"
RULES = DATA / "derived" / "r055_1.2.0_rules_compiled.json"
pytestmark = pytest.mark.skipif(not RULES.exists(), reason="run xmldt.kb.build first")


@pytest.fixture(scope="module")
def classifiers():
    cls, _ = clf.build(DATA / "norms" / "alta_10sr0378.html", date(2026, 11, 7))
    return {**{c.key: c for c in cls}, **clf.load_all(DATA / "derived")}


@pytest.fixture(scope="module")
def engine(classifiers):
    return Engine(load_rules(RULES), load_bindings(DATA / "kb" / "classifier_bindings_R.055_1.2.0.yaml"), classifiers,
                  expert=load_expert(DATA / "kb" / "expert_rules_R.055_1.2.0.yaml"))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA / "kb" / "concepts_R.055_1.2.0.yaml")


def _run(engine, catalog, xml: str, jur="KZ"):
    return engine.run(Declaration(index_xml(xml.encode("utf-8")), catalog), jurisdiction=jur, check_date="2026-11-07")


# ---- classifiers ----------------------------------------------------------
def test_core_classifiers_present(classifiers):
    for k in ("customs_procedures", "countries", "currencies", "document_kinds", "package_kinds",
              "delivery_terms", "transport_modes", "valuation_methods", "payment_kinds", "payment_preferences"):
        assert classifiers[k].items, k


@pytest.mark.parametrize(
    "key,code,name_part,excluded",
    [
        ("customs_procedures", "40", "внутреннего потребления", False),
        ("countries", "CN", "КИТАЙ", False),
        ("currencies", "USD", "Доллар США", False),
        ("currencies", "840", "Доллар США", False),
        ("document_kinds", "04021", "инвойс", False),
        ("document_kinds", "01013", "Исключен", True),  # expanded from range "01012 - 01016"
    ],
)
def test_lookup(classifiers, key, code, name_part, excluded):
    it = classifiers[key].lookup(code)
    assert it and name_part in it.name and it.excluded is excluded


# ---- compiler -------------------------------------------------------------
def _req(note, path="/GoodsDeclaration/X", number="1"):
    return {"path": path, "number": number, "note": note}


@pytest.mark.parametrize(
    "note,kind,params",
    [
        ("реквизит не заполняется", "not_filled", {}),
        ("реквизит должен быть заполнен", "required", {}),
        ('реквизит должен содержать значение "R.055"', "allowed_values", {"values": ["R.055"]}),
        ('реквизит должен содержать одно из следующих значений: "ИМ", "ЭК"', "allowed_values", {"values": ["ИМ", "ЭК"]}),
        ("реквизит должен содержать одно из следующих значений: ЭД - если в электронном виде; ОО - в остальных случаях",
         "allowed_values", {"values": ["ЭД", "ОО"]}),
        ("значение реквизита должно соответствовать шаблону: \\d{9}", "pattern", {"regex": "\\d{9}"}),
        ("реквизит используется в Республике Казахстан", "country_scope", {"countries": ["KZ"]}),
        ("реквизит должен быть заполнен, если номер свидетельства содержит признак", "manual", {}),
    ],
)
def test_compile_templates(note, kind, params):
    r = compile_requisite(_req(note), "2026-11-07")[0]
    assert r.kind == kind and r.params == params
    assert r.legal_ref["doc"] == "Решение Коллегии ЕЭК № 75" and r.legal_ref["position"] == "1"


def test_multi_sentence_note_keeps_unknown_part_manual():
    rs = compile_requisite(_req('реквизит должен содержать значение "СД" - при использовании документов. '
                                "В остальных случаях реквизит не заполняется"), "2026-11-07")
    assert [r.kind for r in rs] == ["allowed_values", "manual"]


# ---- engine ---------------------------------------------------------------
def test_clean_sample_only_planted_duplicate(engine, catalog):
    rep = _run(engine, catalog, SAMPLE.read_text(encoding="utf-8"))
    viol = [r.rule_id for r in rep.results if r.status == "violation"]
    assert viol == ["CS-marking-duplicates"]
    s = rep.summary()
    assert s["by_status"]["not_implemented"] > 0  # coverage is reported honestly


@pytest.mark.parametrize(
    "old,new,rule_prefix",
    [
        ('codeListId="2006">40<', 'codeListId="2006">41<', "CL-customs_procedures"),
        ('<casdo:CACountryCode codeListId="2021">CN</casdo:CACountryCode>',
         '<casdo:CACountryCode codeListId="2021">XX</casdo:CACountryCode>', "CL-countries"),
        (">04021<", ">01013<", "CL-document_kinds"),
        (">ИМ<", ">ЭФ<", "T3-7-"),
        ('currencyCode="USD"', 'currencyCode="ZZZ"', "CL-currencies"),
        ("<casdo:InformationKindCode>13<", "<casdo:InformationKindCode>99<", "T3-19.18.28.1-"),
        ("<casdo:GoodsQuantity>3<", "<casdo:GoodsQuantity>5<", "CS-goods-quantity"),
    ],
)
def test_planted_errors_are_detected_with_evidence(engine, catalog, old, new, rule_prefix):
    raw = SAMPLE.read_text(encoding="utf-8")
    assert old in raw
    rep = _run(engine, catalog, raw.replace(old, new, 1))
    hits = [r for r in rep.results if r.status == "violation" and r.rule_id.startswith(rule_prefix)]
    assert hits, rule_prefix
    assert all(f.evidence and f.evidence[0].line_start > 0 for r in hits for f in r.findings)


def test_not_filled_requisite_present_is_violation(engine, catalog):
    raw = SAMPLE.read_text(encoding="utf-8")
    # DeclarantDetails/UnifiedCountryCode — "реквизит не заполняется" (table 3, pos. 18.1)
    assert "<csdo:SubjectName>ТОО" in raw
    raw = raw.replace("<csdo:SubjectName>ТОО", '<csdo:UnifiedCountryCode codeListId="2021">KZ</csdo:UnifiedCountryCode>'
                      "<csdo:SubjectName>ТОО", 1)
    rep = _run(engine, catalog, raw)
    assert any(r.rule_id.startswith("T3-18.1-") and r.status == "violation" for r in rep.results)


def test_jurisdiction_matters(engine, catalog):
    # SubsoilUserCode is used only in Kazakhstan (table 3, pos. 25)
    raw = SAMPLE.read_text(encoding="utf-8").replace(
        "</gd:GoodsDeclaration>", "<casdo:SubsoilUserCode>1</casdo:SubsoilUserCode></gd:GoodsDeclaration>")

    def status(jur):
        rep = _run(engine, catalog, raw, jur=jur)
        return next(r.status for r in rep.results if r.kind == "country_scope" and r.path.endswith("/SubsoilUserCode"))

    assert status(None) == "needs_info"
    assert status("KZ") == "pass"
    assert status("RU") == "violation"


def test_marking_sign_without_data_is_needs_info_not_violation(engine, catalog):
    raw = SAMPLE.read_text(encoding="utf-8")
    raw = raw.replace("<casdo:CommodityCode>8471300000</casdo:CommodityCode>",
                      "<casdo:CommodityCode>8471300000</casdo:CommodityCode>", 1)
    # put a marking sign on item 2 (which has no marking data)
    i = raw.index("<casdo:ConsignmentItemOrdinal>2</casdo:ConsignmentItemOrdinal>")
    j = raw.index("<cacdo:OriginCountryDetails>", i)
    raw = raw[:j] + "<casdo:CIMSignCode>М</casdo:CIMSignCode>" + raw[j:]
    rep = _run(engine, catalog, raw)
    r = next(r for r in rep.results if r.rule_id == "CS-marking-sign-without-data")
    assert r.status == "needs_info"


# ---- conditional (table 3 sentences) and expert rules ----------------------
from xmldt.rules.conditional import PathResolver, kz_bin_iin_valid, parse_sentence, ru_inn_valid  # noqa: E402


def test_checksums():
    assert kz_bin_iin_valid("123456789013") and not kz_bin_iin_valid("123456789012")
    assert ru_inn_valid("7707083893") and not ru_inn_valid("7707083894")


def test_parse_conditional_sentence():
    res = PathResolver([
        "/GoodsDeclaration/GDGoodsShipmentDetails/GDGoodsItemDetails/DTIdentificationMeansDetails/InformationKindCode",
        "/GoodsDeclaration/GDGoodsShipmentDetails/GDGoodsItemDetails/DTIdentificationMeansDetails/IdentificationMeansDetails/AggregationKindCode",
    ])
    d = parse_sentence("T3-x-1", "/GoodsDeclaration/GDGoodsShipmentDetails/GDGoodsItemDetails/DTIdentificationMeansDetails/"
                       "IdentificationMeansDetails/AggregationKindCode",
                       'если реквизит "Код вида информации (casdo:InformationKindCode)" содержит значение "10", то реквизит '
                       '"Код вида уровня маркировки (casdo:AggregationKindCode)" не должен быть заполнен.', res)
    assert d["scope"].endswith("/IdentificationMeansDetails")
    assert d["when"] == [{"eq": ["../InformationKindCode", "10"]}] and d["then"] == [{"absent": "AggregationKindCode"}]


def _viol(engine, catalog, raw, jur="KZ"):
    return {r.rule_id for r in _run(engine, catalog, raw, jur).results if r.status == "violation"}


def test_cim_quantity_required_when_not_after_release(engine, catalog):
    raw = SAMPLE.read_text(encoding="utf-8").replace("<casdo:CIMQuantity>300</casdo:CIMQuantity>", "", 1)
    assert "T3-19.18.28.3-1" in _viol(engine, catalog, raw, "KZ")
    assert "T3-19.18.28.3-1" not in _viol(engine, catalog, raw, "BY")  # BY has its own sentence (-2)


def test_aggregation_code_forbidden_for_kind_10(engine, catalog):
    raw = SAMPLE.read_text(encoding="utf-8").replace("<casdo:InformationKindCode>13<", "<casdo:InformationKindCode>10<", 1)
    assert "T3-19.18.28.4.1-1" in _viol(engine, catalog, raw)


def test_declarant_bin_checksum(engine, catalog):
    raw = SAMPLE.read_text(encoding="utf-8").replace(">123456789013<", ">123456789012<", 1)
    assert "E-KZ-TAXPAYER-DECLARANT" in _viol(engine, catalog, raw, "KZ")
    assert "E-KZ-TAXPAYER-DECLARANT" not in _viol(engine, catalog, raw, "RU")
    assert "E-KZ-TAXPAYER-DECLARANT" not in _viol(engine, catalog, SAMPLE.read_text(encoding="utf-8"), "KZ")


def test_phone_format(engine, catalog):
    comm = ('<ccdo:CommunicationDetails><csdo:CommunicationChannelCode>TE</csdo:CommunicationChannelCode>'
            '<csdo:CommunicationChannelId>8(727)123-45-67</csdo:CommunicationChannelId></ccdo:CommunicationDetails>')
    raw = SAMPLE.read_text(encoding="utf-8").replace("</ccdo:SubjectAddressDetails>\n  </cacdo:DeclarantDetails>",
                                                     "</ccdo:SubjectAddressDetails>" + comm + "\n  </cacdo:DeclarantDetails>", 1)
    assert any(v.startswith("T3-18.13.3-") for v in _viol(engine, catalog, raw))
    ok = raw.replace("8(727)123-45-67", "+7 727 1234567")
    assert not any(v.startswith("T3-18.13.3-") for v in _viol(engine, catalog, ok))


def test_tnved_and_units_are_checked(engine, catalog):
    raw = SAMPLE.read_text(encoding="utf-8")
    assert any(v.startswith("CL-tnved_eaeu") for v in _viol(engine, catalog, raw.replace(">6403999800<", ">6403999899<", 1)))
    assert any(v.startswith("CL-units_eaeu") for v in _viol(engine, catalog, raw.replace('measurementUnitCode="166"', 'measurementUnitCode="999"', 1)))
    assert not any(v.startswith(("CL-tnved", "CL-units")) for v in _viol(engine, catalog, raw))
