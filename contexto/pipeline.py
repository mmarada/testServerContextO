"""Main ContextO orchestration loop: live errors → tracer → tests; commits → guards."""

from __future__ import annotations

import asyncio
import atexit
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_WEBHOOK_RETRY_INTERVAL_SECONDS = 30

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
from contexto.memory.context_store import LOW_DIGEST_KIND, ContextStore
from contexto.notifications.slack_notifier import (
    build_digest_payload,
    notify_slack,
    post_slack_payload,
)
from contexto.severity import SeverityPolicy, load_policy
from contexto.severity import classify as classify_severity
from live_agent import build_mcp_client, run_tracer


def _error_type_from_event(error: dict[str, Any]) -> str:
    msg = str(error.get("error", "")).strip()
    if not msg:
        return ""
    return msg.split(":", 1)[0].strip()


def _recurrence_alert_due(new_count: int, interval: int) -> bool:
    """True when a known-bug hit count lands on a recurrence-alert milestone.

    ``interval`` comes from RECURRENCE_ALERT_INTERVAL (default 10). A value of
    0 or less disables recurrence alerts entirely rather than raising on the
    modulo.
    """
    if interval <= 0:
        return False
    return new_count % interval == 0


def _low_digest_due(
    now: datetime,
    *,
    oldest_pending_at: datetime | None,
    last_success_at: datetime | None,
    last_attempt_at: datetime | None,
    consecutive_failures: int,
    interval_hours: int,
    retry_base_delay_seconds: int,
) -> bool:
    """True when the held-back LOW incidents should be sent as one digest.

    A digest goes out ``interval_hours`` after whichever is later: the last
    successful digest, or the oldest incident still waiting. Anchoring on the
    oldest pending incident means a quiet week followed by one LOW error still
    waits a full window for company instead of firing instantly.

    After a failed send, retries back off exponentially from
    ``retry_base_delay_seconds`` (the same base the webhook retry queue uses),
    capped at one window so a long Slack outage can't delay past the next
    scheduled digest.

    ``interval_hours <= 0`` means the digest is off; anything still queued from
    when it was on is flushed right away so it can't be stranded.
    """
    if oldest_pending_at is None:
        return False
    window = timedelta(hours=max(interval_hours, 0))
    if consecutive_failures > 0 and last_attempt_at is not None:
        backoff = timedelta(
            seconds=retry_base_delay_seconds * 2 ** (consecutive_failures - 1)
        )
        return now - last_attempt_at >= min(backoff, window or timedelta(hours=1))
    baseline = oldest_pending_at
    if last_success_at is not None and last_success_at > baseline:
        baseline = last_success_at
    return now - baseline >= window


def _parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


async def _process_low_digest(store: ContextStore, settings: Settings) -> None:
    """Send one Slack message covering every LOW incident held back since the last digest."""
    if not settings.slack_webhook_url:
        return
    pending = await store.get_pending_digest_incidents()
    if not pending:
        return
    state = await store.get_digest_delivery_state()
    if not _low_digest_due(
        datetime.now(timezone.utc),
        oldest_pending_at=_parse_iso(pending[0]["digest_queued_at"]),
        last_success_at=_parse_iso(state["last_success_at"]),
        last_attempt_at=_parse_iso(state["last_attempt_at"]),
        consecutive_failures=state["consecutive_failures"],
        interval_hours=settings.low_digest_interval_hours,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
    ):
        return

    payload = build_digest_payload(pending, settings.low_digest_interval_hours)
    ok = await post_slack_payload(settings.slack_webhook_url, payload)
    await store.log_webhook_delivery(
        incident_id=None,
        trace_id=None,
        kind=LOW_DIGEST_KIND,
        success=ok,
        detail=f"{len(pending)} LOW incidents"
        + ("" if ok else " (send failed; will retry with backoff)"),
    )
    if ok:
        await store.mark_incidents_digested([r["incident_id"] for r in pending])
        print(f"[ContextO] pipeline: LOW digest sent ({len(pending)} incidents)")
    else:
        print(f"[ContextO] pipeline: LOW digest failed ({len(pending)} incidents held)")


