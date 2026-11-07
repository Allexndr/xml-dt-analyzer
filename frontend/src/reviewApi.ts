import { ApiError } from "./api";

export type ReviewType = "rule" | "manual" | "concept" | "binding" | "edition";
export type ReviewStatus = "pending" | "approved" | "rejected" | "needs_changes";

export interface ReviewItem {
  key: string;
  type: ReviewType;
  id: string;
  title: string;
  proposal: Record<string, unknown> | null;
  source: string | null;
  text: string;
  status: ReviewStatus;
  stale: boolean;
  comment: string | null;
  reviewer: string | null;
  at: number | null;
}

export type ReviewCounts = Partial<Record<ReviewType, Partial<Record<ReviewStatus, number>>>>;

export interface ReviewList {
  items: ReviewItem[];
  total: number;
  counts: ReviewCounts;
  can_review: boolean;
}

export interface ReviewFilters {
  type: ReviewType | "";
  status: ReviewStatus | "";
  q: string;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch("/api/review" + path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiError(0, "Не удалось связаться с сервером.");
  }
  if (!res.ok) {
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
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const reviewApi = {
  list: (f: ReviewFilters, signal?: AbortSignal) => {
    const p = new URLSearchParams();
    if (f.type) p.set("type", f.type);
    if (f.status) p.set("status", f.status);
    if (f.q) p.set("q", f.q);
    const qs = p.toString();
    return call<ReviewList>("/items" + (qs ? `?${qs}` : ""), { signal });
  },
  decide: (key: string, status: Exclude<ReviewStatus, "pending">, comment?: string) =>
    call<unknown>("/items/" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(comment ? { status, comment } : { status }),
    }),
  exportDecisions: () => call<{ decisions: unknown[] }>("/export"),
};
