import type { CheckReport, CheckResultItem, Evidence, Status } from "../types";
import { isRecord, scalar } from "../format";
import EvidenceList from "./EvidenceList";

type Props = {
  title: string;
  report: CheckReport;
  onReveal: (ev: Evidence[]) => void;
};

export const STATUS_LABEL: Record<Status, string> = {
  violation: "Нарушение",
  needs_info: "Нужны данные",
  pass: "ОК",
  not_applicable: "Не применимо",
  not_implemented: "Не автоматизировано",
};

const ORDER: Status[] = ["violation", "needs_info", "pass", "not_applicable", "not_implemented"];

const JURISDICTIONS: Record<string, string> = {
  KZ: "Казахстан",
  RU: "Россия",
  BY: "Беларусь",
  AM: "Армения",
  KG: "Кыргызстан",
};

export function legalRef(ref: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof ref.doc === "string" && ref.doc) parts.push(ref.doc);
  if (ref.table) parts.push(`табл. ${scalar(ref.table)}`);
  if (ref.position) parts.push(`поз. ${scalar(ref.position)}`);
  if (ref.classifier) parts.push(`классификатор ${scalar(ref.classifier)}`);
  if (parts.length === 0 && ref.basis) parts.push(scalar(ref.basis));
  return parts.join(", ");
}

function RuleCard({ r, onReveal }: { r: CheckResultItem; onReveal: (ev: Evidence[]) => void }) {
  const ref = isRecord(r.legal_ref) ? legalRef(r.legal_ref) : "";
  const asOf = isRecord(r.legal_ref) && r.legal_ref.as_of ? scalar(r.legal_ref.as_of) : "";
  return (
    <li className={`rule rule--${r.status}`}>
      <p className="rule__text">{r.text}</p>
      <p className="rule__meta muted">
        Правило <span className="code">{r.rule_id}</span>
        {r.severity === "warning" ? ", предупреждение" : ""}
      </p>
      {ref && (
        <p className="rule__ref">
          {ref}
          {asOf ? <span className="muted"> (редакция на {asOf})</span> : null}
        </p>
      )}
      {r.findings.length > 0 && (
        <ul className="findings" aria-label="Найденные случаи">
          {r.findings.map((f, i) => (
            <li key={i} className="finding">
              <p>{f.message}</p>
              {f.value != null && f.value !== "" && (
                <p className="finding__value">
                  Значение: <span className="code">{f.value}</span>
                </p>
              )}
              <EvidenceList evidence={f.evidence} onReveal={onReveal} compact />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function ChecksView({ title, report, onReveal }: Props) {
  const by = report.summary.by_status;
  const groups = ORDER.map((s) => ({ status: s, items: report.results.filter((r) => r.status === s) })).filter(
    (g) => g.items.length > 0,
  );
  const allEvidence = report.results.flatMap((r) => r.findings.flatMap((f) => f.evidence));
  const jur = report.jurisdiction ? (JURISDICTIONS[report.jurisdiction] ?? report.jurisdiction) : "не задана";
  return (
    <article className="result" aria-label={`Результат: ${title}`}>
      <h3>{title}</h3>
      <p className="muted">
        Юрисдикция: {jur}. Дата проверки: <span className="num">{report.check_date}</span>.
      </p>
      <dl className="stats" aria-label="Сводка по статусам">
        {ORDER.map((s) => (
          <div key={s} className={`stat stat--${s}`}>
            <dt>{STATUS_LABEL[s]}</dt>
            <dd className="num code">{by[s] ?? 0}</dd>
          </div>
        ))}
      </dl>
      <p className="muted">
        Правил всего: <span className="num code">{report.summary.rules}</span>, автоматизировано:{" "}
        <span className="num code">{report.summary.automated}</span>.
      </p>
      {groups.length === 0 && <p className="empty-note">Нарушений и запросов данных нет.</p>}
      {groups.map((g) => (
        <section key={g.status} className="group" aria-label={STATUS_LABEL[g.status]}>
          <h4 className="group__title">
            {STATUS_LABEL[g.status]} <span className="num code">({g.items.length})</span>
          </h4>
          <ul className="rules">
            {g.items.map((r) => (
              <RuleCard key={r.rule_id + r.path} r={r} onReveal={onReveal} />
            ))}
          </ul>
        </section>
      ))}
      {allEvidence.length > 0 && (
        <div className="evidence__actions">
          <button type="button" className="btn btn--ghost" onClick={() => onReveal(allEvidence)}>
            Показать все источники проверки
          </button>
        </div>
      )}
    </article>
  );
}
