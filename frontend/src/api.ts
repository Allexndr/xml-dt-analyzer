import type {
  AiSettings,
  AiSettingsInput,
  AiTestResult,
  AskAnswer,
  CatalogEntry,
  CheckReport,
  FileMeta,
  HistoryEntry,
  Jurisdiction,
  ToolResult,
  Validation,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function failure(res: Response): Promise<ApiError> {
  let detail = `Ошибка ${res.status}`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      const d = (body as { detail: unknown }).detail;
      if (typeof d === "string") detail = d;
      else if (d != null) detail = JSON.stringify(d);
    }
  } catch {
    /* тело не JSON */
  }
  return new ApiError(res.status, detail);
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiError(0, "Не удалось связаться с сервером.");
  }
  if (!res.ok) throw await failure(res);
  return res;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await request(path, init);
  return (await res.json()) as T;
}

export const api = {
  listFiles: () => json<FileMeta[]>("/files"),
  getFile: (id: string) => json<FileMeta>(`/files/${encodeURIComponent(id)}`),
  uploadFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return json<FileMeta>("/files", { method: "POST", body: form });
  },
  deleteFile: async (id: string) => {
    await request(`/files/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  getText: async (id: string) => (await request(`/files/${encodeURIComponent(id)}/text`)).text(),
  getValidation: (id: string) => json<Validation>(`/files/${encodeURIComponent(id)}/validation`),
  getGoods: (id: string) => json<ToolResult>(`/files/${encodeURIComponent(id)}/facts/goods`),
  getMarking: (id: string) => json<ToolResult>(`/files/${encodeURIComponent(id)}/facts/marking`),
  getCatalog: () => json<CatalogEntry[]>("/catalog"),
  getConcept: (id: string, key: string) =>
    json<ToolResult>(`/files/${encodeURIComponent(id)}/concepts/${encodeURIComponent(key)}`),
  getChecks: (id: string, jurisdiction: Jurisdiction) =>
    json<CheckReport>(
      `/files/${encodeURIComponent(id)}/checks${jurisdiction ? `?jurisdiction=${jurisdiction}` : ""}`,
    ),
  ask: (id: string, question: string) =>
    json<AskAnswer>(`/files/${encodeURIComponent(id)}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  getAiSettings: () => json<AiSettings>("/settings/ai"),
  putAiSettings: (input: AiSettingsInput) =>
    json<AiSettings>("/settings/ai", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  testAiSettings: () => json<AiTestResult>("/settings/ai/test", { method: "POST" }),
  getHistory: (id: string) => json<HistoryEntry[]>(`/files/${encodeURIComponent(id)}/history`),
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Неизвестная ошибка.";
}
