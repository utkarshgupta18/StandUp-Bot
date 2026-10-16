import { FormEvent, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { post } from "../api";
import type { Answer, AnswerSource, Project } from "../types";

interface Turn {
  role: "user" | "assistant" | "error";
  content: string;
  sources?: AnswerSource[];
}

const SUGGESTIONS_ALL = ["Which projects have pending work?", "What's blocked across everything?", "What shipped recently?"];
const SUGGESTIONS_ONE = ["What's the latest update?", "What's still pending?", "Is anything blocked?"];

function sourceLabel(s: AnswerSource): string {
  if (s.title) return s.title;
  if (s.source_type === "commit") return `commit ${s.source_ref.slice(0, 10)}`;
  return `${s.source_type}: ${s.source_ref}`;
}

export default function Chat({ project, projects }: { project: Project | null; projects: Project[] }) {
  // One conversation per scope, so switching projects doesn't mix them up.
  const [histories, setHistories] = useState<Record<string, Turn[]>>({});
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const key = project ? String(project.id) : "all";
  const history = histories[key] ?? [];

  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [history.length]);

  function append(turn: Turn) {
    setHistories((h) => ({ ...h, [key]: [...(h[key] ?? []), turn] }));
  }

  async function ask(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setQuestion("");
    append({ role: "user", content: q });
    setBusy(true);
    try {
      const res = await post<Answer>("/query", { question: q, project_id: project?.id ?? null });
      append({ role: "assistant", content: res.answer, sources: res.sources });
    } catch (e) {
      append({ role: "error", content: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    ask(question);
  }

  return (
    <section className="chat">
      <div className="chat-head">
        <h2>{project ? `Ask about ${project.name}` : "Ask across all projects"}</h2>
        {history.length > 0 && (
          <button className="link" onClick={() => setHistories((h) => ({ ...h, [key]: [] }))}>Clear conversation</button>
        )}
      </div>

      {projects.length === 0 ? (
        <p className="muted">No projects yet. An admin can add one under “Projects & sources”.</p>
      ) : history.length === 0 ? (
        <div className="suggestions">
          {(project ? SUGGESTIONS_ONE : SUGGESTIONS_ALL).map((s) => (
            <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>
          ))}
        </div>
      ) : null}

      <div className="messages" aria-live="polite">
        {history.map((turn, i) => (
          <div key={i} className={`msg ${turn.role}`}>
            {turn.role === "assistant" ? <Markdown>{turn.content}</Markdown> : <p>{turn.content}</p>}
            {turn.sources && turn.sources.length > 0 && (
              <details className="sources">
                <summary>{turn.sources.length} sources</summary>
                <ul>
                  {turn.sources.map((s, j) => (
                    <li key={j}>
                      {!project && <span className="pill">{s.project_name}</span>}{" "}
                      {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{sourceLabel(s)}</a> : sourceLabel(s)}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant muted">Thinking…</div>}
        <div ref={endRef} />
      </div>

      <form className="ask" onSubmit={submit}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What's the latest update? What's still pending?"
          aria-label="Question"
          disabled={projects.length === 0}
        />
        <button type="submit" className="primary" disabled={busy || !question.trim()}>Ask</button>
      </form>
    </section>
  );
}
