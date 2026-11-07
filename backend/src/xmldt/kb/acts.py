"""Entry-into-force dates of amending acts, used to date editions that the markup leaves undated.

The consolidated text links every edition to its amending act (``/tamdoc/25kr0090/``). The act's
page states its general entry-into-force date («Дата вступления в силу 01.04.2026»). Acts with staged
entry (some annexes later) usually carry the staged date in the edition's own comment, which takes
precedence; the general date is a fallback and is recorded as such.

Only the extracted dates are stored (``data/norms/act_dates.json``), not the pages.
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import date
from pathlib import Path

import httpx

BASE = "https://www.alta.ru"
_DATE = re.compile(r"Дата вступления в силу\s+(\d{2})\.(\d{2})\.(\d{4})")
_HREF = re.compile(r"^/tamdoc/[0-9a-z]+/$")


def parse_entry_date(page: str) -> date | None:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S))))
    m = _DATE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def collect_hrefs(roots) -> set[str]:
    out = set()
    for root in roots:
        for h in root.xpath("//div[@class='ordw-urobr']/div[@class='new' or @class='old']/a/@href"):
            if _HREF.match(h):
                out.add(h)
    return out


def fetch_dates(hrefs: set[str], cache: Path, delay: float = 0.6) -> dict[str, str | None]:
    known: dict[str, str | None] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    todo = sorted(h for h in hrefs if h not in known)
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (xmldt kb builder)"}) as c:
        for h in todo:
            try:
                r = c.get(BASE + h)
            except httpx.HTTPError:
                continue
            if r.status_code in (403, 429):
                break  # the publisher asks us to slow down (captcha); stop, retry on a later run
            d = parse_entry_date(r.text) if r.status_code == 200 else None
            if d:
                known[h] = d.isoformat()  # only successes are cached, so failures are retried later
            cache.write_text(json.dumps(known, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
            time.sleep(delay)  # be polite to the publisher
    return known


def load_dates(cache: Path) -> dict[str, date]:
    if not cache.exists():
        return {}
    raw = json.loads(cache.read_text(encoding="utf-8"))
    return {h: date.fromisoformat(d) for h, d in raw.items() if d}
