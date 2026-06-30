"""Main ContextO orchestration loop: live errors → tracer → tests; commits → guards."""

from __future__ import annotations

import asyncio
import atexit
import os
import subprocess
import time
from pathlib import Path
from typing import Any

_vite_proc: subprocess.Popen | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _start_ui_if_enabled() -> None:
    """Spawn `npm run dev` in frontend/ unless CONTEXTO_SKIP_UI is set."""
    global _vite_proc
    if os.getenv("CONTEXTO_SKIP_UI", "").lower() in ("1", "true", "yes"):
        print("[ContextO] UI: skipped (CONTEXTO_SKIP_UI=1)")
        return

    fd = _repo_root() / "frontend"
    if not (fd / "package.json").is_file():
        print(
            "[ContextO] UI: no frontend/package.json — run the UI manually "
            "(cd frontend && npm run dev)"
        )
        return
    if not (fd / "node_modules").is_dir():
        print(
            "[ContextO] UI: run `cd frontend && npm install` once; "
            "skipping auto-start until node_modules exists"
        )
        return

    try:
        _vite_proc = subprocess.Popen(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
            cwd=str(fd),
            stdin=subprocess.DEVNULL,
        )
    except OSError as e:
        print(f"[ContextO] UI: could not start npm ({e})")
        _vite_proc = None
        return

    print(
        "[ContextO] UI: starting Vite in the same terminal (http://127.0.0.1:5173). "
        "Proxy sends /api → Flask on :5000 — keep `python app.py` running."
    )


def _stop_ui() -> None:
    global _vite_proc
    p = _vite_proc
    _vite_proc = None
    if p is None or p.poll() is not None:
        return
    print("[ContextO] UI: stopping Vite…")
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()


atexit.register(_stop_ui)

from langchain_google_genai import ChatGoogleGenerativeAI

from config import Settings, get_settings
from contexto.agents.commit_guard import run_commit_guard
from contexto.agents.test_generator import (
    DUPLICATE_TEST_SENTINEL,
    poll_logs,
    run_test_generator,
)
from contexto.ingestion.commit_watcher import CommitWatcher
from contexto.memory.context_store import ContextStore
from contexto.notifications.slack_notifier import notify_slack
from contexto.severity import classify as classify_severity
from live_agent import build_mcp_client, run_tracer


def _error_type_from_event(error: dict[str, Any]) -> str:
    msg = str(error.get("error", "")).strip()
    if not msg:
        return ""
    return msg.split(":", 1)[0].strip()


def _incident_row(
    trace_map: dict[str, Any],
    error: dict[str, Any],
    error_count: int = 0,
) -> dict[str, Any]:
    fp = trace_map["file_path"]
    error_type = _error_type_from_event(error)
    severity = classify_severity(fp, error_count, error_type)
    return {
        "incident_id": trace_map["incident_id"],
        "trace_id": trace_map["trace_id"],
        "commit_sha": trace_map.get("commit_sha"),
        "user_action": trace_map.get("user_action", ""),
        "log_trace": trace_map.get("log_trace", ""),
        "file_path": fp,
        "line_number": int(trace_map["line_number"]),
        "root_cause": trace_map.get("root_cause", ""),
        "severity": severity,
        "created_at": trace_map.get("created_at") or error.get("timestamp"),
    }


async def run_pipeline(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    store = ContextStore(settings.db_path)
    await store.init()

    seen_trace_ids: set[str] = set()
    last_commit_poll = 0.0
    mcp_client = build_mcp_client()
    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.google_api_key,
    )

    watcher = CommitWatcher(owner=settings.github_owner, repo=settings.github_repo)
    all_tools = await mcp_client.get_tools()
    list_commits_tool = next((t for t in all_tools if t.name == "list_commits"), None)
    if list_commits_tool is None:
        print("[ContextO] pipeline: list_commits tool missing; commit watching disabled")

    print(
        f"[ContextO] pipeline: online (logs={settings.log_source_url}, "
        f"repo={settings.github_owner}/{settings.github_repo}, poll={settings.poll_interval}s)"
    )

    while True:
        try:
            logs = await poll_logs(settings.log_source_url)
            new_errors = [l for l in logs if l.get("trace_id") and l["trace_id"] not in seen_trace_ids]

            for error in new_errors:
                tid = str(error["trace_id"])
                seen_trace_ids.add(tid)
                print(f"[ContextO] pipeline: new error trace_id={tid}")

                trace_map = await run_tracer(error, mcp_client, llm, settings)
                fp = trace_map.get("file_path") or trace_map.get("file", "")
                error_type = _error_type_from_event(error)
                failure_seen_before = await store.incident_exists_for_signature(
                    fp, int(trace_map["line_number"]), error_type
                )
                existing_ctx = await store.get_context_for_file(fp)
                current_error_count = int((existing_ctx or {}).get("error_count", 0))
                incident = _incident_row(trace_map, error, current_error_count)
                stored = await store.log_incident(incident)
                if not stored:
                    print(f"[ContextO] pipeline: incident trace_id={tid} already in DB; skipping")
                    continue

                if stored and not failure_seen_before:
                    if settings.slack_webhook_url:
                        await notify_slack(settings.slack_webhook_url, incident)
                    print(f"[ContextO] pipeline: severity={incident['severity']} for {fp}")

                    await store.upsert_file_context(
                        fp,
                        str(trace_map.get("function", "unknown")),
                        error,
                        [],
                        bump_error_count=True,
                    )
                    test_str = await run_test_generator(
                        trace_map,
                        mcp_client,
                        llm,
                        store,
                        settings,
                        error,
                    )
                    if test_str == DUPLICATE_TEST_SENTINEL:
                        print(
                            "[ContextO] test_generator: secondary duplicate guard; "
                            "no new test persisted"
                        )
                    else:
                        print(
                            f"[ContextO] New bug signature → test generated for {fp}"
                        )
                else:
                    new_count = await store.increment_error_count(fp)
                    snoozed = await store.is_signature_snoozed(
                        fp, int(trace_map["line_number"]), error_type
                    )
                    if new_count % 10 == 0 and not snoozed and settings.slack_webhook_url:
                        reminder = dict(incident)
                        reminder["root_cause"] = (
                            f"[Recurrence #{new_count}] " + reminder.get("root_cause", "")
                        )
                        await notify_slack(settings.slack_webhook_url, reminder)
                        print(
                            f"[ContextO] Known bug hit #{new_count} → Slack recurrence alert sent"
                        )
                    elif snoozed:
                        print(
                            f"[ContextO] Known bug hit #{new_count} → snoozed, "
                            "no recurrence alert"
                        )
                    else:
                        print(
                            "[ContextO] Known bug hit again → error_count incremented, "
                            "no duplicate test"
                        )

            now = time.monotonic()
            if list_commits_tool is not None and (
                now - last_commit_poll >= float(settings.commit_poll_interval)
            ):
                last_commit_poll = now
                print("[ContextO] pipeline: polling GitHub for new commits")
                new_commits = await watcher.poll(list_commits_tool)
                for sha in new_commits:
                    await run_commit_guard(sha, mcp_client, store, settings)

        except Exception as e:  # noqa: BLE001
            print(f"[ContextO] pipeline: loop error: {e!r}")

        await asyncio.sleep(int(os.getenv("POLL_INTERVAL", str(settings.poll_interval))))


def main() -> None:
    _start_ui_if_enabled()
    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        print("\n[ContextO] pipeline: stopped")
    finally:
        _stop_ui()


if __name__ == "__main__":
    main()
