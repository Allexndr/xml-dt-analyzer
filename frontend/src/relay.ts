/**
 * Browser side of the local-model relay (backend: xmldt/ai/relay.py).
 *
 * In the cloud deployment the server cannot reach the user's localhost. While a page of the app is
 * open and AI mode is "local", the browser keeps a WebSocket to /api/relay and forwards the server's
 * OpenAI-compatible requests to the model on this computer (Ollama, LM Studio, llama.cpp).
 * Only loopback targets are allowed: the server can never make the browser call anything else.
 */

export type RelayStatus = "off" | "connecting" | "connected" | "error";

type FetchRequest = { id: number; op: "fetch"; method: "GET" | "POST"; url: string; body?: unknown };

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
const listeners = new Set<(s: RelayStatus) => void>();
let status: RelayStatus = "off";
let socket: WebSocket | null = null;
let wanted = false;
let retry = 0;
let pingTimer: number | undefined;

function setStatus(s: RelayStatus) {
  status = s;
  listeners.forEach((fn) => fn(s));
}

export function relayStatus(): RelayStatus {
  return status;
}

export function onRelayStatus(fn: (s: RelayStatus) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isLoopbackUrl(raw: string): boolean {
  try {
    const u = new URL(raw);
    return (u.protocol === "http:" || u.protocol === "https:") && LOOPBACK.has(u.hostname);
  } catch {
    return false;
  }
}

async function handle(req: FetchRequest): Promise<Record<string, unknown>> {
  if (req.op !== "fetch" || !isLoopbackUrl(req.url)) {
    return { id: req.id, error: "Браузер разрешает обращаться только к модели на этом компьютере (localhost)" };
  }
  try {
    const resp = await fetch(req.url, {
      method: req.method,
      redirect: "error", // a redirect could leave the loopback host that was checked above
      credentials: "omit",
      headers: req.body ? { "Content-Type": "application/json" } : undefined,
      body: req.body ? JSON.stringify(req.body) : undefined,
    });
    const text = await resp.text();
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
    return { id: req.id, status: resp.status, json: parsed, text: parsed ? undefined : text.slice(0, 500) };
  } catch (e) {
    return {
      id: req.id,
      error:
        "Нет доступа к локальной модели. Запущена ли она и разрешены ли запросы с этого сайта " +
        "(для Ollama — переменная OLLAMA_ORIGINS)? " +
        (e instanceof Error ? e.message : ""),
    };
  }
}

function connect() {
  if (!wanted || socket) return;
  setStatus("connecting");
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/api/relay`);
  socket = ws;
  ws.onopen = () => {
    retry = 0;
    setStatus("connected");
    pingTimer = window.setInterval(() => ws.readyState === ws.OPEN && ws.send(JSON.stringify({ type: "ping" })), 25000);
  };
  ws.onmessage = async (ev) => {
    let req: FetchRequest;
    try {
      req = JSON.parse(ev.data as string);
    } catch {
      return;
    }
    const reply = await handle(req);
    if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(reply));
  };
  ws.onclose = () => {
    window.clearInterval(pingTimer);
    socket = null;
    if (!wanted) {
      setStatus("off");
      return;
    }
    setStatus("error");
    retry = Math.min(retry + 1, 6);
    window.setTimeout(connect, 1000 * 2 ** retry);
  };
}

/** Start or stop the relay depending on the current AI settings. */
export function syncRelay(enabled: boolean) {
  wanted = enabled;
  if (enabled) connect();
  else if (socket) socket.close();
  else setStatus("off");
}

/** Reconnect with the current cookies (after sign-in or sign-out the owner changes). */
export function restartRelay() {
  if (!wanted) return;
  if (socket) socket.close();  // onclose reconnects because `wanted` stays true
  else connect();
}
