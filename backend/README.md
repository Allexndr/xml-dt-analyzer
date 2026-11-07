# xmldt — ядро анализатора XML-деклараций на товары (фаза 0)

Детерминированное ядро без ИИ: индекс XML с точными координатами, схемы R.055, база знаний по Решению ЕЭК № 75, инструменты фактов.

```bash
uv sync
uv run pytest -q                                   # тесты (XMLDT_SLOW=1 — нагрузочный)
uv run python -m xmldt.kb.build --data ../data     # пересобрать базу знаний и проверить каталог понятий
uv run python -m xmldt.facts.demo ../data/samples/synthetic_dt_r055_1.2.0.xml ../data
uv run python -m xmldt.rules.demo ../data/samples/synthetic_dt_r055_1.2.0.xml ../data KZ   # проверки по правилам
```

Данные: `../data` (схемы, нормативные тексты, каталог понятий, синтетический пример). Отчёт: `../docs/phase0-report.md`.
