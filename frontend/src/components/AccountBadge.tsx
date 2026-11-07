import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { authApi } from "../authApi";

export default function AccountBadge() {
  const [email, setEmail] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    authApi
      .me()
      .then((m) => alive && setEmail(m.email))
      .catch(() => alive && setEmail(null));
    return () => {
      alive = false;
    };
  }, []);

  async function logout() {
    setBusy(true);
    try {
      await authApi.logout();
      window.dispatchEvent(new Event("xmldt:auth-changed"));
    } catch {
      /* сессия всё равно будет проверена заново */
    }
    // файлы принадлежат аккаунту: после выхода список нужно перечитать
    window.location.assign("/");
  }

  if (email === undefined) return <div className="account" aria-hidden="true" />;
  if (email === null) {
    return (
      <div className="account">
        <Link to="/login" className="btn btn--secondary">
          Войти
        </Link>
      </div>
    );
  }
  return (
    <div className="account">
      <span className="account__email" title={email}>
        {email}
      </span>
      <button type="button" className="btn btn--ghost" onClick={logout} disabled={busy}>
        Выйти
      </button>
    </div>
  );
}
