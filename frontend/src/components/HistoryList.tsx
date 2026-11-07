import type { HistoryEntry } from "../types";
import { formatDate } from "../format";

export type HistoryState =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; items: HistoryEntry[] };

type Props = {
  history: HistoryState;
  activeId: string | number | null;
  onOpen: (e: HistoryEntry) => void;
  onRetry: () => void;
};

export default function HistoryList({ history, activeId, onOpen, onRetry }: Props) {
  return (
    <section className="panel history" aria-label="История запросов" aria-busy={history.state === "loading"}>
      <div className="panel__head">
        <h2>История запросов</h2>
      </div>
      <div className="panel__body">
        {history.state === "loading" && (
          <div aria-hidden="true">
            <div className="skeleton skeleton--row" />
            <div className="skeleton skeleton--row" />
          </div>
        )}
        {history.state === "error" && (
          <div className="inline-error" role="alert">
            <p>{history.message}</p>
            <button type="button" className="btn btn--secondary" onClick={onRetry}>
              Повторить
            </button>
          </div>
        )}
        {history.state === "ready" && history.items.length === 0 && (
          <p className="empty-note">Запросов по этому файлу пока нет.</p>
        )}
        {history.state === "ready" && history.items.length > 0 && (
          <ul className="history__list">
            {history.items.map((h) => (
              <li key={h.id}>
                <button
                  type="button"
                  className={`history__row${h.id === activeId ? " is-current" : ""}`}
                  aria-current={h.id === activeId ? "true" : undefined}
                  onClick={() => onOpen(h)}
                >
                  <span className="history__time num code">{formatDate(h.at)}</span>
                  <span className="history__title">{h.title}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
