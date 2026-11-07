import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api";
import type { AiMode, AiProvider, AiSettings, AiSettingsInput, AiTestResult } from "../types";

const KEY_LIKE = /^(sk-|sk_|pk-|xai-|gsk_|AIza|key-)|^[A-Za-z0-9_-]{40,}$/;

const MODES: { value: AiMode; label: string }[] = [
  { value: "off", label: "Без ИИ" },
  { value: "api", label: "ИИ через API" },
  { value: "local", label: "Локальный ИИ" },
];

const PROVIDERS: { value: AiProvider; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "openai_compatible", label: "OpenAI-совместимый" },
  { value: "ollama", label: "Ollama" },
];

type Draft = {
  mode: AiMode;
  provider: AiProvider;
  baseUrl: string;
  model: string;
  consent: boolean;
  apiKey: string;
  removeKey: boolean;
};

function toDraft(s: AiSettings): Draft {
  return {
    mode: s.mode,
    provider: s.provider,
    baseUrl: s.base_url ?? "",
    model: s.model ?? "",
    consent: s.consent_external,
    apiKey: "",
    removeKey: false,
  };
}

export default function Settings() {
  const navigate = useNavigate();
  const location = useLocation();
  const [loaded, setLoaded] = useState<AiSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [test, setTest] = useState<AiTestResult | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    setLoaded(null);
    api
      .getAiSettings()
      .then((s) => {
        setLoaded(s);
        setDraft(toDraft(s));
      })
      .catch((e) => setLoadError(errorMessage(e)));
  }, []);

  useEffect(load, [load]);

  const back = () => (location.key !== "default" ? navigate(-1) : navigate("/"));

  const change = (patch: Partial<Draft>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setSaved(false);
    setTest(null);
  };

  const changeMode = (mode: AiMode) => {
    if (!draft) return;
    let provider = draft.provider;
    if (mode === "local" && (provider === "openai" || provider === "anthropic")) provider = "ollama";
    if (mode === "api" && provider === "ollama") provider = "openai";
    change({ mode, provider });
  };

  const hasKey = !!loaded?.has_api_key && !draft?.removeKey;
  const needsUrl = draft?.provider === "openai_compatible" || draft?.provider === "ollama";
  const consentMissing = draft?.mode === "api" && !draft.consent;

  const persist = async (): Promise<boolean> => {
    if (!draft) return false;
    if (KEY_LIKE.test(draft.model.trim())) {
      setSaveError("В поле «Модель» вставлен ключ API. Перенесите его в поле «Ключ API», а в «Модель» укажите название, например gpt-5.4-mini.");
      return false;
    }
    const input: AiSettingsInput = {
      mode: draft.mode,
      provider: draft.provider,
      base_url: needsUrl && draft.baseUrl.trim() ? draft.baseUrl.trim() : null,
      model: draft.model.trim() || null,
      consent_external: draft.consent,
    };
    if (draft.apiKey) input.api_key = draft.apiKey;
    else if (draft.removeKey) input.api_key = null;
    setSaving(true);
    setSaveError(null);
    try {
      const s = await api.putAiSettings(input);
      window.dispatchEvent(new CustomEvent("xmldt:ai-settings", { detail: s }));
      setLoaded(s);
      setDraft(toDraft(s));
      setSaved(true);
      return true;
    } catch (e) {
      setSaveError(errorMessage(e));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTest(null);
    setSaveError(null);
    setTesting(true);
    try {
      if (!(await persist())) return;
      setSaved(false);
      setTest(await api.testAiSettings());
    } catch (e) {
      setTest({ ok: false, message: errorMessage(e) });
    } finally {
      setTesting(false);
    }
  };

  const busy = saving || testing;
  const suggestions = test?.models ?? [];

  return (
    <main className="page page--narrow">
      <header className="page__head">
        <p>
          <button type="button" className="btn btn--secondary" onClick={back}>
            Назад
          </button>
        </p>
        <h1>Настройки ИИ</h1>
        <p className="lead">
          Настройки действуют на время текущей сессии. Файл и история запросов при смене режима сохраняются.
        </p>
      </header>

      {loadError && (
        <div className="inline-error" role="alert">
          <p>{loadError}</p>
          <button type="button" className="btn btn--secondary" onClick={load}>
            Повторить
          </button>
        </div>
      )}
      {!draft && !loadError && (
        <div aria-busy="true" aria-label="Загрузка настроек">
          <div className="skeleton skeleton--row" />
          <div className="skeleton skeleton--row" />
          <div className="skeleton skeleton--block" />
        </div>
      )}

      {draft && loaded && (
        <form
          className="settings"
          aria-busy={busy}
          onSubmit={(e) => {
            e.preventDefault();
            if (!consentMissing && !busy) void persist();
          }}
        >
          <fieldset>
            <legend>Режим</legend>
            <div className="choices">
              {MODES.map((m) => (
                <label key={m.value} className="choice">
                  <input
                    type="radio"
                    name="ai-mode"
                    value={m.value}
                    checked={draft.mode === m.value}
                    onChange={() => changeMode(m.value)}
                  />
                  {m.label}
                </label>
              ))}
            </div>
            {draft.mode === "off" && (
              <p className="field__hint">Вопросы разбираются встроенными правилами, данные никуда не передаются.</p>
            )}
          </fieldset>

          {draft.mode === "local" && loaded.relay_available === false && !loaded.local_allowed && (
            <p className="note" role="note">
              На этом хостинге нет постоянных соединений (WebSocket), поэтому локальный ИИ через браузер недоступен.
              Используйте ИИ через API или установите сервис у себя (Docker): тогда модель подключается напрямую.
            </p>
          )}
          {draft.mode === "local" && !loaded.local_allowed && loaded.relay_available !== false && (
            <div className="note" role="note">
              <p>
                Модель работает на вашем компьютере, а запросы к ней передаёт этот браузер: пока открыта страница
                сервиса, сервер отправляет вопрос в браузер, браузер — модели на localhost. Декларация сторонним
                ИИ-провайдерам не передаётся.
              </p>
              <p>
                Для Ollama разрешите запросы с этого сайта: запустите её с переменной{" "}
                <code className="code">OLLAMA_ORIGINS={window.location.origin}</code>. Адрес API —{" "}
                <code className="code">http://localhost:11434/v1</code>.
              </p>
            </div>
          )}

          {draft.mode !== "off" && (
            <>
              <div className="settings__field">
                <label className="field__label" htmlFor="ai-provider">
                  Провайдер
                </label>
                <select
                  id="ai-provider"
                  className="input"
                  value={draft.provider}
                  onChange={(e) => change({ provider: e.target.value as AiProvider })}
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>

              {needsUrl && (
                <div className="settings__field">
                  <label className="field__label" htmlFor="ai-base-url">
                    Адрес сервера
                  </label>
                  <input
                    id="ai-base-url"
                    className="input"
                    type="url"
                    inputMode="url"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={draft.provider === "ollama" ? "http://localhost:11434" : "https://example.com/v1"}
                    value={draft.baseUrl}
                    onChange={(e) => change({ baseUrl: e.target.value })}
                  />
                </div>
              )}

              <div className="settings__field">
                <label className="field__label" htmlFor="ai-model">
                  Модель
                </label>
                <input
                  id="ai-model"
                  className="input"
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  list="ai-model-list"
                  value={draft.model}
                  onChange={(e) => change({ model: e.target.value })}
                  aria-invalid={KEY_LIKE.test(draft.model.trim()) || undefined}
                  aria-describedby="ai-model-hint"
                />
                <datalist id="ai-model-list">
                  {suggestions.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
                <p className="field__hint" id="ai-model-hint">
                  {KEY_LIKE.test(draft.model.trim())
                    ? "Это похоже на ключ API, а не на название модели. Ключ вводится ниже, в поле «Ключ API»."
                    : suggestions.length > 0
                    ? "Варианты из проверки подключения доступны в списке."
                    : "Список моделей появится после проверки подключения."}
                </p>
              </div>

              {draft.provider !== "ollama" && (
                <div className="settings__field">
                  <label className="field__label" htmlFor="ai-key">
                    Ключ API
                  </label>
                  {hasKey && (
                    <div className="settings__key">
                      <span className="status status--ok">Ключ сохранён</span>
                      <button type="button" className="btn btn--danger" onClick={() => change({ removeKey: true, apiKey: "" })}>
                        Удалить ключ
                      </button>
                    </div>
                  )}
                  {!hasKey && loaded.has_server_key && draft.provider === "openai" && (
                    <p className="field__hint">
                      <span className="status status--ok">Ключ OpenAI подключён на сервере.</span> Своё значение
                      вводить не обязательно; введённый ключ заменит серверный только для вашей сессии.
                    </p>
                  )}
                  {loaded.has_api_key && draft.removeKey && (
                    <p className="field__hint">Ключ будет удалён после сохранения.</p>
                  )}
                  <input
                    id="ai-key"
                    className="input"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={hasKey ? "Введите новый ключ, чтобы заменить" : ""}
                    value={draft.apiKey}
                    onChange={(e) => change({ apiKey: e.target.value, removeKey: false })}
                  />
                  <p className="field__hint">Ключ хранится только в памяти сервера на время сессии и не показывается повторно.</p>
                </div>
              )}
            </>
          )}

          {draft.mode === "api" && (
            <div className="settings__field">
              <label className="consent">
                <input type="checkbox" checked={draft.consent} onChange={(e) => change({ consent: e.target.checked })} />
                <span>Я согласен, что вопрос и найденные фрагменты XML будут переданы внешнему ИИ-провайдеру</span>
              </label>
              <p className="field__hint">Файл целиком провайдеру не отправляется.</p>
            </div>
          )}

          <div className="settings__actions">
            <button
              type="button"
              className="btn btn--secondary"
              disabled={busy || consentMissing || draft.mode === "off"}
              aria-busy={testing}
              onClick={() => void runTest()}
            >
              {testing ? "Проверка…" : "Проверить подключение"}
            </button>
            <button type="submit" className="btn btn--primary" disabled={busy || consentMissing} aria-busy={saving && !testing}>
              {saving && !testing ? "Сохранение…" : "Сохранить"}
            </button>
          </div>
          {draft.mode !== "off" && (
            <p className="field__hint">Проверка сначала сохраняет текущие настройки.</p>
          )}

          <div role="status" aria-live="polite">
            {saved && !test && <p className="status status--ok">Настройки сохранены.</p>}
            {test && (
              <p className={`status ${test.ok ? "status--ok" : "status--error"}`}>
                {test.message || (test.ok ? "Подключение работает." : "Не удалось подключиться.")}
              </p>
            )}
          </div>
          {saveError && (
            <div className="inline-error" role="alert">
              <p>{saveError}</p>
            </div>
          )}
          <p>
            <Link to="/">На главную</Link>
          </p>
        </form>
      )}
    </main>
  );
}
