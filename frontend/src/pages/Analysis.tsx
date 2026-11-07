import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../api";
import type { AskAnswer, CheckReport, Evidence, FileMeta, HistoryEntry, PanelResult, ToolResult, Validation } from "../types";
import { schemaLabel } from "../format";
import XmlViewer, { type XmlViewerHandle } from "../components/XmlViewer";
import SplitPane from "../components/SplitPane";
import AnalysisPanel from "../components/AnalysisPanel";
import HistoryList, { type HistoryState } from "../components/HistoryList";

type Loadable<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: T };

function entryToResult(e: HistoryEntry): PanelResult {
  if (e.kind === "checks") return { kind: "checks", title: e.title, result: e.result as unknown as CheckReport };
  if (e.kind === "ask") return { kind: "ask", title: e.title, result: e.result as unknown as AskAnswer };
  return { kind: e.kind, title: e.title, result: e.result as unknown as ToolResult };
}

export default function Analysis() {
  const { id = "" } = useParams();
  const viewer = useRef<XmlViewerHandle>(null);
  const [file, setFile] = useState<Loadable<FileMeta>>({ state: "loading" });
  const [text, setText] = useState<Loadable<string>>({ state: "loading" });
  const [validation, setValidation] = useState<Validation | null>(null);
  const [xsdOpen, setXsdOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(false);
  const [shown, setShown] = useState<PanelResult | null>(null);
  const [activeHistory, setActiveHistory] = useState<string | number | null>(null);
  const [history, setHistory] = useState<HistoryState>({ state: "loading" });
  const autoRevealed = useRef(false);

  const loadFile = useCallback(() => {
    setFile({ state: "loading" });
    api
      .getFile(id)
      .then((data) => setFile({ state: "ready", data }))
      .catch((e) => setFile({ state: "error", message: errorMessage(e) }));
  }, [id]);

  const loadText = useCallback(() => {
    setText({ state: "loading" });
    api
      .getText(id)
      .then((data) => setText({ state: "ready", data }))
      .catch((e) => setText({ state: "error", message: errorMessage(e) }));
  }, [id]);

  const loadHistory = useCallback(() => {
    api
      .getHistory(id)
      .then((items) => setHistory({ state: "ready", items }))
      .catch((e) => setHistory({ state: "error", message: errorMessage(e) }));
  }, [id]);

  useEffect(() => {
    autoRevealed.current = false;
    setShown(null);
    setActiveHistory(null);
    setValidation(null);
    setXsdOpen(false);
    setHistory({ state: "loading" });
    loadFile();
    loadText();
    loadHistory();
  }, [id, loadFile, loadText, loadHistory]);

  const meta = file.state === "ready" ? file.data : null;

  useEffect(() => {
    if (!meta || meta.xsd_error_count === 0) return;
    api
      .getValidation(id)
      .then(setValidation)
      .catch(() => setValidation({ wellformed_error: null, xsd: [] }));
  }, [id, meta]);

  // Файл с ошибкой разбора: сразу показываем строку ошибки.
  useEffect(() => {
    if (autoRevealed.current || !meta || text.state !== "ready" || !meta.error) return;
    autoRevealed.current = true;
    viewer.current?.revealLine(meta.error.line);
  }, [meta, text]);

  const reveal = useCallback((ev: Evidence[]) => viewer.current?.revealEvidence(ev), []);

  const xsdStatus = () => {
    if (!meta) return null;
    if (meta.status === "error") return <span className="status status--error">Файл не является корректным XML</span>;
    if (meta.xsd_valid === true) return <span className="status status--ok">Соответствует XSD</span>;
    if (meta.xsd_valid === false)
      return (
        <button
          type="button"
          className="btn btn--link status status--error"
          aria-expanded={xsdOpen}
          aria-controls="xsd-list"
          onClick={() => setXsdOpen((o) => !o)}
        >
          Ошибок XSD: {meta.xsd_error_count}
        </button>
      );
    return <span className="status">Проверка по XSD не выполнялась</span>;
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__row">
          <Link to="/" className="btn btn--secondary">
            На главную
          </Link>
          <Link to="/settings" className="btn btn--secondary">
            Настройки ИИ
          </Link>
          <h1 className="topbar__name" title={meta?.name}>
            {meta ? meta.name : file.state === "error" ? "Файл недоступен" : "Загрузка…"}
          </h1>
          {meta && (
            <dl className="topbar__meta">
              <div>
                <dt>Схема</dt>
                <dd className="code">{schemaLabel(meta.schema)}</dd>
              </div>
              <div>
                <dt>XSD</dt>
                <dd>{xsdStatus()}</dd>
              </div>
            </dl>
          )}
        </div>
        {file.state === "error" && (
          <div className="inline-error" role="alert">
            <p>{file.message}</p>
            <button type="button" className="btn btn--secondary" onClick={loadFile}>
              Повторить
            </button>
          </div>
        )}
        {meta?.schema?.note && (
          <div className="banner" role="status">
            <p>{meta.schema.note}</p>
          </div>
        )}
        {meta?.error && (
          <div className="banner banner--error" role="alert">
            <p>
              Ошибка разбора XML: {meta.error.message}. Строка <span className="num code">{meta.error.line}</span>, столбец{" "}
              <span className="num code">{meta.error.column}</span>.
            </p>
            <button
              type="button"
              className="btn btn--secondary"
              disabled={text.state !== "ready"}
              onClick={() => viewer.current?.revealLine(meta.error!.line)}
            >
              Показать строку
            </button>
          </div>
        )}
        {xsdOpen && meta && (
          <section id="xsd-list" className="banner" aria-label="Ошибки XSD">
            {!validation && <div className="skeleton skeleton--row" aria-hidden="true" />}
            {validation && validation.xsd.length === 0 && <p className="muted">Подробности недоступны.</p>}
            {validation && validation.xsd.length > 0 && (
              <ul className="xsd-list">
                {validation.xsd.map((x, i) => (
                  <li key={i}>
                    <button
                      type="button"
                      className="xsd-list__row"
                      onClick={() => viewer.current?.revealLine(x.line)}
                    >
                      <span className="num code">
                        {x.line}:{x.column}
                      </span>
                      <span>
                        {x.message}
                        {x.path ? <span className="muted code"> {x.path}</span> : null}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </header>

      <main className="workspace">
        <SplitPane
          storageKey="xmldt.split.left"
          leftLabel="XML-файл"
          left={
            <section className="panel viewer-panel" aria-label="XML-файл" aria-busy={text.state === "loading"}>
              <div className="panel__head">
                <h2>XML-файл</h2>
                <button
                  type="button"
                  className="btn btn--secondary"
                  disabled={!highlighted}
                  onClick={() => viewer.current?.clearHighlight()}
                >
                  Снять подсветку
                </button>
              </div>
              <div className="viewer-panel__body">
                {text.state === "loading" && (
                  <div className="skeleton skeleton--fill" aria-hidden="true" />
                )}
                {text.state === "error" && (
                  <div className="inline-error" role="alert">
                    <p>{text.message}</p>
                    <button type="button" className="btn btn--secondary" onClick={loadText}>
                      Повторить
                    </button>
                  </div>
                )}
                {text.state === "ready" && text.data.length === 0 && <p className="empty-note">Файл пуст.</p>}
                {text.state === "ready" && text.data.length > 0 && (
                  <XmlViewer ref={viewer} text={text.data} label="Исходный текст XML" onHighlightChange={setHighlighted} />
                )}
              </div>
            </section>
          }
          right={
            <AnalysisPanel
              fileId={id}
              shown={shown}
              onResult={(r) => {
                setShown(r);
                setActiveHistory(null);
              }}
              onQueryDone={loadHistory}
              onReveal={reveal}
            />
          }
        />
      </main>

      <HistoryList
        history={history}
        activeId={activeHistory}
        onRetry={loadHistory}
        onOpen={(e) => {
          setShown(entryToResult(e));
          setActiveHistory(e.id);
        }}
      />
    </div>
  );
}
