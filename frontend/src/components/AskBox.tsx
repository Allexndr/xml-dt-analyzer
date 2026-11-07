import { onRelayStatus, relayStatus } from "../relay";
import { useEffect, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import type { AiSettings } from "../types";

const MAX = 1000;

type Props = {
  pending: boolean;
  disabled: boolean;
  settings: AiSettings | null;
  onSend: (question: string) => void;
};

const RELAY_LABEL = { off: "выключена", connecting: "подключение…", connected: "есть", error: "нет, повтор…" } as const;

export function modeLabel(s: AiSettings | null): string {
  if (!s) return "Режим: не определён";
  const model = s.model ? ` (${s.model})` : "";
  if (s.mode === "api") return `Режим: ИИ через API${model}`;
  if (s.mode === "local")
    return s.local_allowed
      ? `Режим: локальный ИИ${model}`
      : `Режим: локальный ИИ через браузер${model}, связь: ${RELAY_LABEL[relayStatus()]}`;
  return "Режим: без ИИ";
}

export default function AskBox({ pending, disabled, settings, onSend }: Props) {
  const [text, setText] = useState("");
  const [, setRelayTick] = useState(0);
  useEffect(() => onRelayStatus(() => setRelayTick((n) => n + 1)), []);
  const question = text.trim();
  const canSend = question.length > 0 && !disabled;

  const send = () => {
    if (!canSend) return;
    onSend(question);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  return (
    <form
      className="ask"
      aria-busy={pending}
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <label className="field__label" htmlFor="ask-question">
        Вопрос по файлу
      </label>
      <textarea
        id="ask-question"
        className="input ask__input"
        rows={3}
        maxLength={MAX}
        value={text}
        placeholder="Например: сколько товаров в декларации?"
        aria-describedby="ask-hint"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
      />
      <div className="ask__row">
        <p id="ask-hint" className="field__hint">
          Enter отправляет, Shift+Enter переносит строку. <span className="num">{text.length}</span> из {MAX}
        </p>
        <button type="submit" className="btn btn--primary" disabled={!canSend} aria-busy={pending}>
          {pending ? "Выполняется…" : "Отправить"}
        </button>
      </div>
      <p className="ask__mode">
        {modeLabel(settings)}{" "}
        <Link to="/settings">Настройки ИИ</Link>
      </p>
      <p className="visually-hidden" role="status" aria-live="polite">
        {pending ? "Вопрос обрабатывается." : ""}
      </p>
    </form>
  );
}
