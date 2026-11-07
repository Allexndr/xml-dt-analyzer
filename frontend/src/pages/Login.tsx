import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, errorMessage } from "../api";
import { authApi } from "../authApi";

type Step = "email" | "code";

export default function Login() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [devCode, setDevCode] = useState<string | null>(null);

  async function sendCode(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const r = await authApi.requestCode(email.trim());
      setDevCode(r.dev_code ?? null);
      setCode("");
      setStep("code");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Слишком много запросов. Подождите несколько минут и попробуйте снова."
          : errorMessage(err),
      );
    } finally {
      setPending(false);
    }
  }

  async function checkCode(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await authApi.verify(email.trim(), code.trim());
      window.dispatchEvent(new Event("xmldt:auth-changed"));
      // полная перезагрузка: список файлов и бейдж должны подхватить аккаунт
      window.location.assign("/");
      navigate("/");
    } catch (err) {
      setError(errorMessage(err));
      setPending(false);
    }
  }

  return (
    <main className="page page--narrow">
      <header className="page__head">
        <h1>Вход по почте</h1>
        <p className="lead">Вход необязателен. После входа загруженные файлы и история сохранятся в аккаунте.</p>
      </header>

      {step === "email" ? (
        <form className="auth" onSubmit={sendCode} noValidate>
          <div className="field">
            <label className="field__label" htmlFor="auth-email">
              Адрес электронной почты
            </label>
            <input
              id="auth-email"
              className="input"
              type="email"
              autoComplete="email"
              inputMode="email"
              required
              maxLength={254}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-describedby={error ? "auth-error" : undefined}
            />
            <p className="field__hint">Мы отправим на этот адрес одноразовый код.</p>
          </div>
          {error && (
            <p id="auth-error" className="status status--error" role="alert">
              {error}
            </p>
          )}
          <div className="auth__actions">
            <button type="submit" className="btn btn--primary" disabled={pending || !email.trim()}>
              {pending ? "Отправка…" : "Получить код"}
            </button>
            <Link to="/" className="btn btn--ghost">
              Продолжить без входа
            </Link>
          </div>
        </form>
      ) : (
        <form className="auth" onSubmit={checkCode} noValidate>
          <p>
            Код отправлен на адрес <strong className="auth__addr">{email.trim()}</strong>. Он действует 10 минут.
          </p>
          {devCode && <p className="note">Режим разработки: код {devCode}</p>}
          <div className="field">
            <label className="field__label" htmlFor="auth-code">
              Код из письма
            </label>
            <input
              id="auth-code"
              className="input auth__code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              aria-describedby={error ? "auth-error" : undefined}
            />
          </div>
          {error && (
            <p id="auth-error" className="status status--error" role="alert">
              {error}
            </p>
          )}
          <div className="auth__actions">
            <button type="submit" className="btn btn--primary" disabled={pending || code.length !== 6}>
              {pending ? "Проверка…" : "Войти"}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={pending}
              onClick={() => {
                setStep("email");
                setError(null);
              }}
            >
              Изменить адрес
            </button>
          </div>
        </form>
      )}
    </main>
  );
}
