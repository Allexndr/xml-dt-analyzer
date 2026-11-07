"""Generate a SYNTHETIC R.055 v1.2.0 goods declaration for tests and demos.

Not a real declaration: all names, numbers and codes are invented. Element
order and namespaces come from the flattened XSD tree, so the output follows
the schema sequence order.
"""
import json, sys, uuid
from pathlib import Path
from xml.sax.saxutils import escape

DATA = Path(sys.argv[1])
tree = json.loads((DATA / "derived/r055_1.2.0_tree.json").read_text(encoding="utf-8"))
order = {n["path"]: i for i, n in enumerate(tree)}
info = {n["path"]: n for n in tree}
PFX = {"urn:EEC:R:055:GoodsDeclaration:v1.2.0": "gd", "urn:EEC:M:SimpleDataObjects:v0.4.16": "csdo",
       "urn:EEC:M:ComplexDataObjects:v0.4.16": "ccdo", "urn:EEC:M:CA:SimpleDataObjects:v1.10.4": "casdo",
       "urn:EEC:M:CA:ComplexDataObjects:v1.10.4": "cacdo"}

def E(name, value=None, *children, **attrs):
    return (name, value, list(children), attrs)

def render(node, parent, ind):
    name, value, children, attrs = node
    path = f"{parent}/{name}"
    if path not in info:
        raise SystemExit(f"not in schema: {path}")
    tag = f"{PFX[info[path]['ns']]}:{name}"
    a = "".join(f' {k}="{escape(str(v))}"' for k, v in attrs.items())
    if not children:
        return f"{ind}<{tag}{a}>{escape(str(value))}</{tag}>\n"
    children = sorted(children, key=lambda c: order[f"{path}/{c[0]}"])  # stable for repeats
    inner = "".join(render(c, path, ind + "  ") for c in children)
    return f"{ind}<{tag}{a}>\n{inner}{ind}</{tag}>\n"

def subject(name, inn, country="KZ"):
    return [E("SubjectName", name), E("TaxpayerId", inn),
            E("SubjectAddressDetails", None, E("AddressKindCode", "1"), E("UnifiedCountryCode", country, codeListId="2021"),
              E("CityName", "Алматы" if country == "KZ" else "Шанхай"), E("StreetName", "ул. Примерная, 1"))]

def code(*units):
    return E("IdentificationMeansItemDetails", None,
             *[E("IdentificationMeansDataUnitDetails", None, E("AIId", ai), E("IdentificationMeansUnitCharacterValueId", v)) for ai, v in units])

def item(no, hs, desc, gross, net, price, extra=()):
    return E("GDGoodsItemDetails", None,
             E("ConsignmentItemOrdinal", no), E("CommodityCode", hs), E("GoodsDescriptionText", desc),
             E("UnifiedGrossMassMeasure", gross, measurementUnitCode="166", measurementUnitCodeListId="2016"),
             E("UnifiedNetMassMeasure", net, measurementUnitCode="166", measurementUnitCodeListId="2016"),
             E("OriginCountryDetails", None, E("CACountryCode", "CN", codeListId="2021")),
             E("CAValueAmount", price, currencyCode="USD", currencyCodeListId="2022"),
             E("PresentedDocDetails", None, E("DocKindCode", "04021", codeListId="2009"), E("DocId", "INV-2026-117"), E("DocCreationDate", "2026-10-20")),
             E("PresentedDocDetails", None, E("DocKindCode", "03011", codeListId="2009"), E("DocId", "CN-KZ-0042"), E("DocCreationDate", "2026-01-15")),
             *extra)

root = E("GoodsDeclaration", None,
    E("EDocCode", "R.055"), E("EDocId", str(uuid.UUID(int=0x1234))), E("EDocDateTime", "2026-11-02T10:15:00+05:00"),
    E("DeclarationKindCode", "ИМ"), E("CustomsProcedureCode", "40", codeListId="2006"), E("EDocIndicatorCode", "ЭД"),
    E("GoodsQuantity", "3"), E("CargoQuantity", "12"),
    E("DeclarantDetails", None, *subject("ТОО «Пример Импорт»", "123456789013")),
    E("GDGoodsShipmentDetails", None,
      E("ConsignorDetails", None, *subject("Example Trading Co., Ltd.", "91310000000000000X", "CN")),
      E("ConsigneeDetails", None, E("EqualIndicator", "1")),
      E("OriginCountryDetails", None, E("CACountryCode", "CN", codeListId="2021")),
      E("CAValueAmount", "15250.00", currencyCode="USD", currencyCodeListId="2022"),
      item(1, "6403999800", "Обувь мужская кожаная, 120 пар", "180.500", "150.000", "7200.00", extra=[
          E("CIMSignCode", "М"),
          E("DTIdentificationMeansDetails", None, E("InformationKindCode", "13"), E("CIMQuantity", "4"),
            E("IdentificationMeansDetails", None,
              # AggregationKindCode "1": conditional value; the list of marking levels is not in the bundled classifiers
              E("AggregationKindCode", "1"),
              E("IdentificationMeansListDetails", None,
                code(("01", "04601234567893"), ("21", "sN7<x01")),
                code(("01", "04601234567893"), ("21", "sN7x02")),
                code(("01", "04601234567893"), ("21", "sN7x03")),
                code(("01", "04601234567893"), ("21", "sN7x02")))))]),
      item(2, "8471300000", "Ноутбуки портативные, 10 шт.", "35.000", "28.000", "6500.00"),
      item(3, "6110209100", "Свитеры трикотажные хлопковые, 300 шт.", "95.000", "90.000", "1550.00", extra=[
          E("CIMSignCode", "М"),
          E("DTIdentificationMeansDetails", None, E("InformationKindCode", "13"), E("CIMQuantity", "300"),
            E("IdentificationMeansDetails", None,
              E("AggregationKindCode", "1"),
              E("IdentificationMeansRangeDetails", None,
                E("FirstIdentificationMeansItemDetails", None, E("IdentificationMeansDataUnitDetails", None, E("AIId", "01"), E("IdentificationMeansUnitCharacterValueId", "04601234500001"))),
                E("LastIdentificationMeansItemDetails", None, E("IdentificationMeansDataUnitDetails", None, E("AIId", "01"), E("IdentificationMeansUnitCharacterValueId", "04601234500300"))))))]),
    ))

ns = " ".join(f'xmlns:{p}="{u}"' for u, p in PFX.items())
xml = render(root, "", "").replace("<gd:GoodsDeclaration>", f"<gd:GoodsDeclaration {ns}>", 1)
out = DATA / "samples" / "synthetic_dt_r055_1.2.0.xml"
out.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!-- СИНТЕТИЧЕСКИЙ ПРИМЕР. Не является реальной декларацией. -->\n' + xml, encoding="utf-8")
print(out)
