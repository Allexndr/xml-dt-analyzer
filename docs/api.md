# HTTP API v0 (прототип)

Базовый путь `/api`. JSON в UTF-8. Сессия анонимная: сервер ставит httpOnly-cookie `xmldt_sid`, и каждый файл принадлежит своей сессии. Чужой файл отвечает 404, его существование не раскрывается.

## Общие типы

```ts
type Evidence = {
  node_id: number;
  line_start: number;  // 1-based, CodeMirror convention (\r\n, \n, \r = break)
  line_end: number;
  start: number;       // UTF-16 offset into the text returned by GET /files/{id}/text
  end: number;
  tag: string;         // "cacdo:GDGoodsItemDetails"
  label: string;       // human-readable, Russian
};

type ToolResult = { tool: string; value: Record<string, unknown>; evidence: Evidence[]; warnings: string[] };

type FileMeta = {
  id: string; name: string; size: number; sha256: string; uploaded_at: string; // ISO
  encoding: string;
  status: "ready" | "error";
  error: null | { message: string; line: number; column: number; offset_utf16: number; kind: string };
  schema: null | { code: string; version: string; namespace: string; supported: boolean };
  xsd_valid: boolean | null;    // null if schema unsupported or file not well-formed
  xsd_error_count: number;
  goods_items: number | null;
  expires_at: string;           // anonymous retention (ISO)
};
```

## Файлы

| Метод | Путь | Ответ |
|---|---|---|
| POST | `/files` (multipart, поле `file`) | `201 FileMeta`. Файл некорректный XML → всё равно `201` со `status:"error"` и `error`. `413` больше лимита (50 МБ), `415` не XML по расширению/типу |
| GET | `/files` | `FileMeta[]`, новые первыми |
| GET | `/files/{id}` | `FileMeta` |
| GET | `/files/{id}/text` | `text/plain; charset=utf-8`, декодированный текст файла без изменений (BOM снят). Смещения `Evidence` указывают в этот текст |
| GET | `/files/{id}/raw` | исходные байты, `Content-Disposition: attachment` |
| DELETE | `/files/{id}` | `204` (файл, индекс и история удаляются) |
| GET | `/files/{id}/validation` | `{ wellformed_error: FileMeta["error"], xsd: {line:number, column:number, message:string, path:string\|null}[] }` |

