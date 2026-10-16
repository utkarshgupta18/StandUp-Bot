"""Streamlit UI for StandUp Bot.

Talks to the FastAPI backend over HTTP so the two can be deployed/scaled
independently later. Run the API first (see README), then this app.
"""
import os

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("STANDUP_API_URL", "http://localhost:8000")
# With AUTH_ENABLED on the API, set a token from `python -m app.manage create-token <user>`.
API_TOKEN = os.getenv("STANDUP_API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
ALL_PROJECTS = "(all projects)"
HEALTH_BADGES = {
    "on_track": "\U0001F7E2 On track",
    "at_risk": "\U0001F7E0 At risk",
    "blocked": "\U0001F534 Blocked",
    "unknown": "⚪ Unknown",
}
SECTION_LABELS = {
    "done": "Done",
    "in_progress": "In progress",
    "next_steps": "Next steps",
    "blockers": "Blockers",
}

st.set_page_config(page_title="StandUp Bot", page_icon="\U0001F4CB", layout="wide")
st.title("\U0001F4CB StandUp Bot")


def api_get(path: str, quiet: bool = False):
    r = requests.get(f"{API_BASE}{path}", headers=HEADERS, timeout=60)
    if not r.ok:
        if not quiet:
            st.error(_detail(r))
        return None
    return r.json()


def api_post(path: str, payload: dict | None = None, params: dict | None = None):
    r = requests.post(f"{API_BASE}{path}", json=payload, params=params, headers=HEADERS, timeout=600)
    if not r.ok:
        st.error(_detail(r))
        return None
    return r.json()


def api_delete(path: str):
    r = requests.delete(f"{API_BASE}{path}", headers=HEADERS, timeout=60)
    if not r.ok:
        st.error(_detail(r))
        return None
    return r.json()


def _detail(r: requests.Response) -> str:
    try:
        return r.json().get("detail", r.text)
    except ValueError:
        return r.text


def reingest_summary(report: dict) -> str:
    docs = report.get("docs") or {}
    git = report.get("git") or {}
    parts = [
        f"**{report.get('project_name')}**: "
        f"{len(docs.get('ingested', []))} docs updated, "
        f"{len(docs.get('removed', []))} removed, "
        f"{len(git.get('ingested', []))} new commits"
    ]
    if report.get("status_regenerated"):
        parts.append("status regenerated")
    return ", ".join(parts)


def show_reports(reports: list[dict]) -> None:
    for report in reports:
        st.success(reingest_summary(report))
        for err in report.get("errors", []):
            st.warning(err)


def render_status(update: dict | None) -> None:
    if not update or not update.get("status"):
        st.caption("No status generated yet.")
        return
    status = update["status"]
    st.markdown(f"{HEALTH_BADGES.get(status.get('health'), HEALTH_BADGES['unknown'])} · generated {update['generated_at']} UTC")
    st.write(update["summary"])
    cols = st.columns(len(SECTION_LABELS))
    for col, (key, label) in zip(cols, SECTION_LABELS.items()):
        with col:
            st.markdown(f"**{label}**")
            items = status.get(key) or []
            if not items:
                st.caption("None")
            for item in items:
                refs = ", ".join(s[:10] if len(s) == 40 else s for s in item.get("sources", []))
                st.markdown(f"- {item['text']}" + (f"  \n  <small>{refs}</small>" if refs else ""), unsafe_allow_html=True)


# ------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Projects")

    try:
        projects = api_get("/projects") or []
    except requests.exceptions.ConnectionError:
        st.error(f"Can't reach the API at {API_BASE}. Start it with:\n\nuvicorn app.main:app --reload")
        st.stop()

    project_options = [ALL_PROJECTS] + [p["name"] for p in projects]
    selected_name = st.selectbox("Active project", project_options)
    selected_project = next((p for p in projects if p["name"] == selected_name), None)

    if st.button("\U0001F504 Refresh all projects", use_container_width=True, disabled=not projects):
        with st.spinner("Re-ingesting every project..."):
            reports = api_post("/ingest/all")
        if reports is not None:
            show_reports(reports)

    if selected_project:
        st.divider()
        st.subheader(selected_project["name"])
        st.caption(f"Docs: {selected_project['docs_path'] or '—'}  \nRepo: {selected_project['repo_path'] or '—'}")
        if st.button("Refresh this project (docs + git)", use_container_width=True):
            with st.spinner("Re-ingesting..."):
                report = api_post(f"/projects/{selected_project['id']}/reingest")
            if report:
                show_reports([report])
        if st.button("Generate status now", use_container_width=True):
            with st.spinner("Asking Claude for a structured status..."):
                update = api_post(f"/projects/{selected_project['id']}/status")
            if update:
                st.success("Status updated — see the Dashboard tab.")
        with st.expander("Danger zone"):
            if st.button("Delete project", type="secondary"):
                if api_delete(f"/projects/{selected_project['id']}"):
                    st.rerun()

    st.divider()
    st.subheader("Add a project")
    with st.form("new_project", clear_on_submit=True):
        name = st.text_input("Name")
        docs_path = st.text_input("Docs folder path")
        repo_path = st.text_input("Git repo path")
        ingest_now = st.checkbox("Ingest right away", value=True)
        submitted = st.form_submit_button("Create")
        if submitted and name:
            result = api_post("/projects", {"name": name, "docs_path": docs_path or None, "repo_path": repo_path or None})
            if result:
                if ingest_now:
                    with st.spinner("Ingesting..."):
                        api_post(f"/projects/{result['id']}/reingest")
                st.success(f"Created project '{name}'")
                st.rerun()

    health = api_get("/health", quiet=True) or {}
    sched = health.get("scheduler") or {}
    st.divider()
    if sched.get("enabled"):
        st.caption(f"Auto-refresh every {sched['interval_minutes']} min · last run: {sched.get('last_run_at') or 'not yet'}")
    else:
        st.caption("Auto-refresh off (set REINGEST_INTERVAL_MINUTES to enable)")
    if not health.get("llm_configured", True):
        st.warning("ANTHROPIC_API_KEY isn't set: ingestion works, but chat and status generation won't.")

chat_tab, dashboard_tab = st.tabs(["\U0001F4AC Chat", "\U0001F4CA Dashboard"])

# ------------------------------------------------------------- chat
with chat_tab:
    st.subheader(f"Ask about: {selected_name}")
    if selected_project is None:
        st.caption("Searching across every project — try \"Which projects have pending work?\"")

    # Separate history per scope, so switching projects doesn't mix conversations.
    histories = st.session_state.setdefault("histories", {})
    history = histories.setdefault(selected_name, [])

    for turn in history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            if turn.get("sources"):
                with st.expander("Sources"):
                    for s in turn["sources"]:
                        st.caption(f"{s.get('project_name', '')} · {s['source_type']}: {s['source_ref']}")

    question = st.chat_input("What's the latest update? What's still pending?")
    if question:
        history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        project_id = selected_project["id"] if selected_project else None
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                result = api_post("/query", {"question": question, "project_id": project_id})
            if result:
                st.markdown(result["answer"])
                if result["sources"]:
                    with st.expander("Sources"):
                        for s in result["sources"]:
                            st.caption(f"{s.get('project_name', '')} · {s['source_type']}: {s['source_ref']}")
                history.append({"role": "assistant", "content": result["answer"], "sources": result["sources"]})

    if history and st.button("Clear conversation"):
        histories[selected_name] = []
        st.rerun()

# ------------------------------------------------------------- dashboard
with dashboard_tab:
    rows = api_get("/dashboard") or []
    if selected_project:
        rows = [r for r in rows if r["id"] == selected_project["id"]]
    if not rows:
        st.info("No projects yet. Add one in the sidebar.")
    for row in rows:
        with st.container(border=True):
            update = row.get("latest_update")
            run = row.get("last_run") or {}
            st.markdown(f"### {row['name']}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Docs", row["counts"]["doc"])
            c2.metric("Commits", row["counts"]["commit"])
            c3.metric("Last commit", (row.get("last_commit_date") or "—")[:10])
            c4.metric("Last refresh", (run.get("finished_at") or run.get("started_at") or "never")[:16])
            if run.get("errors"):
                with st.expander(f"Last refresh had {len(run['errors'])} error(s)"):
                    for err in run["errors"]:
                        st.caption(err)
            render_status(update)
            if st.button("Regenerate status", key=f"status-{row['id']}"):
                with st.spinner("Asking Claude..."):
                    if api_post(f"/projects/{row['id']}/status"):
                        st.rerun()
