"""Concept catalog: business terms -> schema paths for one schema version."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = "/GoodsDeclaration/"


@dataclass
class Concept:
    key: str
    title: str
    synonyms: list[str]
    graphs: list[str]
    level: str
    paths: list[str]  # absolute, by local names
    kind: str
    classifier: str | None = None
    pitfalls: list[str] = field(default_factory=list)
    review: str = "pending"


@dataclass
class Catalog:
    schema: dict
    concepts: dict[str, Concept]

    def by_graph(self, graph: str) -> list[Concept]:
        g = graph.strip().upper()
        return [c for c in self.concepts.values() if any(x == g or x.split(".")[0] == g for x in c.graphs)]


def load_catalog(path: Path) -> Catalog:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, Concept] = {}
    for c in raw["concepts"]:
        if c["key"] in out:
            raise ValueError(f"duplicate concept key {c['key']}")
        out[c["key"]] = Concept(
            key=c["key"],
            title=c["title"],
            synonyms=c.get("synonyms", []),
            graphs=[str(g) for g in c.get("graphs", [])],
            level=c["level"],
            paths=[p if p.startswith("/") else ROOT + p for p in c["paths"]],  # "/..." = outside R.055 (envelope)
            kind=c["kind"],
            classifier=c.get("classifier"),
            pitfalls=c.get("pitfalls", []),
            review=c.get("review", "pending"),
        )
    return Catalog(schema=raw["schema"], concepts=out)


def validate(catalog: Catalog, schema_paths: set[str]) -> list[str]:
    """Return problems: every concept path must exist in the XSD tree of this version."""
    problems = []
    for c in catalog.concepts.values():
        for p in c.paths:
            if not p.startswith(ROOT):
                continue  # national envelope element (e.g. Keden), not part of the EEC schema
            if p not in schema_paths:
                problems.append(f"{c.key}: path not in schema: {p}")
        if c.level not in ("declaration", "shipment", "goods"):
            problems.append(f"{c.key}: bad level {c.level}")
    return problems
