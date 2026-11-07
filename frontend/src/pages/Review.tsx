import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { errorMessage } from "../api";
import { reviewApi } from "../reviewApi";
import type { ReviewCounts, ReviewItem, ReviewList, ReviewStatus, ReviewType } from "../reviewApi";

const TYPES: { value: ReviewType; label: string }[] = [
  { value: "rule", label: "Правила" },
  { value: "manual", label: "Не автоматизированные" },
  { value: "concept", label: "Понятия (графы ↔ элементы)" },
  { value: "binding", label: "Привязки классификаторов" },
  { value: "edition", label: "Редакции норм" },
];

const STATUSES: { value: ReviewStatus; label: string }[] = [
  { value: "pending", label: "Ожидает" },
  { value: "approved", label: "Подтверждено" },
  { value: "rejected", label: "Отклонено" },
  { value: "needs_changes", label: "Нужна доработка" },
];

const STATUS_LABEL = Object.fromEntries(STATUSES.map((s) => [s.value, s.label])) as Record<ReviewStatus, string>;
const isType = (v: string | null): v is ReviewType => TYPES.some((t) => t.value === v);
const isStatus = (v: string | null): v is ReviewStatus => STATUSES.some((s) => s.value === v);

type Decision = Exclude<ReviewStatus, "pending">;

function moveCount(counts: ReviewCounts, type: ReviewType, from: ReviewStatus, to: ReviewStatus): ReviewCounts {
  if (from === to) return counts;
  const row = { ...(counts[type] ?? {}) };
  row[from] = Math.max(0, (row[from] ?? 0) - 1);
  row[to] = (row[to] ?? 0) + 1;
  return { ...counts, [type]: row };
}

