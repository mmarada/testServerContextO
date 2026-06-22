import { useMemo } from "react";
import { usePoller } from "../hooks/usePoller.js";

function errorType(root) {
  if (!root) return "";
  return String(root).split(":")[0].trim() || "—";
}

const SEV_STYLES = {
  HIGH: "border border-red-500/60 text-red-400 bg-red-500/10",
  MEDIUM: "border border-yellow-500/60 text-yellow-400 bg-yellow-500/10",
  LOW: "border border-green-500/60 text-green-400 bg-green-500/10",
};

function SeverityBadge({ severity }) {
  const s = (severity || "LOW").toUpperCase();
  return (
    <span className={`inline-block px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${SEV_STYLES[s] || SEV_STYLES.LOW}`}>
      {s}
    </span>
  );
}

function signatureKey(row) {
  return `${row.file_path}|${row.line_number}|${errorType(row.root_cause)}`;
}

export function Incidents({ onViewTrace }) {
  const inc = usePoller("/api/incidents", 60_000);
  const ctx = usePoller("/api/file-context", 60_000);

  const rows = Array.isArray(inc.data) ? inc.data : [];
  const fctx = Array.isArray(ctx.data) ? ctx.data : [];

  const metrics = useMemo(() => {
    const sig = new Set(rows.map(signatureKey));
    const testCount = fctx.reduce((acc, r) => {
      const n = Array.isArray(r.generated_tests) ? r.generated_tests.length : 0;
      return acc + n;
    }, 0);
    return {
      total: rows.length,
      signatures: sig.size,
      tests: testCount,
    };
  }, [rows, fctx]);

  const lastCommit = useMemo(() => {
    const withSha = rows.filter((r) => r.commit_sha);
    if (!withSha.length) return null;
    const sorted = [...withSha].sort(
      (a, b) =>
        new Date(b.created_at || 0) - new Date(a.created_at || 0)
    );
    return sorted[0]?.commit_sha || null;
  }, [rows]);

  const guardRows = useMemo(() => {
    if (!lastCommit) return [];
    return rows.filter((r) => r.commit_sha === lastCommit);
  }, [rows, lastCommit]);

  const fmt = (d) =>
    d ? d.toLocaleTimeString(undefined, { hour12: false }) : "—";

  return (
    <div className="ctx-grid min-h-full p-6">
      <header className="mb-6 border-b border-ctx-border pb-4">
        <h1 className="text-sm font-semibold tracking-wide text-ctx-text">
          Incidents
        </h1>
        <p className="mt-1 text-[11px] text-ctx-muted">
          From GET /api/incidents · refresh 60s · last {fmt(inc.lastChecked)}
        </p>
      </header>

      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[
          ["Total incidents", metrics.total],
          ["Unique bug signatures", metrics.signatures],
          ["Tests generated", metrics.tests],
        ].map(([label, val]) => (
          <div
            key={label}
            className="border border-ctx-border bg-ctx-card px-3 py-3"
          >
            <div className="text-[10px] uppercase tracking-wide text-ctx-muted">
              {label}
            </div>
            <div className="mt-1 text-xl text-ctx-accent">{val}</div>
          </div>
        ))}
      </div>

      {inc.error && (
        <div className="mb-4 text-[11px] text-ctx-muted">incidents: unavailable</div>
      )}

      <div className="overflow-x-auto border border-ctx-border">
        <table className="w-full border-collapse text-left text-[11px]">
          <thead>
            <tr className="border-b border-ctx-border bg-ctx-card text-[10px] uppercase tracking-wide text-ctx-muted">
              <th className="p-2">trace</th>
              <th className="p-2">severity</th>
              <th className="p-2">file:line</th>
              <th className="p-2">error type</th>
              <th className="p-2">hits</th>
              <th className="p-2">timestamp</th>
              <th className="p-2">action</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="p-4 text-ctx-muted">
                  No incidents.
                </td>
              </tr>
            )}
            {rows.map((r) => {
              const hits =
                fctx.find((c) => c.file_path === r.file_path)?.error_count ?? "—";
              return (
                <tr key={r.trace_id} className="border-b border-ctx-border">
                  <td className="p-2 font-mono text-ctx-accent">
                    {(r.trace_id || "").slice(0, 8)}
                  </td>
                  <td className="p-2">
                    <SeverityBadge severity={r.severity} />
                  </td>
                  <td className="p-2 text-ctx-text">
                    {r.file_path}:{r.line_number}
                  </td>
                  <td className="p-2 text-ctx-muted">{errorType(r.root_cause)}</td>
                  <td className="p-2 text-ctx-text">{hits}</td>
                  <td className="p-2 text-ctx-muted">{r.created_at || "—"}</td>
                  <td className="p-2">
                    <button
                      type="button"
                      onClick={() => onViewTrace(r.trace_id)}
                      className="border border-ctx-border px-2 py-0.5 text-[10px] text-ctx-accent hover:bg-ctx-bg"
                    >
                      View trace
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <section className="mt-8 border border-ctx-border bg-ctx-card p-4">
        <h2 className="text-[11px] font-semibold uppercase tracking-wide text-ctx-muted">
          Commit guard
        </h2>
        <p className="mt-2 text-[10px] text-ctx-muted">
          Rows where <span className="text-ctx-text">commit_sha</span> matches the
          most recent non-null commit on an incident (
          <span className="text-ctx-accent">{lastCommit?.slice(0, 7) || "—"}</span>
          ). Pass/fail requires a pipeline API — shown as audit list only.
        </p>
        <ul className="mt-3 space-y-2 text-[10px] text-ctx-text">
          {guardRows.length === 0 && (
            <li className="text-ctx-muted">No incidents tagged with a commit_sha.</li>
          )}
          {guardRows.map((r) => (
            <li key={r.incident_id} className="border-l-2 border-ctx-border pl-2">
              <span className="text-ctx-accent">{r.file_path}</span> · trace{" "}
              {(r.trace_id || "").slice(0, 8)} ·{" "}
              <span className="text-ctx-warn">guard status: n/a (API)</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
