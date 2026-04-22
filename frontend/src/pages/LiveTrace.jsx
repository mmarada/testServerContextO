import { useEffect, useMemo, useRef, useState } from "react";
import { usePoller } from "../hooks/usePoller.js";
import { CodeBlock } from "../components/CodeBlock.jsx";
import { TraceStep } from "../components/TraceStep.jsx";

function errorType(msg) {
  if (!msg) return "";
  return String(msg).split(":")[0].trim();
}

function basename(p) {
  return (p || "").split("/").pop() || "";
}

function pickLatest(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const sorted = [...rows].sort((a, b) => {
    const tb = new Date(b.timestamp || b.created_at || 0).getTime();
    const ta = new Date(a.timestamp || a.created_at || 0).getTime();
    return tb - ta;
  });
  return sorted[0];
}

export function LiveTrace({
  config,
  focusTraceId,
  clearFocusTrace,
  onNewIncident,
  onTestGenerated,
}) {
  const logUrl = config.logSourceUrl || "/api/logs";
  const sessionUrl = config.sessionEventsUrl?.trim() || "";
  const sessionEnabled = Boolean(config.monitoringActive && sessionUrl);

  const logsPoll = usePoller(logUrl, 30_000, {
    enabled: Boolean(config.monitoringActive),
  });
  const incidentsPoll = usePoller("/api/incidents", 30_000, {
    enabled: Boolean(config.monitoringActive),
  });
  const ctxPoll = usePoller("/api/file-context", 30_000, {
    enabled: Boolean(config.monitoringActive),
  });
  const sessionPoll = usePoller(sessionUrl || "/api/session-events", 30_000, {
    enabled: sessionEnabled,
  });

  const seenTraces = useRef(null);
  const [banner, setBanner] = useState(null);

  useEffect(() => {
    const logs = logsPoll.data;
    if (!Array.isArray(logs)) return;
    if (seenTraces.current === null) {
      seenTraces.current = new Set(
        logs.map((l) => l.trace_id).filter(Boolean)
      );
      return;
    }
    for (const row of logs) {
      if (row.trace_id && !seenTraces.current.has(row.trace_id)) {
        seenTraces.current.add(row.trace_id);
        const et = errorType(row.error);
        setBanner({
          trace_id: row.trace_id,
          file: row.file,
          error: et,
          ts: row.timestamp || new Date().toISOString(),
        });
        onNewIncident?.(
          `New incident — ${et} in ${row.file} · trace_id: ${row.trace_id}`
        );
      }
    }
  }, [logsPoll.data, onNewIncident]);

  const currentLog = useMemo(() => {
    const logs = Array.isArray(logsPoll.data) ? logsPoll.data : [];
    if (focusTraceId) {
      const hit = logs.find((l) => l.trace_id === focusTraceId);
      if (hit) return hit;
    }
    return pickLatest(logs);
  }, [logsPoll.data, focusTraceId]);

  const incidentForTrace = useMemo(() => {
    const inc = Array.isArray(incidentsPoll.data) ? incidentsPoll.data : [];
    if (!currentLog?.trace_id) return null;
    return inc.find((i) => i.trace_id === currentLog.trace_id) || null;
  }, [incidentsPoll.data, currentLog]);

  const fileCtx = useMemo(() => {
    const rows = Array.isArray(ctxPoll.data) ? ctxPoll.data : [];
    const f = currentLog?.file;
    if (!f) return null;
    const b = basename(f);
    return (
      rows.find((r) => r.file_path === f) ||
      rows.find((r) => basename(r.file_path) === b) ||
      null
    );
  }, [ctxPoll.data, currentLog]);

  const sessionRow = useMemo(() => {
    if (!sessionEnabled) return null;
    const ev = Array.isArray(sessionPoll.data) ? sessionPoll.data : [];
    if (!currentLog?.trace_id) return null;
    return ev.find((e) => e.trace_id === currentLog.trace_id) || null;
  }, [sessionEnabled, sessionPoll.data, currentLog]);

  const configKeywords =
    /\b(DATABASE|SECRET|ENV|API_KEY|credential|token|password)\b/i;
  const configHit = currentLog?.error && configKeywords.test(currentLog.error);

  const tests = Array.isArray(fileCtx?.generated_tests)
    ? fileCtx.generated_tests
    : [];
  const firstTest = tests[0] || "";
  const testFnMatch = firstTest.match(/\bdef\s+(test_\w+)\s*\(/);
  const testFnName = testFnMatch ? testFnMatch[1] : "—";
  const dupLine = `${tests.length} stored · ${
    Math.max(0, (fileCtx?.error_count || 0) - tests.length)
  } duplicates skipped (approx.)`;

  const steps = currentLog
    ? [
        {
          key: "s1",
          status: "done",
          title: "User action captured",
          detail: `Event: ${currentLog.event || "—"} · trace: ${currentLog.trace_id}`,
          body: sessionRow ? (
            <div className="text-[10px] text-ctx-muted">
              Session row: {JSON.stringify(sessionRow).slice(0, 240)}…
            </div>
          ) : (
            <div className="text-[10px] text-ctx-muted">
              No session row (optional source empty or no correlation).
            </div>
          ),
        },
        {
          key: "s2",
          status: "done",
          title: "Log trace matched",
          detail: `${currentLog.file}:${currentLog.line} · ${errorType(
            currentLog.error
          )}`,
          body: (
            <CodeBlock
              code={currentLog.error || ""}
              language="log"
              borderAccent
            />
          ),
        },
        {
          key: "s3",
          status: "done",
          title: "Config & environment check",
          detail: configHit
            ? "Possible config / secret keyword in error text — review env."
            : "No config issues detected (heuristic).",
        },
        {
          key: "s4",
          status: fileCtx ? "done" : "active",
          title: "Code located in repo",
          detail: fileCtx
            ? `Function: ${fileCtx.function_name || "—"} · last_commit_sha: ${
                fileCtx.last_commit_sha || "—"
              }`
            : "Waiting for file_context from pipeline / DB…",
          body: fileCtx ? (
            <>
              <CodeBlock
                code={`# ${currentLog.file}:${currentLog.line}\n# (snippet not served by API — open repo)\n# ${(
                  incidentForTrace?.root_cause ||
                  currentLog.error ||
                  ""
                ).slice(0, 400)}`}
                language="python"
                highlightLineIndex={2}
              />
              <div className="border border-ctx-border bg-ctx-card p-2 text-[10px] text-ctx-muted">
                <div className="text-ctx-text">
                  commit{" "}
                  <span className="text-ctx-accent">
                    {(fileCtx.last_commit_sha || "???????").slice(0, 7)}
                  </span>
                </div>
                <div>message: — (not in API)</div>
                <div>author: — · date: —</div>
                <div>files: {basename(currentLog.file)}</div>
              </div>
            </>
          ) : null,
        },
        {
          key: "s5",
          status: tests.length ? "done" : "pending",
          title: "Regression test generated",
          detail: `Test: ${testFnName} · ${dupLine}`,
          body: tests.length ? (
            <>
              <CodeBlock code={firstTest} language="python" />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="border border-ctx-border px-2 py-1 text-[10px] text-ctx-accent hover:bg-ctx-bg"
                  onClick={() => {
                    const stem = basename(currentLog.file).replace(
                      /\.[^.]+$/,
                      ""
                    );
                    const cmd = `pytest tests/regression/${stem}_test.py -v`;
                    navigator.clipboard.writeText(cmd);
                    onTestGenerated?.("Pytest command copied to clipboard.");
                  }}
                >
                  Run test (copy pytest)
                </button>
                <button
                  type="button"
                  disabled
                  title="Future: push to repo"
                  className="cursor-not-allowed border border-ctx-border px-2 py-1 text-[10px] text-ctx-muted"
                >
                  Push to repo (disabled)
                </button>
              </div>
            </>
          ) : (
            <div className="text-[10px] text-ctx-muted">
              Pipeline has not persisted tests for this file yet.
            </div>
          ),
        },
      ]
    : [];

  const fmt = (d) =>
    d ? d.toLocaleTimeString(undefined, { hour12: false }) : "—";

  return (
    <div className="ctx-grid flex min-h-full flex-col p-6">
      {focusTraceId && (
        <div className="mb-3 flex items-center justify-between border border-ctx-border bg-ctx-card px-2 py-1 text-[10px] text-ctx-muted">
          <span>
            Viewing trace <span className="text-ctx-accent">{focusTraceId}</span>
          </span>
          <button
            type="button"
            onClick={clearFocusTrace}
            className="text-ctx-accent hover:underline"
          >
            clear focus
          </button>
        </div>
      )}

      {banner && (
        <div className="mb-4 flex items-start gap-3 border border-ctx-err bg-ctx-card px-3 py-2">
          <span className="mt-0.5 h-2 w-2 shrink-0 animate-pulse-dot rounded-full bg-ctx-err" />
          <div className="min-w-0 flex-1 text-[11px]">
            <div className="font-semibold text-ctx-err">New incident</div>
            <div className="text-ctx-text">
              {banner.error} in {banner.file} · trace_id: {banner.trace_id}
            </div>
            <div className="mt-1 text-[10px] text-ctx-muted">{banner.ts}</div>
          </div>
          <button
            type="button"
            onClick={() => setBanner(null)}
            className="shrink-0 text-ctx-muted hover:text-ctx-text"
          >
            dismiss
          </button>
        </div>
      )}

      <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-ctx-border pb-4">
        <div>
          <h1 className="text-sm font-semibold tracking-wide text-ctx-text">
            Live trace
          </h1>
          <p className="mt-1 text-[11px] text-ctx-muted">
            Pipeline view from polled logs + SQLite context.
          </p>
          {config.githubRepo?.trim() && (
            <p className="mt-1 text-[10px] text-ctx-accent">
              repo: {config.githubRepo.trim()}
            </p>
          )}
        </div>
        <div className="text-right text-[10px] text-ctx-muted">
          <div>polling every 30s</div>
          <div>last: {fmt(logsPoll.lastChecked)}</div>
          {logsPoll.error && (
            <div className="text-ctx-err">logs: unavailable</div>
          )}
        </div>
      </header>

      {!config.monitoringActive && (
        <div className="border border-ctx-warn bg-ctx-card p-3 text-[11px] text-ctx-warn">
          Start monitoring from Connect to enable polling.
        </div>
      )}

      <main className="mt-6 flex-1">
        {!currentLog && (
          <div className="text-[11px] text-ctx-muted">
            No log rows yet. Trigger /api/process-invoice from the demo app.
          </div>
        )}
        {steps.map((s, i) => (
          <TraceStep
            key={s.key}
            stepNumber={i + 1}
            status={s.status}
            title={s.title}
            detail={s.detail}
            isLast={i === steps.length - 1}
          >
            {s.body}
          </TraceStep>
        ))}
      </main>
    </div>
  );
}
