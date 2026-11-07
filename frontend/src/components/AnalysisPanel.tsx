import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../api";
import type { AiSettings, CatalogEntry, Evidence, Jurisdiction, PanelResult } from "../types";
import ConceptPicker from "./ConceptPicker";
import ResultView from "./ResultView";
import ChecksView from "./ChecksView";
import AnswerView from "./AnswerView";
import AskBox from "./AskBox";

type Props = {
  fileId: string;
  shown: PanelResult | null;
  onResult: (r: PanelResult) => void;
  onQueryDone: () => void;
  onReveal: (ev: Evidence[]) => void;
};

type Preset = { id: string; label: string; run: () => Promise<PanelResult> };

const JURISDICTIONS: { value: Jurisdiction; label: string }[] = [
  { value: "KZ", label: "Казахстан" },
  { value: "RU", label: "Россия" },
  { value: "BY", label: "Беларусь" },
  { value: "AM", label: "Армения" },
  { value: "KG", label: "Кыргызстан" },
  { value: "", label: "не задана" },
];

export default function AnalysisPanel({ fileId, shown, onResult, onQueryDone, onReveal }: Props) {
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; retry: () => void } | null>(null);
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [concept, setConcept] = useState<string | null>(null);
  const [jurisdiction, setJurisdiction] = useState<Jurisdiction>("KZ");
  const [ai, setAi] = useState<AiSettings | null>(null);
  const seq = useRef(0);

  const loadCatalog = useCallback(() => {
    setCatalog(null);
    setCatalogError(null);
    api
      .getCatalog()
      .then(setCatalog)
      .catch((e) => setCatalogError(errorMessage(e)));
  }, []);

  useEffect(loadCatalog, [loadCatalog]);

  useEffect(() => {
    let alive = true;
    api
      .getAiSettings()
      .then((s) => alive && setAi(s))
      .catch(() => alive && setAi(null));
    return () => {
      alive = false;
    };
  }, []);

  const conceptRun = useCallback(
    (key: string, title: string) => async (): Promise<PanelResult> => ({
      kind: "concept",
      title,
      result: await api.getConcept(fileId, key),
    }),
    [fileId],
  );

  const presets: Preset[] = [
    {
      id: "goods",
      label: "Сколько товаров?",
      run: async () => ({ kind: "goods", title: "Сколько товаров?", result: await api.getGoods(fileId) }),
    },
    {
      id: "marking",
      label: "Коды маркировки",
      run: async () => ({ kind: "marking", title: "Коды маркировки", result: await api.getMarking(fileId) }),
    },
    { id: "c-consignor", label: "Отправитель", run: conceptRun("consignor", "Отправитель") },
    { id: "c-consignee", label: "Получатель", run: conceptRun("consignee", "Получатель") },
    { id: "c-declarant", label: "Декларант", run: conceptRun("declarant", "Декларант") },
    { id: "c-proc", label: "Таможенная процедура", run: conceptRun("customs_procedure", "Таможенная процедура") },
    { id: "c-docs", label: "Документы графы 44", run: conceptRun("presented_documents", "Документы графы 44") },
  ];

  const run = async (id: string, job: () => Promise<PanelResult>) => {
    if (running) return;
    const mine = ++seq.current;
    setRunning(id);
    setError(null);
    try {
      const r = await job();
      if (mine !== seq.current) return;
      onResult(r);
    } catch (e) {
      if (mine !== seq.current) return;
      setError({ message: errorMessage(e), retry: () => void run(id, job) });
    } finally {
      if (mine === seq.current) {
        setRunning(null);
        onQueryDone();
      }
    }
  };

  const selectedEntry = catalog?.find((c) => c.key === concept) ?? null;
  const jurLabel = JURISDICTIONS.find((j) => j.value === jurisdiction)?.label ?? "";
  const busy = running !== null;

  return (
    <section className="panel analysis" aria-label="Анализ" aria-busy={busy}>
      <div className="panel__head">
        <h2>Анализ</h2>
      </div>
      <div className="panel__body">
        <AskBox
          pending={running === "ask"}
          disabled={busy}
          settings={ai}
          onSend={(q) =>
            void run("ask", async () => ({ kind: "ask", title: q, result: await api.ask(fileId, q) }))
          }
        />

        <p className="field__label presets__title" id="presets-title">
          Быстрые запросы
        </p>
        <div className="presets" role="group" aria-labelledby="presets-title">
          {presets.map((p) => (
            <button
              key={p.id}
              type="button"
              className="btn btn--secondary"
              disabled={busy}
              aria-busy={running === p.id}
              onClick={() => void run(p.id, p.run)}
            >
              {running === p.id ? "Выполняется…" : p.label}
            </button>
          ))}
        </div>

        <div className="controls-row">
          <div className="controls-row__main">
            {catalog === null && !catalogError && <div className="skeleton skeleton--field" aria-hidden="true" />}
            {catalogError && (
              <div className="inline-error" role="alert">
                <p>Не удалось загрузить каталог: {catalogError}</p>
                <button type="button" className="btn btn--secondary" onClick={loadCatalog}>
                  Повторить
                </button>
              </div>
            )}
            {catalog && catalog.length === 0 && <p className="muted">Каталог понятий пуст.</p>}
            {catalog && catalog.length > 0 && <ConceptPicker catalog={catalog} value={concept} onChange={setConcept} />}
          </div>
          <button
            type="button"
            className="btn btn--secondary"
            disabled={busy || !selectedEntry}
            aria-busy={running === "concept"}
            onClick={() => selectedEntry && void run("concept", conceptRun(selectedEntry.key, selectedEntry.title))}
          >
            {running === "concept" ? "Выполняется…" : "Найти в файле"}
          </button>
        </div>

        <div className="controls-row">
          <div className="controls-row__main">
            <label className="field__label" htmlFor="jurisdiction">
              Юрисдикция
            </label>
            <select
              id="jurisdiction"
              className="input"
              value={jurisdiction}
              onChange={(e) => setJurisdiction(e.target.value as Jurisdiction)}
            >
              {JURISDICTIONS.map((j) => (
                <option key={j.value || "none"} value={j.value}>
                  {j.label}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            aria-busy={running === "checks"}
            onClick={() =>
              void run("checks", async () => ({
                kind: "checks",
                title: `Проверка по правилам: ${jurLabel}`,
                result: await api.getChecks(fileId, jurisdiction),
              }))
            }
          >
            {running === "checks" ? "Выполняется…" : "Проверить по правилам"}
          </button>
        </div>

        <p className="visually-hidden" role="status" aria-live="polite">
          {running ? "Выполняется запрос." : shown ? `Результат: ${shown.title}.` : ""}
        </p>

        <div className="result-area">
          {busy && !shown && <div className="skeleton skeleton--block" aria-hidden="true" />}
          {error && (
            <div className="inline-error" role="alert">
              <p>{error.message}</p>
              <button type="button" className="btn btn--secondary" onClick={error.retry}>
                Повторить
              </button>
            </div>
          )}
          {!shown && !busy && !error && (
            <p className="empty-note">Задайте вопрос, выберите быстрый запрос или понятие, чтобы увидеть результат.</p>
          )}
          {shown && shown.kind === "ask" && (
            <AnswerView key={`ask-${shown.title}-${shown.result.summary}`} title={shown.title} answer={shown.result} onReveal={onReveal} />
          )}
          {shown && shown.kind !== "checks" && shown.kind !== "ask" && (
            <ResultView key={shown.title + shown.result.tool} title={shown.title} result={shown.result} onReveal={onReveal} />
          )}
          {shown && shown.kind === "checks" && (
            <ChecksView title={shown.title} report={shown.result} onReveal={onReveal} />
          )}
        </div>
      </div>
    </section>
  );
}
