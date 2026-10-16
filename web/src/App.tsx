import { useCallback, useEffect, useState } from "react";
import { ApiError, get, setToken, setUnauthorizedHandler } from "./api";
import type { HealthInfo, Project, User } from "./types";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Projects from "./pages/Projects";
import Users from "./pages/Users";

type Tab = "chat" | "dashboard" | "projects" | "users";
const ALL = "all";

export default function App() {
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [scope, setScope] = useState<string>(ALL);
  const [tab, setTab] = useState<Tab>("chat");
  const [error, setError] = useState<string | null>(null);

  const signOut = useCallback(() => {
    setToken(null);
    setUser(null);
    setScope(ALL);
    setTab("chat");
  }, []);

  useEffect(() => setUnauthorizedHandler(signOut), [signOut]);

  const loadSession = useCallback(async () => {
    setError(null);
    try {
      const cfg = await get<{ auth_enabled: boolean }>("/auth/config");
      setAuthEnabled(cfg.auth_enabled);
      setUser(await get<User>("/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setUser(null);
      else setError((e as Error).message);
    }
  }, []);

  const refreshProjects = useCallback(async () => {
    const list = await get<Project[]>("/projects");
    setProjects(list);
    setScope((s) => (s === ALL || list.some((p) => String(p.id) === s) ? s : ALL));
  }, []);

  useEffect(() => {
    loadSession();
  }, [loadSession]);

  useEffect(() => {
    if (!user) return;
    refreshProjects().catch((e) => setError((e as Error).message));
    get<HealthInfo>("/health").then(setHealth).catch(() => {});
  }, [user, refreshProjects]);

  if (error && authEnabled === null) {
    return (
      <div className="center-page">
        <div className="card narrow">
          <h1>StandUp Bot</h1>
          <p className="error">{error}</p>
          <button onClick={loadSession}>Try again</button>
        </div>
      </div>
    );
  }
  if (authEnabled === null) return <div className="center-page muted">Loading…</div>;
  if (!user) return <Login onSignedIn={(u) => setUser(u)} />;

  const isAdmin = user.role === "admin";
  const selected = projects.find((p) => String(p.id) === scope) ?? null;
  const tabs: { key: Tab; label: string; show: boolean }[] = [
    { key: "chat", label: "Chat", show: true },
    { key: "dashboard", label: "Dashboard", show: true },
    { key: "projects", label: "Projects & sources", show: isAdmin },
    { key: "users", label: "Users", show: isAdmin && authEnabled },
  ];
  // A tab the current user can't see (e.g. after an admin signs out and a
  // viewer signs in) falls back to Chat instead of rendering nothing.
  const activeTab: Tab = tabs.some((t) => t.key === tab && t.show) ? tab : "chat";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">📋 StandUp Bot</div>
        <nav className="tabs" aria-label="Sections">
          {tabs.filter((t) => t.show).map((t) => (
            <button key={t.key} className={activeTab === t.key ? "tab active" : "tab"} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="topbar-right">
          <label className="scope">
            <span className="sr-only">Project</span>
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value={ALL}>All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={String(p.id)}>{p.name}</option>
              ))}
            </select>
          </label>
          {authEnabled && (
            <span className="whoami">
              {user.username} <span className="pill">{user.role}</span>
              <button className="link" onClick={signOut}>Sign out</button>
            </span>
          )}
        </div>
      </header>

      {health && !health.llm_configured && (
        <div className="banner warn">
          ANTHROPIC_API_KEY isn't set on the server: ingestion works, but chat and status generation won't.
        </div>
      )}
      {error && <div className="banner error">{error}</div>}

      <main className="content">
        {activeTab === "chat" && <Chat project={selected} projects={projects} />}
        {activeTab === "dashboard" && <Dashboard project={selected} isAdmin={isAdmin} />}
        {activeTab === "projects" && <Projects projects={projects} onChanged={refreshProjects} />}
        {activeTab === "users" && <Users projects={projects} me={user} />}
      </main>

      {health && (
        <footer className="footer muted">
          Storage: {health.storage.database} + {health.storage.vectors} ·{" "}
          {health.scheduler.enabled
            ? `auto-refresh every ${health.scheduler.interval_minutes} min (last: ${health.scheduler.last_run_at ?? "not yet"})`
            : "auto-refresh off"}
        </footer>
      )}
    </div>
  );
}
