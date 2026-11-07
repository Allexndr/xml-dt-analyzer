export type Evidence = {
  node_id: number;
  line_start: number;
  line_end: number;
  start: number;
  end: number;
  tag: string;
  label: string;
};

export type ToolResult = {
  tool: string;
  value: Record<string, unknown>;
  evidence: Evidence[];
  warnings: string[];
};

export type FileError = {
  message: string;
  line: number;
  column: number;
  offset_utf16: number;
  kind: string;
};

export type FileMeta = {
  id: string;
  name: string;
  size: number;
  sha256: string;
  uploaded_at: string;
  encoding: string;
  status: "ready" | "error";
  error: null | FileError;
  schema: null | { code: string; version: string; namespace: string; supported: boolean; note?: string };
  xsd_valid: boolean | null;
  xsd_error_count: number;
  goods_items: number | null;
  expires_at: string;
};

export type XsdIssue = { line: number; column: number; message: string; path: string | null };

export type Validation = {
  wellformed_error: FileError | null;
  xsd: XsdIssue[];
};

export type CatalogEntry = {
  key: string;
  title: string;
  graphs: string[];
  level: string;
  synonyms: string[];
};

export type Status = "violation" | "needs_info" | "pass" | "not_applicable" | "not_implemented";

export type Finding = { message: string; value: string | null; evidence: Evidence[] };

export type CheckResultItem = {
  rule_id: string;
  kind: string;
  status: Status;
  severity: "error" | "warning";
  path: string;
  legal_ref: Record<string, unknown>;
  text: string;
  findings: Finding[];
};

export type CheckReport = {
  jurisdiction: string | null;
  check_date: string;
  summary: {
    rules: number;
    automated: number;
    by_status: Partial<Record<Status, number>>;
    violations_by_severity: Record<string, number>;
  };
  results: CheckResultItem[];
};

export type HistoryKind = "goods" | "marking" | "concept" | "checks" | "ask";

export type HistoryEntry = {
  id: number | string;
  at: string;
  kind: HistoryKind;
  title: string;
  request: Record<string, unknown>;
  result: Record<string, unknown>;
};

/** Результат, показанный на правой панели. */
export type PanelResult =
  | { kind: "goods" | "marking" | "concept"; title: string; result: ToolResult }
  | { kind: "checks"; title: string; result: CheckReport }
  | { kind: "ask"; title: string; result: AskAnswer };

export type Jurisdiction = "" | "KZ" | "RU" | "BY" | "AM" | "KG";

export type AskFactKind = "xml_fact" | "format_requirement" | "filling_rule" | "interpretation";

export type NormSource = { ref: string; id: string; as_of: string; text: string };

export type AskFact = { text: string; kind: AskFactKind; evidence: Evidence[]; sources?: NormSource[] };

export type AskAnswer = {
  question: string;
  mode: "llm" | "rules";
  model: string | null;
  summary: string;
  facts: AskFact[];
  warnings: string[];
  verified: boolean;
  unsupported: boolean;
  tool_calls: { tool: string; args: Record<string, unknown> }[];
};

export type AiMode = "off" | "api" | "local";
export type AiProvider = "openai" | "anthropic" | "openai_compatible" | "ollama";

export type AiSettings = {
  mode: AiMode;
  provider: AiProvider;
  base_url: string | null;
  model: string | null;
  has_api_key: boolean;
  has_server_key: boolean;
  relay_connected?: boolean;
  relay_available?: boolean;
  consent_external: boolean;
  local_allowed: boolean;
};

export type AiSettingsInput = Partial<Omit<AiSettings, "has_api_key" | "has_server_key" | "local_allowed">> & {
  api_key?: string | null;
};

export type AiTestResult = { ok: boolean; message: string; models?: string[] };
