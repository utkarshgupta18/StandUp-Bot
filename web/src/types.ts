export type Role = "admin" | "viewer";

export interface User {
  id: number | null;
  username: string;
  role: Role;
  created_at?: string;
  project_ids?: number[];
}

export interface Project {
  id: number;
  name: string;
  docs_path: string | null;
  repo_path: string | null;
  created_at: string;
}

export interface StatusItem {
  text: string;
  sources: string[];
}

export type Health = "on_track" | "at_risk" | "blocked" | "unknown";

export interface Status {
  summary: string;
  health: Health;
  done: StatusItem[];
  in_progress: StatusItem[];
  next_steps: StatusItem[];
  blockers: StatusItem[];
}

export interface Update {
  id: number;
  project_id: number;
  summary: string;
  generated_at: string;
  source_refs: string[];
  status: Status | null;
}

export interface Run {
  id: number;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "ok" | "error";
  docs_ingested: number;
  docs_removed: number;
  commits_ingested: number;
  items_ingested: number;
  errors: string[];
}

export interface Source {
  id: number;
  project_id: number;
  kind: string;
  name: string;
  config: Record<string, unknown>;
  created_at: string;
  last_synced_at: string | null;
  last_error: string | null;
}

export interface DashboardRow extends Project {
  counts: Record<string, number>;
  sources: Source[];
  last_commit_date: string | null;
  last_run: Run | null;
  latest_update: Update | null;
}

export interface ConnectorField {
  name: string;
  label: string;
  type: "string" | "int" | "list";
  required: boolean;
  default: unknown;
  help: string;
}

export interface Connector {
  kind: string;
  label: string;
  configured: boolean;
  env_vars: string[];
  fields: ConnectorField[];
}

export interface AnswerSource {
  project_id: number;
  project_name: string;
  source_type: string;
  source_ref: string;
  title?: string;
  url?: string;
}

export interface Answer {
  answer: string;
  sources: AnswerSource[];
}

export interface HealthInfo {
  status: string;
  llm_configured: boolean;
  auth_enabled: boolean;
  storage: { database: string; vectors: string };
  scheduler: { enabled: boolean; interval_minutes: number; last_run_at: string | null; runs: number };
}

export interface RefreshReport {
  project_id: number;
  project_name: string;
  docs: { ingested: string[]; removed: string[] } | null;
  git: { ingested: string[] } | null;
  sources?: { name: string; kind: string; ingested: string[] }[];
  errors: string[];
  status_regenerated?: boolean;
}
