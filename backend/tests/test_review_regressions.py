"""Regression tests for defects found in the code review of the phase-0 spike."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest
from lxml import html

from xmldt.facts.tools import Declaration, safe_int
from xmldt.indexer import index_xml
from xmldt.kb.alta import resolve_on_date
from xmldt.kb.concepts import load_catalog
from xmldt.schema import validate as xsdval
from xmldt.schema.xsd_tree import SchemaSet, TreeBuilder

DATA = Path(__file__).resolve().parents[2] / "data"
SAMPLE = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"
needs_data = pytest.mark.skipif(not SAMPLE.exists(), reason="data bundle not present")


# 1. ReDoS in root-namespace detection
def test_detect_is_linear_on_hostile_input(tmp_path):
    for payload in (b"<" + b"a" * 65535, b"<a" * 30000):
        t = time.perf_counter()
        assert xsdval.detect(payload, tmp_path) is None
        assert time.perf_counter() - t < 1.0


# 2. Path traversal through the version in the namespace URN
def test_detect_rejects_traversal(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "manifest.json").write_text("{}")
    evil = b'<GoodsDeclaration xmlns="urn:EEC:R:055:GoodsDeclaration:v../../x"/>'
    assert xsdval.detect(evil, tmp_path / "schemas") is None


def test_detect_single_quoted_namespace(tmp_path):
    doc = b"<g:GoodsDeclaration xmlns:g='urn:EEC:R:055:GoodsDeclaration:v1.2.0'/>"
    info = xsdval.detect(doc, tmp_path)
    assert info and info.version == "1.2.0" and not info.supported


# 3. Non-text codecs must not escape as LookupError
@pytest.mark.parametrize("enc", ["hex", "base64", "zlib", "rot13", "bz2"])
def test_non_text_encoding_is_rejected(enc):
    r = index_xml(f'<?xml version="1.0" encoding="{enc}"?><a/>'.encode())
    assert not r.ok and r.error["kind"] == "encoding"


# 5-7. Edition resolution
def _doc(comment_new: str, comment_old: str = "", tail: str = "") -> html.HtmlElement:
    old = f'<p><span class="ordw-comment">{comment_old}</span></p>' if comment_old else ""
    return html.fromstring(
        "<div><p>Начало<div class='ordw-urobr'><div class='new'>Нов. ред. Решение 2</div>"
        "<div class='content'><div class='content-new'>"
        f"<p><span class='ordw-comment'>{comment_new}</span></p><p>НОВАЯ</p>"
        "<div class='ordw-urobr'><div class='old'>См. пред. ред. Решение 1</div>"
        f"<div class='content'><div class='content-old'>{old}<p>СТАРАЯ</p></div></div></div>"
        f"</div></div></div>{tail}</p></div>"
    )


def test_impossible_date_does_not_crash():
    doc, log = resolve_on_date(_doc("Начало действия редакции - 31.02.2026 г."), date(2026, 1, 1))
    assert log and "НОВАЯ" in doc.text_content() or "СТАРАЯ" in doc.text_content()


def test_tail_text_stays_after_inserted_edition():
    doc, _ = resolve_on_date(_doc("Начало действия редакции - 01.01.2026 г.", tail=" ХВОСТ"), date(2026, 6, 1))
    text = " ".join(doc.text_content().split())
    assert text.index("НОВАЯ") < text.index("ХВОСТ")


def test_undated_edition_is_flagged_ambiguous():
    _, log = resolve_on_date(_doc("без даты"), date(2020, 1, 1))
    assert log[0].ambiguous


def test_date_after_all_ranges_picks_newest():
    d = _doc("Редакция действует до 31.12.2025 г. (включительно)", "Редакция действует до 31.12.2024 г. (включительно)")
    doc, log = resolve_on_date(d, date(2027, 1, 1))
    assert log[0].fallback and "НОВАЯ" in doc.text_content()


# 8-9. Fact tools on malformed declarations
def test_safe_int():
    assert safe_int("12") == 12 and safe_int("²") is None and safe_int(None) is None and safe_int("x") is None


@needs_data
def test_malformed_counts_do_not_crash():
    raw = SAMPLE.read_bytes()
    raw = raw.replace(b"<casdo:GoodsQuantity>3</casdo:GoodsQuantity>", b"<casdo:GoodsQuantity>three</casdo:GoodsQuantity>")
    raw = raw.replace(b"<casdo:CIMQuantity>4</casdo:CIMQuantity>", b"<casdo:CIMQuantity/>")
    start = raw.index(b"<cacdo:FirstIdentificationMeansItemDetails>")
    end = raw.index(b"</cacdo:FirstIdentificationMeansItemDetails>") + len(b"</cacdo:FirstIdentificationMeansItemDetails>")
    raw = raw[:start] + raw[end:]
    d = Declaration(index_xml(raw), load_catalog(DATA / "kb" / "concepts_R.055_1.2.0.yaml"))
    g = d.count_goods()
    assert g.value["declared_goods_quantity"] is None and any("Графа 5" in w for w in g.warnings)
    m = d.marking_stats()
    assert m.value["ranges_with_unknown_size"] == 1
    assert any("границ" in w for w in m.warnings)


@needs_data
def test_many_duplicates_are_fast_and_capped():
    unit = (b"<cacdo:IdentificationMeansItemDetails><cacdo:IdentificationMeansDataUnitDetails>"
            b"<casdo:IdentificationMeansUnitCharacterValueId>X</casdo:IdentificationMeansUnitCharacterValueId>"
            b"</cacdo:IdentificationMeansDataUnitDetails></cacdo:IdentificationMeansItemDetails>")
    raw = SAMPLE.read_bytes()
    a = raw.index(b"<cacdo:IdentificationMeansListDetails>") + len(b"<cacdo:IdentificationMeansListDetails>")
    b = raw.index(b"</cacdo:IdentificationMeansListDetails>")
    raw = raw[:a] + unit * 20000 + raw[b:]
    d = Declaration(index_xml(raw), load_catalog(DATA / "kb" / "concepts_R.055_1.2.0.yaml"))
    t = time.perf_counter()
    m = d.marking_stats()
    assert time.perf_counter() - t < 5
    assert m.value["codes_listed"] == 20000 and m.value["codes_listed_unique"] == 1
    assert len([e for e in m.evidence if "повторяющийся" in e.label]) <= 50


# 10. Group multiplicity and attribute scoping in the XSD tree
def test_group_occurs_and_attribute_scope(tmp_path):
    xsd = tmp_path / "t.xsd"
    xsd.write_text(
        """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:t" xmlns:t="urn:t">
  <xs:element name="Root"><xs:complexType>
    <xs:sequence>
      <xs:sequence maxOccurs="unbounded"><xs:element name="A" type="xs:string"/></xs:sequence>
      <xs:choice><xs:element name="B" type="xs:string"/><xs:element name="C" type="xs:string"/></xs:choice>
      <xs:element name="Child"><xs:complexType>
        <xs:sequence><xs:element name="X" type="xs:string"/></xs:sequence>
        <xs:attribute name="childAttr" type="xs:string"/>
      </xs:complexType></xs:element>
    </xs:sequence>
    <xs:attribute name="rootAttr" type="xs:string" use="required"/>
  </xs:complexType></xs:element>
</xs:schema>""",
        encoding="utf-8",
    )
    nodes = {n.name: n for n in TreeBuilder(SchemaSet([xsd])).build("{urn:t}Root")}
    assert (nodes["A"].min_occurs, nodes["A"].max_occurs) == (1, None)
    assert nodes["B"].min_occurs == 0 and nodes["B"].in_choice
    assert [a.name for a in nodes["Root"].attributes] == ["rootAttr"]
    assert [a.name for a in nodes["Child"].attributes] == ["childAttr"]
