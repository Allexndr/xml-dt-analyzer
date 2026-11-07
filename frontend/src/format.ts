const dateFmt = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const longFmt = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeStyle: "short" });

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateFmt.format(d);
}

export function formatDateLong(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : longFmt.format(d);
}

export function formatSize(bytes: number): string {
  const nf = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
  if (bytes < 1024) return `${nf.format(bytes)} Б`;
  if (bytes < 1024 * 1024) return `${nf.format(bytes / 1024)} КБ`;
  return `${nf.format(bytes / 1024 / 1024)} МБ`;
}

export function schemaLabel(s: { code: string; version: string } | null): string {
  return s ? `${s.code} v${s.version}` : "не определён";
}

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function scalar(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

export function linesLabel(a: number, b: number): string {
  return a === b ? `Строка ${a}` : `Строки ${a}–${b}`;
}
