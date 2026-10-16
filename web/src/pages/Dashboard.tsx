import { useCallback, useEffect, useState } from "react";
import { get, post } from "../api";
import type { DashboardRow, Health, Project, RefreshReport, Status } from "../types";

const HEALTH: Record<Health, string> = {
  on_track: "On track",
  at_risk: "At risk",
  blocked: "Blocked",
  unknown: "Unknown",
};

const SECTIONS: { key: keyof Pick<Status, "done" | "in_progress" | "next_steps" | "blockers">; label: string }[] = [
  { key: "done", label: "Done" },
  { key: "in_progress", label: "In progress" },
  { key: "next_steps", label: "Next steps" },
  { key: "blockers", label: "Blockers" },
];

function shortRef(ref: string): string {
  return /^[0-9a-f]{40}$/.test(ref) ? ref.slice(0, 10) : ref;
}

export function refreshSummary(r: RefreshReport): string {
  const parts = [
    `${r.docs?.ingested.length ?? 0} docs updated`,
    `${r.docs?.removed.length ?? 0} removed`,
    `${r.git?.ingested.length ?? 0} new commits`,
  ];
  const items = (r.sources ?? []).reduce((n, s) => n + s.ingested.length, 0);
  if (r.sources?.length) parts.push(`${items} source items`);
  if (r.status_regenerated) parts.push("status regenerated");
  return `${r.project_name}: ${parts.join(", ")}`;
}

function ProjectCard({ row, isAdmin, onChanged }: { row: DashboardRow; isAdmin: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const update = row.latest_update;
  const status = update?.status ?? null;
  const counts = Object.entries(row.counts).filter(([k, v]) => v > 0 || k === "doc" || k === "commit");

  async function run(label: string, fn: () => Promise<string>) {
    setBusy(label);
    setMessage(null);
    try {
      setMessage({ kind: "ok", text: await fn() });
      onChanged();
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <article className="card project-card">
      <div className="card-head">
        <h3>{row.name}</h3>
        {status && <span className={`health ${status.health}`}>{HEALTH[status.health]}</span>}
      </div>

      <dl className="stats">
        {counts.map(([kind, n]) => (
          <div key={kind}><dt>{kind === "doc" ? "Docs" : kind === "commit" ? "Commits" : kind}</dt><dd>{n}</dd></div>
        ))}
        <div><dt>Last commit</dt><dd>{row.last_commit_date?.slice(0, 10) ?? "—"}</dd></div>
        <div><dt>Last refresh</dt><dd>{(row.last_run?.finished_at ?? row.last_run?.started_at)?.slice(0, 16) ?? "never"}</dd></div>
      </dl>

      {row.last_run && row.last_run.errors.length > 0 && (
        <details className="run-errors">
          <summary>Last refresh had {row.last_run.errors.length} error(s)</summary>
          <ul>{row.last_run.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
        </details>
      )}

      {status ? (
        <>
          <p className="summary">{update!.summary}</p>
          <p className="muted small">Status generated {update!.generated_at} UTC</p>
          <div className="sections">
            {SECTIONS.map(({ key, label }) => (
              <div key={key} className={`section ${key}`}>
                <h4>{label}</h4>
                {status[key].length === 0 ? (
                  <p className="muted small">None</p>
                ) : (
                  <ul>
                    {status[key].map((item, i) => (
                      <li key={i}>
                        {item.text}
                        {item.sources.length > 0 && <span className="refs"> {item.sources.map(shortRef).join(", ")}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="muted">No status generated yet.</p>
      )}

      {isAdmin && (
        <div className="actions">
          <button disabled={!!busy} onClick={() => run("refresh", async () =>
            refreshSummary(await post<RefreshReport>(`/projects/${row.id}/reingest`)))}>
            {busy === "refresh" ? "Refreshing…" : "Refresh"}
          </button>
          <button disabled={!!busy} onClick={() => run("status", async () => {
            await post(`/projects/${row.id}/status`);
            return "Status updated.";
          })}>
            {busy === "status" ? "Asking Claude…" : "Regenerate status"}
          </button>
        </div>
      )}
      {message && <p className={message.kind === "ok" ? "ok small" : "error small"}>{message.text}</p>}
    </article>
  );
}

export default function Dashboard({ project, isAdmin }: { project: Project | null; isAdmin: boolean }) {
  const [rows, setRows] = useState<DashboardRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(() => {
    get<DashboardRow[]>("/dashboard").then(setRows).catch((e) => setError((e as Error).message));
  }, []);

  useEffect(load, [load]);

  async function refreshAll() {
    setRefreshing(true);
    try {
      await post<RefreshReport[]>("/ingest/all");
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }

  const visible = (rows ?? []).filter((r) => !project || r.id === project.id);

  return (
    <section>
      <div className="section-head">
        <h2>Dashboard</h2>
        {isAdmin && rows && rows.length > 0 && (
          <button onClick={refreshAll} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh all projects"}</button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {rows === null && !error && <p className="muted">Loading…</p>}
      {rows && visible.length === 0 && <p className="muted">No projects to show.</p>}
      <div className="cards">
        {visible.map((row) => <ProjectCard key={row.id} row={row} isAdmin={isAdmin} onChanged={load} />)}
      </div>
    </section>
  );
}