## Факты (без ИИ)

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/files/{id}/facts/goods` | `ToolResult` (`value.goods_items`, `value.declared_goods_quantity`) |
| GET | `/files/{id}/facts/marking` | `ToolResult` (см. `marking_stats`) |
| GET | `/catalog` | `{key, title, graphs: string[], level, synonyms: string[]}[]` |
| GET | `/files/{id}/concepts/{key}` | `ToolResult` (`value.title`, `value.graphs`, `value.found`, `value.values[].fields[]`) |

## Проверки

`GET /files/{id}/checks?jurisdiction=KZ|RU|BY|AM|KG` (без параметра — юрисдикция не задана)

```ts
type CheckReport = {
  jurisdiction: string | null; check_date: string;
  summary: { rules: number; automated: number; by_status: Record<Status, number>; violations_by_severity: Record<string, number> };
  results: {
    rule_id: string; kind: string;
    status: "violation" | "needs_info" | "pass" | "not_applicable" | "not_implemented";
    severity: "error" | "warning"; path: string;
    legal_ref: Record<string, unknown>;  // {doc, table, position, as_of} | {doc, classifier, as_of} | {basis}
    text: string;
    findings: { message: string; value: string | null; evidence: Evidence[] }[];
  }[];  // по умолчанию только violation и needs_info; ?all=1 — все
};
```

## История запросов

`GET /files/{id}/history` → `{id, at, kind: "goods"|"marking"|"concept"|"checks", title, request: object, result: object}[]` — последние 100. Сервер пишет запись при каждом вызове фактов и проверок. Повторное открытие показывает сохранённый результат, а не пересчитывает его.

## Ошибки

`{ "detail": "<сообщение на русском>" }` с кодом 4xx/5xx.

## Вопросы на естественном языке

`POST /api/files/{id}/ask` с телом `{ "question": string }` (до 1000 символов).

```ts
type AskFact = {
  text: string;                 // числа и значения подставлены сервером из результатов инструментов
  kind: "xml_fact" | "format_requirement" | "filling_rule" | "interpretation";
  evidence: Evidence[];
};
type AskAnswer = {
  question: string;
  mode: "llm" | "rules";        // rules — ответ без ИИ (встроенный разбор вопроса)
  model: string | null;         // "openai:gpt-…" | "ollama:qwen2.5:14b" | null
  summary: string;              // краткий ответ
  facts: AskFact[];
  warnings: string[];
  verified: boolean;            // false — ответ ИИ не прошёл проверку, показан шаблонный ответ
  unsupported: boolean;         // вопрос не распознан: summary объясняет, что можно спросить
  tool_calls: { tool: string; args: Record<string, unknown> }[];
};
```

Ответ пишется в историю (`kind: "ask"`, `title` = вопрос). Контекст диалога хранится на сервере по файлу: «а у каких из них…» после «сколько товаров» понимается без пересказа.

## Настройки ИИ (на сессию)

`GET /api/settings/ai` → `AiSettings`; `PUT /api/settings/ai` с `AiSettingsInput` → `AiSettings`; `POST /api/settings/ai/test` → `{ ok: boolean; message: string; models?: string[] }`.

```ts
type AiSettings = {
  mode: "off" | "api" | "local";  // off — только встроенный разбор без ИИ
  provider: "openai" | "anthropic" | "openai_compatible" | "ollama";
  base_url: string | null;        // для openai_compatible / ollama
  model: string | null;
  has_api_key: boolean;           // ключ никогда не возвращается
  consent_external: boolean;      // согласие на передачу фрагментов XML внешнему ИИ (обязательно для mode=api)
  local_allowed: boolean;         // сервер разрешает обращаться к локальной модели (только при установке у клиента)
};
type AiSettingsInput = Partial<Omit<AiSettings, "has_api_key" | "local_allowed">> & { api_key?: string | null };
```

Ключ хранится только в памяти сервера на время сессии и не пишется на диск или в логи. Внешнему ИИ уходят только вопрос, каталог понятий и результаты инструментов — не файл целиком.

## Вход по e-mail (необязательный)

| Метод | Путь | Ответ |
|---|---|---|
| POST | `/api/auth/request` `{email}` | `202 {status:"sent"}` — одинаково для любых адресов; код из 6 цифр живёт 10 минут. `429` при превышении лимитов (3 запроса на адрес за 10 минут, 20 с IP в час). `dev_code` в ответе только при `XMLDT_AUTH_DEV_ECHO=1` |
| POST | `/api/auth/verify` `{email, code}` | `200 {email}` + httpOnly-cookie `xmldt_uid` на 30 дней; файлы анонимной сессии переходят в аккаунт. `400` при неверном коде (не более 5 попыток) |
| GET | `/api/auth/me` | `{email}` или `{email:null}` |
| POST | `/api/auth/logout` | `204` |

Письма отправляются через SMTP (`XMLDT_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM`). Без SMTP код пишется в журнал сервера с пометкой DEV MODE.

## Локальный ИИ через браузер

WebSocket `/api/relay` (тот же origin, cookie сессии). Пока открыта страница сервиса и в настройках выбран режим `local` в облачной установке, сервер отправляет браузеру `{id, op:"fetch", method, url, body}`, браузер выполняет запрос к модели на `localhost` и возвращает `{id, status, json}` или `{id, error}`. Браузер отказывается обращаться к любому адресу, кроме loopback. `GET /api/settings/ai` возвращает `relay_connected`.

## Экспертная проверка базы знаний

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/review/items?type=&status=&q=` | `{items, total, counts, can_review}`; типы: `rule`, `manual`, `concept`, `binding`, `edition` |
| POST | `/api/review/items/{key}` `{status, comment?}` | только для e-mail из `XMLDT_REVIEWERS`; статусы `approved`, `rejected`, `needs_changes` (комментарий обязателен), `pending` |
| GET | `/api/review/export` | все решения с автором и временем |

Решение по правилу видно в результатах проверок: `legal_ref.review = "approved:<email>"`. Если правило изменилось после проверки, решение считается устаревшим (`stale`).