def _incident_row(
    trace_map: dict[str, Any],
    error: dict[str, Any],
    error_count: int = 0,
    severity_policy: SeverityPolicy | None = None,
) -> dict[str, Any]:
    fp = trace_map["file_path"]
    error_type = _error_type_from_event(error)
    severity = classify_severity(fp, error_count, error_type, severity_policy)
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


async def _process_webhook_retries(store: ContextStore, settings: Settings) -> None:
    """Retry previously-failed Slack deliveries whose backoff window has elapsed."""
    if not settings.slack_webhook_url:
        return
    due = await store.get_due_webhook_retries()
    for row in due:
        ok = await notify_slack(settings.slack_webhook_url, row["payload"])
        await store.log_webhook_delivery(
            incident_id=row["incident_id"],
            trace_id=row["trace_id"],
            kind=f"{row['kind']}:retry",
            success=ok,
            detail="" if ok else f"retry attempt {row['attempt'] + 1} failed",
        )
        await store.record_webhook_retry_result(
            row["id"],
            row["attempt"],
            ok,
            error="" if ok else "delivery failed",
        )
        if ok:
            print(
                f"[ContextO] pipeline: webhook retry succeeded "
                f"(trace_id={row['trace_id']}, attempt={row['attempt'] + 1})"
            )
        else:
            print(
                f"[ContextO] pipeline: webhook retry failed "
                f"(trace_id={row['trace_id']}, attempt={row['attempt'] + 1})"
            )


async def run_pipeline(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    # Load before anything else so a bad rules file fails fast at startup.
    severity_policy = load_policy(settings.severity_rules_path)
    if settings.severity_rules_path:
        print(
            f"[ContextO] pipeline: severity rules from {settings.severity_rules_path} "
            f"({len(severity_policy.rules)} rules)"
        )
    store = ContextStore(
        settings.db_path,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_attempts=settings.retry_max_attempts,
    )
    await store.init()

    seen_trace_ids: set[str] = set()
    last_commit_poll = 0.0
    last_webhook_retry_poll = 0.0
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
                incident = _incident_row(
                    trace_map, error, current_error_count, severity_policy
                )
                stored = await store.log_incident(incident)
                if not stored:
                    print(f"[ContextO] pipeline: incident trace_id={tid} already in DB; skipping")
                    continue

                if stored and not failure_seen_before:
                    hold_for_digest = (
                        settings.slack_webhook_url
                        and settings.low_digest_interval_hours > 0
                        and incident["severity"] == "LOW"
                    )
                    if hold_for_digest:
                        await store.queue_incident_for_digest(incident["incident_id"])
                        print(
                            f"[ContextO] pipeline: LOW incident held for "
                            f"{settings.low_digest_interval_hours}h digest"
                        )
                    elif settings.slack_webhook_url:
                        ok = await notify_slack(settings.slack_webhook_url, incident)
                        await store.log_webhook_delivery(
                            incident_id=incident["incident_id"],
                            trace_id=incident["trace_id"],
                            kind="slack:new_incident",
                            success=ok,
                            detail="" if ok else "non-200 response or request failed",
                        )
                        if not ok:
                            await store.enqueue_webhook_retry(
                                incident_id=incident["incident_id"],
                                trace_id=incident["trace_id"],
                                kind="slack:new_incident",
                                payload=incident,
                            )
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
                    if (
                        _recurrence_alert_due(
                            new_count, settings.recurrence_alert_interval
                        )
                        and not snoozed
                        and settings.slack_webhook_url
                    ):
                        reminder = dict(incident)
                        reminder["root_cause"] = (
                            f"[Recurrence #{new_count}] " + reminder.get("root_cause", "")
                        )
                        ok = await notify_slack(settings.slack_webhook_url, reminder)
                        await store.log_webhook_delivery(
                            incident_id=incident["incident_id"],
                            trace_id=incident["trace_id"],
                            kind="slack:recurrence",
                            success=ok,
                            detail="" if ok else "non-200 response or request failed",
                        )
                        if not ok:
                            await store.enqueue_webhook_retry(
                                incident_id=incident["incident_id"],
                                trace_id=incident["trace_id"],
                                kind="slack:recurrence",
                                payload=reminder,
                            )
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

            if now - last_webhook_retry_poll >= float(_WEBHOOK_RETRY_INTERVAL_SECONDS):
                last_webhook_retry_poll = now
                await _process_webhook_retries(store, settings)
                await _process_low_digest(store, settings)

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
