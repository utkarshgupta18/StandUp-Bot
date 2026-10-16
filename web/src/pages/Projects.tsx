import { FormEvent, useCallback, useEffect, useState } from "react";
import { del, get, patch, post } from "../api";
import type { Connector, Project, RefreshReport, Run, Source } from "../types";
import { refreshSummary } from "./Dashboard";

function NewProject({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [docsPath, setDocsPath] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [ingest, setIngest] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const created = await post<{ id: number }>("/projects", {
        name, docs_path: docsPath || null, repo_path: repoPath || null,
      });
      let text = `Created ${name}.`;
      if (ingest && (docsPath || repoPath)) {
        text = refreshSummary(await post<RefreshReport>(`/projects/${created.id}/reingest`));
      }
      setName(""); setDocsPath(""); setRepoPath("");
      setMessage({ kind: "ok", text });
      onCreated();
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h3>Add a project</h3>
      <div className="grid3">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label>Docs folder (on the server)<input value={docsPath} onChange={(e) => setDocsPath(e.target.value)} placeholder="/path/to/docs" /></label>
        <label>Git repo (on the server)<input value={repoPath} onChange={(e) => setRepoPath(e.target.value)} placeholder="/path/to/repo" /></label>
      </div>
      <label className="inline"><input type="checkbox" checked={ingest} onChange={(e) => setIngest(e.target.checked)} /> Ingest right away</label>
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>{busy ? "Working…" : "Create project"}</button>
      </div>
      {message && <p className={message.kind === "ok" ? "ok small" : "error small"}>{message.text}</p>}
    </form>
  );
}

function AddSource({ projectId, connectors, onAdded }: { projectId: number; connectors: Connector[]; onAdded: () => void }) {
  const [kind, setKind] = useState(connectors[0]?.kind ?? "");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const connector = connectors.find((c) => c.kind === kind);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const config = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ""));
    try {
      await post(`/projects/${projectId}/sources`, { kind, name, config });
      setValues({}); setName("");
      onAdded();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (!connector) return null;
  return (
    <form className="add-source stack" onSubmit={submit}>
      <div className="grid3">
        <label>Kind
          <select value={kind} onChange={(e) => { setKind(e.target.value); setValues({}); }}>
            {connectors.map((c) => <option key={c.kind} value={c.kind}>{c.label}</option>)}
          </select>
        </label>
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder={`${connector.label} source`} /></label>
        {connector.fields.map((f) => (
          <label key={f.name} title={f.help}>
            {f.label}{f.required ? " *" : ""}
            <input
              value={values[f.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              placeholder={f.default != null ? String(f.default) : f.help}
              inputMode={f.type === "int" ? "numeric" : undefined}
            />
          </label>
        ))}
      </div>
      {!connector.configured && (
        <p className="warn small">
          {connector.label} credentials aren't set on the server ({connector.env_vars.join(", ")}). You can add the
          source now; syncing will fail until they are.
        </p>
      )}
      {error && <p className="error small">{error}</p>}
      <div className="actions"><button type="submit">Add source</button></div>
    </form>
  );
}

function ProjectPanel({ project, connectors, onChanged }: { project: Project; connectors: Connector[]; onChanged: () => void }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [docsPath, setDocsPath] = useState(project.docs_path ?? "");
  const [repoPath, setRepoPath] = useState(project.repo_path ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(() => {
    get<Source[]>(`/projects/${project.id}/sources`).then(setSources).catch(() => {});
    get<Run[]>(`/projects/${project.id}/runs?limit=5`).then(setRuns).catch(() => {});
  }, [project.id]);

  useEffect(load, [load]);

  async function run(label: string, fn: () => Promise<string | void>) {
    setBusy(label);
    setMessage(null);
    try {
      const text = await fn();
      if (text) setMessage({ kind: "ok", text });
      load();
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <article className="card stack">
      <div className="card-head">
        <h3>{project.name}</h3>
        <div className="actions">
          <button disabled={!!busy} onClick={() => run("refresh", async () =>
            refreshSummary(await post<RefreshReport>(`/projects/${project.id}/reingest`)))}>
            {busy === "refresh" ? "Refreshing…" : "Refresh now"}
          </button>
          <button className="danger" disabled={!!busy} onClick={() => {
            if (!window.confirm(`Delete ${project.name} and everything ingested for it?`)) return;
            run("delete", async () => { await del(`/projects/${project.id}`); onChanged(); });
          }}>Delete</button>
        </div>
      </div>

      <div className="grid3">
        <label>Docs folder<input value={docsPath} onChange={(e) => setDocsPath(e.target.value)} /></label>
        <label>Git repo<input value={repoPath} onChange={(e) => setRepoPath(e.target.value)} /></label>
        <div className="actions end">
          <button disabled={!!busy || (docsPath === (project.docs_path ?? "") && repoPath === (project.repo_path ?? ""))}
            onClick={() => run("paths", async () => {
              await patch(`/projects/${project.id}`, { docs_path: docsPath, repo_path: repoPath });
              onChanged();
              return "Paths saved.";
            })}>Save paths</button>
        </div>
      </div>

      <h4>Sources</h4>
      {sources.length === 0 ? <p className="muted small">No connected sources.</p> : (
        <table className="table">
          <thead><tr><th>Name</th><th>Kind</th><th>Settings</th><th>Last sync</th><th></th></tr></thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.kind}</td>
                <td className="small mono">{Object.entries(s.config).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join(" ")}</td>
                <td className="small">
                  {s.last_synced_at ?? "never"}
                  {s.last_error && <div className="error">{s.last_error}</div>}
                </td>
                <td className="actions">
                  <button disabled={!!busy} onClick={() => run(`sync-${s.id}`, async () => {
                    const r = await post<{ ingested: string[]; skipped: string[] }>(`/sources/${s.id}/sync`);
                    return `${s.name}: ${r.ingested.length} new or changed, ${r.skipped.length} unchanged`;
                  })}>{busy === `sync-${s.id}` ? "Syncing…" : "Sync"}</button>
                  <button className="danger" disabled={!!busy} onClick={() => {
                    if (!window.confirm(`Remove ${s.name} and its ingested items?`)) return;
                    run(`del-${s.id}`, async () => { await del(`/sources/${s.id}`); return "Source removed."; });
                  }}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {connectors.length > 0 && <AddSource projectId={project.id} connectors={connectors} onAdded={load} />}

      {message && <p className={message.kind === "ok" ? "ok small" : "error small"}>{message.text}</p>}

      {runs.length > 0 && (
        <details>
          <summary>Recent refreshes</summary>
          <table className="table small">
            <thead><tr><th>Started</th><th>Trigger</th><th>Result</th><th>Docs</th><th>Commits</th><th>Items</th><th>Errors</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.started_at}</td><td>{r.trigger}</td><td>{r.status}</td>
                  <td>{r.docs_ingested}{r.docs_removed ? ` (−${r.docs_removed})` : ""}</td>
                  <td>{r.commits_ingested}</td><td>{r.items_ingested}</td>
                  <td>{r.errors.join("; ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </article>
  );
}

export default function Projects({ projects, onChanged }: { projects: Project[]; onChanged: () => void }) {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  useEffect(() => {
    get<Connector[]>("/connectors").then(setConnectors).catch(() => {});
  }, []);

  return (
    <section className="stack">
      <h2>Projects & sources</h2>
      <NewProject onCreated={onChanged} />
      {projects.map((p) => <ProjectPanel key={p.id} project={p} connectors={connectors} onChanged={onChanged} />)}
    </section>
  );
}
