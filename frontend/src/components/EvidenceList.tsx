import { useState } from "react";
import type { Evidence } from "../types";
import { linesLabel } from "../format";

type Props = {
  evidence: Evidence[];
  onReveal: (ev: Evidence[]) => void;
  heading?: string;
  compact?: boolean;
};

const PAGE = 30;

export default function EvidenceList({ evidence, onReveal, heading = "Источники в файле", compact = false }: Props) {
  const [limit, setLimit] = useState(PAGE);
  if (evidence.length === 0) return null;
  const shown = evidence.slice(0, limit);
  const body = (
    <>
      <ul className="evidence">
        {shown.map((ev, i) => (
          <li key={`${ev.node_id}-${ev.start}-${i}`}>
            <button type="button" className="evidence__btn" onClick={() => onReveal([ev])}>
              <span className="evidence__lines num">{linesLabel(ev.line_start, ev.line_end)}</span>
              <span className="evidence__label">
                {ev.label} (<span className="code">{ev.tag}</span>)
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div className="evidence__actions">
        {evidence.length > 1 && (
          <button type="button" className="btn btn--ghost" onClick={() => onReveal(evidence)}>
            Показать все
          </button>
        )}
        {evidence.length > limit && (
          <button type="button" className="btn btn--ghost" onClick={() => setLimit((l) => l + PAGE)}>
            Ещё источники ({evidence.length - limit})
          </button>
        )}
      </div>
    </>
  );
  if (compact) return <div className="sources sources--compact">{body}</div>;
  return (
    <section className="sources" aria-label={heading}>
      <h4 className="sources__title">
        {heading} <span className="num">({evidence.length})</span>
      </h4>
      {body}
    </section>
  );
}
