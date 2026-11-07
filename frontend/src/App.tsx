import { useEffect } from "react";
import { api } from "./api";
import { restartRelay, syncRelay } from "./relay";
import type { AiSettings } from "./types";
import { Route, Routes, Link } from "react-router-dom";
import Home from "./pages/Home";
import Analysis from "./pages/Analysis";
import Settings from "./pages/Settings";
import Login from "./pages/Login";
import Review from "./pages/Review";

function NotFound() {
  return (
    <main className="page page--narrow">
      <h1>Страница не найдена</h1>
      <p>
        <Link to="/">На главную</Link>
      </p>
    </main>
  );
}

function RelayBridge() {
  // keep the local-model relay in sync with the AI settings (cloud deployment, mode "local")
  useEffect(() => {
    const apply = (st: AiSettings) => syncRelay(st.mode === "local" && !st.local_allowed);
    api.getAiSettings().then(apply).catch(() => syncRelay(false));
    const onChange = (e: Event) => apply((e as CustomEvent<AiSettings>).detail);
    // after sign-in/out the owner changes: re-read settings and reconnect the relay with the new cookie
    const onAuth = () => api.getAiSettings().then((st) => { apply(st); restartRelay(); }).catch(() => syncRelay(false));
    window.addEventListener("xmldt:ai-settings", onChange);
    window.addEventListener("xmldt:auth-changed", onAuth);
    return () => {
      window.removeEventListener("xmldt:ai-settings", onChange);
      window.removeEventListener("xmldt:auth-changed", onAuth);
    };
  }, []);
  return null;
}

export default function App() {
  return (
    <>
    <RelayBridge />
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/files/:id" element={<Analysis />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/review" element={<Review />} />
      <Route path="/login" element={<Login />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
    </>
  );
}
