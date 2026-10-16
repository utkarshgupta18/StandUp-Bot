import { FormEvent, useCallback, useEffect, useState } from "react";
import { del, get, patch, post } from "../api";
import type { Project, Role, User } from "../types";

function ProjectPicker({ projects, value, onChange }: { projects: Project[]; value: number[]; onChange: (ids: number[]) => void }) {
  return (
    <div className="picker">
      {projects.map((p) => (
        <label key={p.id} className="inline">
          <input
            type="checkbox"
            checked={value.includes(p.id)}
            onChange={(e) => onChange(e.target.checked ? [...value, p.id] : value.filter((id) => id !== p.id))}
          />{" "}
          {p.name}
        </label>
      ))}
      {projects.length === 0 && <span className="muted small">No projects yet</span>}
    </div>
  );
}

export default function Users({ projects, me }: { projects: Project[]; me: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role, project_ids: [] as number[] });

  const load = useCallback(() => {
    get<User[]>("/users").then(setUsers).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    act(async () => {
      await post("/users", form);
      setForm({ username: "", password: "", role: "viewer", project_ids: [] });
    });
  }

  return (
    <section className="stack">
      <h2>Users</h2>
      <p className="muted">Admins can see and change everything. Viewers can read dashboards and ask questions about the projects ticked for them.</p>
      {error && <p className="error">{error}</p>}

      <table className="table card">
        <thead><tr><th>User</th><th>Role</th><th>Projects</th><th></th></tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.username}{u.id === me.id && <span className="muted"> (you)</span>}</td>
              <td>
                <select value={u.role} disabled={u.id === me.id}
                        onChange={(e) => act(() => patch(`/users/${u.id}`, { role: e.target.value }))}>
                  <option value="viewer">viewer</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>
                {u.role === "admin" ? <span className="muted small">all projects</span> : (
                  <ProjectPicker projects={projects} value={u.project_ids ?? []}
                                 onChange={(ids) => act(() => patch(`/users/${u.id}`, { project_ids: ids }))} />
                )}
              </td>
              <td className="actions">
                <button onClick={() => {
                  const password = window.prompt(`New password for ${u.username} (at least 8 characters)`);
                  if (password) act(() => patch(`/users/${u.id}`, { password }));
                }}>Reset password</button>
                {u.id !== me.id && (
                  <button className="danger" onClick={() => {
                    if (window.confirm(`Delete ${u.username}?`)) act(() => del(`/users/${u.id}`));
                  }}>Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="card stack" onSubmit={create}>
        <h3>Add a user</h3>
        <div className="grid3">
          <label>Username<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required /></label>
          <label>Password<input type="password" minLength={8} value={form.password} autoComplete="new-password"
                                onChange={(e) => setForm({ ...form, password: e.target.value })} required /></label>
          <label>Role
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              <option value="viewer">viewer</option>
              <option value="admin">admin</option>
            </select>
          </label>
        </div>
        {form.role === "viewer" && (
          <fieldset className="fieldset">
            <legend>Projects they can see</legend>
            <ProjectPicker projects={projects} value={form.project_ids} onChange={(ids) => setForm({ ...form, project_ids: ids })} />
          </fieldset>
        )}
        <div className="actions"><button type="submit" className="primary">Create user</button></div>
      </form>
    </section>
  );
}
