import { ApiError } from "./api";

export interface RequestCodeResult {
  status: string;
  dev_code?: string;
}

export interface Me {
  email: string | null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch("/api/auth" + path, { credentials: "same-origin", ...init });
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
      }
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const authApi = {
  requestCode: (email: string) => call<RequestCodeResult>("/request", post({ email })),
  verify: (email: string, code: string) => call<Me>("/verify", post({ email, code })),
  me: () => call<Me>("/me"),
  logout: () => call<Me>("/logout", { method: "POST" }),
};
