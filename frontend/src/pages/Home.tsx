import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import AccountBadge from "../components/AccountBadge";
import { Link, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api";
import type { FileMeta } from "../types";
import { formatDate, formatDateLong, formatSize, schemaLabel } from "../format";

type List = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; items: FileMeta[] };

function statusLabel(f: FileMeta): string {
  if (f.status === "error") return "Ошибка разбора";
  if (f.xsd_valid === true) return "Соответствует XSD";
  if (f.xsd_valid === false) return `Ошибок XSD: ${f.xsd_error_count}`;
  return "Готов";
}

export default function Home() {
  const nav = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [list, setList] = useState<List>({ state: "loading" });
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(() => {
    setList({ state: "loading" });
    api
      .listFiles()
      .then((items) => setList({ state: "ready", items }))
      .catch((e) => setList({ state: "error", message: errorMessage(e) }));
  }, []);

  useEffect(load, [load]);

  const upload = async (file: File | undefined) => {
    if (!file || uploading) return;
    setUploadError(null);
    if (!/\.xml$/i.test(file.name)) {
      setUploadError("Нужен файл с расширением .xml.");
      return;
    }
    setUploading(true);
    try {
      const meta = await api.uploadFile(file);
      nav(`/files/${meta.id}`);
    } catch (e) {
      setUploadError(errorMessage(e));
      setUploading(false);
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    void upload(e.dataTransfer.files[0]);
  };

  const remove = async (id: string) => {
    setDeletingId(id);
    setDeleteError(null);
    try {
      await api.deleteFile(id);
      setConfirmId(null);
      setList((l) => (l.state === "ready" ? { state: "ready", items: l.items.filter((f) => f.id !== id) } : l));
    } catch (e) {
      setDeleteError(errorMessage(e));
    } finally {
      setDeletingId(null);
    }
  };

  const expiry =
    list.state === "ready" && list.items.length > 0
      ? list.items.map((f) => f.expires_at).sort()[0]
      : null;

  return (
    <main className="page">
      <header className="page__head">
        <h1>Анализ XML-деклараций</h1>
        <p className="lead">
          Загрузите декларацию в формате XML: сервис покажет исходный текст, найдёт нужные сведения и проверит их по правилам.
        </p>
        <div className="account-row">
          <Link to="/settings" className="btn btn--secondary">
            Настройки ИИ
          </Link>
          <Link to="/review" className="btn btn--secondary">
            Экспертная проверка
          </Link>
          <AccountBadge />
        </div>
      </header>

      <section aria-label="Загрузка файла">
        <div
          className={`dropzone${dragOver ? " is-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          aria-busy={uploading}
        >
          <p className="dropzone__text">{uploading ? "Файл загружается…" : "Перетащите XML-файл сюда или выберите на компьютере."}</p>
          <input
            ref={input}
            className="visually-hidden"
            type="file"
            accept=".xml,application/xml,text/xml"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => {
              void upload(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <button type="button" className="btn btn--primary" disabled={uploading} onClick={() => input.current?.click()}>
            {uploading ? "Загрузка…" : "Загрузить файл"}
          </button>
        </div>
        <p className="visually-hidden" role="status" aria-live="polite">
          {uploading ? "Файл загружается." : ""}
        </p>
        {uploadError && (
          <p className="form-error" role="alert">
            {uploadError}
          </p>
        )}
      </section>

      <section className="history-files" aria-label="Загруженные файлы" aria-busy={list.state === "loading"}>
        <h2>Загруженные файлы</h2>
        {expiry && <p className="muted">Без входа файлы хранятся до {formatDateLong(expiry)}.</p>}

        {list.state === "loading" && (
          <div aria-hidden="true">
            <div className="skeleton skeleton--row" />
            <div className="skeleton skeleton--row" />
            <div className="skeleton skeleton--row" />
          </div>
        )}
        {list.state === "error" && (
          <div className="inline-error" role="alert">
            <p>{list.message}</p>
            <button type="button" className="btn btn--secondary" onClick={load}>
              Повторить
            </button>
          </div>
        )}
        {list.state === "ready" && list.items.length === 0 && (
          <p className="empty-note">Файлов пока нет. Загрузите XML-декларацию.</p>
        )}
        {deleteError && (
          <p className="form-error" role="alert">
            {deleteError}
          </p>
        )}
        {list.state === "ready" && list.items.length > 0 && (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Таблица загруженных файлов">
            <table className="table spec">
              <thead>
                <tr>
                  <th scope="col">Имя файла</th>
                  <th scope="col">Загружен</th>
                  <th scope="col" className="num">Размер</th>
                  <th scope="col">Тип</th>
                  <th scope="col">Статус</th>
                  <th scope="col">
                    <span className="visually-hidden">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.items.map((f) => (
                  <tr key={f.id}>
                    <th scope="row" className="table__name">
                      <Link to={`/files/${f.id}`}>{f.name}</Link>
                    </th>
                    <td className="num code">{formatDate(f.uploaded_at)}</td>
                    <td className="num code">{formatSize(f.size)}</td>
                    <td className="code">{schemaLabel(f.schema)}</td>
                    <td className={f.status === "error" || f.xsd_valid === false ? "text-error" : undefined}>{statusLabel(f)}</td>
                    <td className="table__actions">
                      {confirmId === f.id ? (
                        <span className="confirm" role="group" aria-label={`Удалить файл ${f.name}?`}>
                          <span className="confirm__q">Удалить файл?</span>
                          <button
                            type="button"
                            className="btn btn--danger"
                            disabled={deletingId === f.id}
                            onClick={() => void remove(f.id)}
                          >
                            {deletingId === f.id ? "Удаление…" : "Да, удалить"}
                          </button>
                          <button type="button" className="btn btn--secondary" onClick={() => setConfirmId(null)}>
                            Отмена
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn--secondary"
                          aria-label={`Удалить файл ${f.name}`}
                          onClick={() => {
                            setDeleteError(null);
                            setConfirmId(f.id);
                          }}
                        >
                          Удалить
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
