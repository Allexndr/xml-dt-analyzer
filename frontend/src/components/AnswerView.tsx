import { useState } from "react";
import type { AskAnswer, AskFactKind, Evidence } from "../types";
import EvidenceList from "./EvidenceList";

type Props = {
  title: string;
  answer: AskAnswer;
  onReveal: (ev: Evidence[]) => void;
};

const KIND_LABEL: Record<AskFactKind, string> = {
  xml_fact: "Факт из XML",
  format_requirement: "Требование формата",
  filling_rule: "Правило заполнения",
  interpretation: "Интерпретация ИИ",
};

function footer(a: AskAnswer): string {
  return a.mode === "rules" ? "Ответ сформирован без ИИ" : `Модель: ${a.model ?? "не указана"}`;
}

export default function AnswerView({ title, answer, onReveal }: Props) {
  const [allOpen, setAllOpen] = useState(false);
  const allEvidence = answer.facts.flatMap((f) => f.evidence);

  return (
    <article className="result answer" aria-label={title}>
      <h3>{answer.question || title}</h3>
      <p className={`answer__summary${answer.unsupported ? " answer__summary--guide" : ""}`}>{answer.summary}</p>
      {answer.unsupported && (
        <p className="muted">Используйте быстрые запросы выше или переформулируйте вопрос.</p>
      )}

      {answer.facts.length > 0 && (
        <ul className="facts">
          {answer.facts.map((f, i) => (
            <li key={i} className={`fact${f.kind === "interpretation" ? " fact--interpretation" : ""}`}>
              <p className="fact__kind">{KIND_LABEL[f.kind] ?? f.kind}</p>
              <p className="fact__text">{f.text}</p>
              {f.evidence.length > 0 && (
                <EvidenceList evidence={f.evidence} onReveal={onReveal} heading="Источники факта" compact />
              )}
              {(f.sources ?? []).length > 0 && (
                <ul className="norm-sources" aria-label="Нормативные источники">
                  {(f.sources ?? []).map((src) => (
                    <li key={src.id}>
                      <details className="norm-source">
                        <summary>{src.ref}</summary>
                        <p className="norm-source__meta">Редакция на {src.as_of}</p>
                        <p className="norm-source__text">{src.text}</p>
                      </details>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}

      {answer.warnings.length > 0 && (
        <div className="notes">
          {answer.warnings.map((w, i) => (
            <p key={i} className="note">
              {w}
            </p>
          ))}
        </div>
      )}

      {allEvidence.length > 0 && (
        <div className="answer__all">
          <button
            type="button"
            className="btn btn--ghost"
            aria-expanded={allOpen}
            onClick={() => setAllOpen((o) => !o)}
          >
            {allOpen ? "Скрыть все источники" : `Показать все источники (${allEvidence.length})`}
          </button>
          {allOpen && <EvidenceList evidence={allEvidence} onReveal={onReveal} heading="Все источники ответа" />}
        </div>
      )}

      <div className="answer__foot">
        <p className="muted">{footer(answer)}</p>
        {answer.mode === "llm" && !answer.verified && (
          <p className="note">Ответ ИИ не прошёл проверку, показан ответ по данным файла</p>
        )}
      </div>
    </article>
  );
}
