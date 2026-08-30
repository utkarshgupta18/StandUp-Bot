"""Streamlit UI for the RAG project tracker.

Talks to the FastAPI backend over HTTP so the two can be deployed/scaled
independently later. Run the API first (see README), then this app.
"""
import requests
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(page_title="Project Tracker", page_icon="\U0001F4CB", layout="wide")
st.title("\U0001F4CB RAG Project Tracker")


def api_get(path: str):
    r = requests.get(f"{API_BASE}{path}")
    r.raise_for_status()
    return r.json()


def api_post(path: str, payload: dict):
    r = requests.post(f"{API_BASE}{path}", json=payload)
    if not r.ok:
        st.error(r.json().get("detail", r.text))
        return None
    return r.json()


# ------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Projects")

    try:
        projects = api_get("/projects")
    except requests.exceptions.ConnectionError:
        st.error("Can't reach the API. Start it with:\n\nuvicorn app.main:app --reload")
        st.stop()

    project_names = [p["name"] for p in projects]
    project_options = ["(all projects)"] + project_names
    selected_name = st.selectbox("Active project", project_options)
    selected_project = next((p for p in projects if p["name"] == selected_name), None)

    st.divider()
    st.subheader("Add a project")
    with st.form("new_project"):
        name = st.text_input("Name")
        docs_path = st.text_input("Docs folder path")
        repo_path = st.text_input("Git repo path")
        submitted = st.form_submit_button("Create")
        if submitted and name:
            result = api_post("/projects", {"name": name, "docs_path": docs_path or None, "repo_path": repo_path or None})
            if result:
                st.success(f"Created project '{name}'")
                st.rerun()

    if selected_project:
        st.divider()
        st.subheader(f"Ingest: {selected_project['name']}")
        if st.button("Re-ingest docs"):
            with st.spinner("Ingesting docs..."):
                report = api_post("/ingest/docs", {"project_id": selected_project["id"]})
            if report:
                st.success(f"Ingested {len(report['ingested'])}, skipped {len(report['skipped'])}")
        if st.button("Re-ingest git history"):
            with st.spinner("Ingesting commits..."):
                report = api_post("/ingest/git", {"project_id": selected_project["id"]})
            if report:
                st.success(f"Ingested {len(report['ingested'])}, skipped {len(report['skipped'])}")

# ------------------------------------------------------------- main chat
st.subheader(f"Ask about: {selected_name}")

if "history" not in st.session_state:
    st.session_state.history = []

for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])

question = st.chat_input("What's the latest update? What's still pending?")
if question:
    st.session_state.history.append({"role": "user", "content": question})
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
                        st.caption(f"{s['source_type']}: {s['source_ref']}")
            st.session_state.history.append({"role": "assistant", "content": result["answer"]})