function ItemRow({
  item,
  canReview,
  onDecide,
}: {
  item: ReviewItem;
  canReview: boolean;
  onDecide: (item: ReviewItem, status: Decision, comment?: string) => Promise<string | null>;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<Decision | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detailsId = `review-details-${item.key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  const commentId = `${detailsId}-comment`;

  const submit = async (status: Decision, text?: string) => {
    setBusy(true);
    setError(null);
    const err = await onDecide(item, status, text);
    setBusy(false);
    if (err) setError(err);
    else {
      setPending(null);
      setComment("");
    }
  };

  const choose = (status: Decision) => {
    if (status === "approved") {
      void submit(status);
    } else {
      setPending(status);
      setError(null);
    }
  };

  const confirm = () => {
    if (!pending) return;
    if (!comment.trim()) {
      setError("Укажите комментарий: он обязателен для этого решения.");
      return;
    }
    void submit(pending, comment.trim());
  };

  return (
    <li className="review-item">
      <div className="review-item__row">
        <div className="review-item__main">
          <h3 className="review-item__title">{item.title}</h3>
          {item.stale && <p className="review-item__stale">изменилось после проверки</p>}
        </div>
        <p className="review-item__source">{item.source ?? "нет источника"}</p>
        <p className="review-item__status">
          <span className={`badge badge--${item.status}`}>{STATUS_LABEL[item.status]}</span>
        </p>
      </div>

      {item.comment && (
        <p className="review-item__comment">
          Комментарий{item.reviewer ? ` (${item.reviewer})` : ""}: {item.comment}
        </p>
      )}

      <div className="review-item__tools">
        <button
          type="button"
          className="btn btn--ghost"
          aria-expanded={open}
          aria-controls={detailsId}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Скрыть подробности" : "Подробности"}
        </button>
        {canReview && (
          <>
            <button type="button" className="btn btn--primary" disabled={busy} onClick={() => choose("approved")}>
              Подтвердить
            </button>
            <button type="button" className="btn btn--danger" disabled={busy} onClick={() => choose("rejected")}>
              Отклонить
            </button>
            <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => choose("needs_changes")}>
              На доработку
            </button>
          </>
        )}
      </div>

      {canReview && pending && (
        <div className="review-item__form">
          <label className="field__label" htmlFor={commentId}>
            {pending === "rejected" ? "Почему отклонено" : "Что нужно доработать"}
          </label>
          <textarea
            id={commentId}
            className="input review-item__textarea"
            rows={3}
            value={comment}
            aria-invalid={!!error && !comment.trim()}
            onChange={(e) => setComment(e.target.value)}
          />
          <div className="review-item__tools">
            <button type="button" className="btn btn--primary" disabled={busy} aria-busy={busy} onClick={confirm}>
              {busy ? "Сохранение…" : "Сохранить решение"}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={() => {
                setPending(null);
                setError(null);
              }}
            >
              Отмена
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="review-item__error text-error" role="alert">
          {error}
        </p>
      )}

      {open && (
        <div id={detailsId} className="review-item__details">
          {item.text && <p className="review-item__text">{item.text}</p>}
          {item.proposal && <pre className="code review-item__pre">{JSON.stringify(item.proposal, null, 2)}</pre>}
          {!item.text && !item.proposal && <p className="muted">Подробностей нет.</p>}
        </div>
      )}
    </li>
  );
}

export default function Review() {
  const [params, setParams] = useSearchParams();
  const typeParam = params.get("type");
  const statusParam = params.get("status");
  const type = isType(typeParam) ? typeParam : "";
  const status = isStatus(statusParam) ? statusParam : "";
  const q = params.get("q") ?? "";

  const [search, setSearch] = useState(q);
  const [data, setData] = useState<ReviewList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const dataRef = useRef<ReviewList | null>(null);
  dataRef.current = data;

  const setParam = useCallback(
    (key: string, value: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // debounce search input into the URL
  useEffect(() => {
    if (search === q) return;
    const t = window.setTimeout(() => setParam("q", search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search, q, setParam]);

  const [reloadTick, setReloadTick] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    setData(null);
    reviewApi
      .list({ type, status, q }, ctl.signal)
      .then(setData)
      .catch((e) => {
        if (ctl.signal.aborted) return;
        setError(errorMessage(e));
      });
    return () => ctl.abort();
  }, [type, status, q, reloadTick]);

  const decide = useCallback(
    async (item: ReviewItem, next: Decision, comment?: string): Promise<string | null> => {
      const before = dataRef.current;
      if (!before) return null;
      const prev = item;
      setData({
        ...before,
        counts: moveCount(before.counts, item.type, item.status, next),
        items: before.items.map((it) =>
          it.key === item.key ? { ...it, status: next, stale: false, comment: comment ?? null } : it,
        ),
      });
      try {
        await reviewApi.decide(item.key, next, comment);
        return null;
      } catch (e) {
        setData((cur) =>
          cur
            ? {
                ...cur,
                counts: moveCount(cur.counts, prev.type, next, prev.status),
                items: cur.items.map((it) => (it.key === prev.key ? prev : it)),
              }
            : cur,
        );
        return errorMessage(e);
      }
    },
    [],
  );

  const download = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const body = await reviewApi.exportDecisions();
      const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "review-decisions.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(errorMessage(e));
    } finally {
      setExporting(false);
    }
  };

  const visible = data ? data.items : [];

  return (
    <main className="page review">
      <header className="page__head">
        <p>
          <Link to="/">На главную</Link>
        </p>
        <h1>Экспертная проверка базы знаний</h1>
        <p className="lead">
          Правила, связи граф с элементами XML, привязки классификаторов и даты редакций подготовлены ИИ-экспертом.
          Подтвердите или отклоните каждую позицию — решения сразу отражаются в проверках.
        </p>
      </header>

      <section aria-label="Сводка по статусам" className="table-wrap" tabIndex={0}>
        <table className="table review-summary">
          <thead>
            <tr>
              <th scope="col">Раздел</th>
              {STATUSES.map((s) => (
                <th key={s.value} scope="col" className="num">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {TYPES.map((t) => (
              <tr key={t.value}>
                <th scope="row">{t.label}</th>
                {STATUSES.map((s) => (
                  <td key={s.value} className="num">
                    {data ? (data.counts[t.value]?.[s.value] ?? 0) : "–"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-label="Фильтры" className="review-filters">
        <div className="review-tabs" role="group" aria-label="Раздел">
          <button type="button" className="btn btn--ghost review-tab" aria-pressed={type === ""} onClick={() => setParam("type", "")}>
            Все
          </button>
          {TYPES.map((t) => (
            <button
              key={t.value}
              type="button"
              className="btn btn--ghost review-tab"
              aria-pressed={type === t.value}
              onClick={() => setParam("type", t.value)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="review-filters__row">
          <div>
            <label className="field__label" htmlFor="review-status">
              Статус
            </label>
            <select id="review-status" className="input" value={status} onChange={(e) => setParam("status", e.target.value)}>
              <option value="">Любой</option>
              {STATUSES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="field__label" htmlFor="review-q">
              Поиск
            </label>
            <input
              id="review-q"
              className="input"
              type="search"
              autoComplete="off"
              spellCheck={false}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="review-filters__export">
            <button type="button" className="btn btn--secondary" disabled={exporting} aria-busy={exporting} onClick={() => void download()}>
              Скачать решения (JSON)
            </button>
          </div>
        </div>
        {exportError && (
          <p className="text-error" role="alert">
            {exportError}
          </p>
        )}
      </section>

      {data && !data.can_review && (
        <div className="note" role="note">
          <p>
            Подтверждать могут эксперты из списка XMLDT_REVIEWERS после входа по e-mail.{" "}
            <Link to="/login">Войти</Link>
          </p>
        </div>
      )}

      <section aria-label="Позиции" aria-busy={!data && !error}>
        {error && (
          <div className="inline-error" role="alert">
            <p>{error}</p>
            <button type="button" className="btn btn--secondary" onClick={() => setReloadTick((n) => n + 1)}>
              Повторить
            </button>
          </div>
        )}
        {!data && !error && (
          <div aria-label="Загрузка">
            <div className="skeleton skeleton--row" />
            <div className="skeleton skeleton--row" />
            <div className="skeleton skeleton--row" />
            <div className="skeleton skeleton--row" />
          </div>
        )}
        {data && visible.length === 0 && <p className="empty-note">Ничего не найдено по фильтрам</p>}
        {data && visible.length > 0 && (
          <>
            <p className="field__hint" role="status">
              Показано <span className="code">{visible.length}</span> из <span className="code">{data.total}</span>
              {data.total > visible.length ? ". Уточните фильтры, чтобы увидеть остальные." : ""}
            </p>
            <ul className="review-list">
              {visible.map((it) => (
                <ItemRow key={it.key} item={it} canReview={data.can_review} onDecide={decide} />
              ))}
            </ul>
          </>
        )}
      </section>
    </main>
  );
}
