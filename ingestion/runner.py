"""Phase 2: re-ingest a whole project (docs + git) in one step.

Used by the "refresh" endpoints, the UI buttons, and the background
scheduler. Every run is recorded in `ingestion_runs`. Ingestion is cheap
on re-runs (unchanged docs and known commits are skipped), so refreshing
everything on a schedule is fine.
"""
from __future__ import annotations

import logging
import threading

from app.config import STATUS_AUTO_GENERATE
from db import database as db
from ingestion.doc_ingestor import ingest_docs
from ingestion.git_ingestor import ingest_git
from ingestion.source_ingestor import ingest_source
from rag import llm

log = logging.getLogger(__name__)

# One ingestion at a time per process, so a scheduled run and a button click
# can't embed the same files concurrently. Re-entrant so reingest_all can
# hold it while calling reingest_project.
ingestion_lock = threading.RLock()


def reingest_project(project_id: int, trigger: str = "manual", auto_status: bool | None = None) -> dict:
    """Re-scan a project's docs folder, pull new commits from its repo, and
    sync its connector sources (Slack, Linear, Jira, Notion).

    If anything new was ingested and an API key is configured, the project's
    structured status is regenerated too (disable with auto_status=False or
    STATUS_AUTO_GENERATE=false).
    """
    project = db.get_project(project_id)
    if not project:
        raise ValueError("Project not found")

    with ingestion_lock:
        run_id = db.start_ingestion_run(project_id, trigger)
        report: dict = {
            "project_id": project_id,
            "project_name": project["name"],
            "run_id": run_id,
            "docs": None,
            "git": None,
            "sources": [],
            "errors": [],
            "status_regenerated": False,
        }

        if project["docs_path"]:
            try:
                report["docs"] = ingest_docs(project_id, project["docs_path"])
                report["errors"].extend(report["docs"]["errors"])
            except Exception as e:  # keep going: git may still work
                report["errors"].append(f"docs: {e}")
        if project["repo_path"]:
            try:
                report["git"] = ingest_git(project_id, project["repo_path"])
                report["errors"].extend(report["git"]["errors"])
            except Exception as e:
                report["errors"].append(f"git: {e}")
        sources = db.list_sources(project_id)
        for source in sources:
            try:
                source_report = ingest_source(source)
                report["sources"].append(source_report)
                report["errors"].extend(f"{source['kind']} '{source['name']}': {e}" for e in source_report["errors"])
            except Exception as e:
                report["errors"].append(f"{source['kind']} '{source['name']}': {e}")
        if not project["docs_path"] and not project["repo_path"] and not sources:
            report["errors"].append("Project has no docs_path, repo_path or sources configured")

        docs_ingested = len(report["docs"]["ingested"]) if report["docs"] else 0
        docs_removed = len(report["docs"]["removed"]) if report["docs"] else 0
        commits_ingested = len(report["git"]["ingested"]) if report["git"] else 0
        items_ingested = sum(len(s["ingested"]) for s in report["sources"])
        report["changed"] = bool(docs_ingested or docs_removed or commits_ingested or items_ingested)

        # A run "fails" only if nothing at all could be read.
        ok = report["docs"] is not None or report["git"] is not None or bool(report["sources"])
        db.finish_ingestion_run(
            run_id,
            "ok" if ok else "error",
            docs_ingested=docs_ingested,
            docs_removed=docs_removed,
            commits_ingested=commits_ingested,
            items_ingested=items_ingested,
            errors=report["errors"],
        )

    if auto_status is None:
        auto_status = STATUS_AUTO_GENERATE
    if auto_status and report["changed"] and llm.is_configured():
        # Imported here to keep ingestion importable without the status module's deps.
        from rag.status import generate_status

        try:
            generate_status(project_id)
            report["status_regenerated"] = True
        except Exception as e:
            log.warning("Status generation failed for project %s: %s", project_id, e)
            report["errors"].append(f"status: {e}")

    return report


def reingest_all(trigger: str = "manual", auto_status: bool | None = None) -> list[dict]:
    """Re-ingest every registered project. One failing project doesn't stop the rest."""
    reports = []
    with ingestion_lock:
        for project in db.list_projects():
            try:
                reports.append(reingest_project(project["id"], trigger, auto_status))
            except Exception as e:
                log.exception("Re-ingestion failed for project %s", project["id"])
                reports.append({"project_id": project["id"], "project_name": project["name"], "errors": [str(e)]})
    return reports
