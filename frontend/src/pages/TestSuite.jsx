import { useMemo, useState } from "react";
import { usePoller } from "../hooks/usePoller.js";
import { CodeBlock } from "../components/CodeBlock.jsx";

function stem(p) {
  const b = (p || "").split("/").pop() || "";
  return b.replace(/\.[^.]+$/, "") || "module";
}

export function TestSuite({ onToast }) {
  const ctx = usePoller("/api/file-context", 60_000);
  const rows = Array.isArray(ctx.data) ? ctx.data : [];

  const [modal, setModal] = useState(null);
  const [scenario, setScenario] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const grouped = useMemo(() => rows, [rows]);

  const submitGenerate = async () => {
    if (!modal) return;
    setSubmitting(true);
    try {
      const res = await fetch("/api/generate-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_path: modal.file_path,
          scenario,
        }),
      });
      const j = await res.json().catch(() => ({}));
      onToast?.("success", j.message || "Request recorded (stub).");
      setModal(null);
      setScenario("");
    } catch {
      onToast?.("error", "generate-test request failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="ctx-grid min-h-full p-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-ctx-border pb-4">
        <div>
          <h1 className="text-sm font-semibold tracking-wide text-ctx-text">
            Test suite
          </h1>
          <p className="mt-1 text-[11px] text-ctx-muted">
            Generated tests from /api/file-context
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            const cmd = "pytest tests/regression/ -v -m regression";
            navigator.clipboard.writeText(cmd);
            onToast?.("success", "Copied: pytest tests/regression/ -v -m regression");
          }}
          className="border border-ctx-accent px-3 py-1 text-[11px] text-ctx-accent hover:bg-[rgba(34,211,238,0.08)]"
        >
          Run all regression tests (copy)
        </button>
      </header>

      <div className="mb-4 rounded border border-ctx-border bg-ctx-card p-2 text-[10px] text-ctx-muted">
        <div className="text-ctx-text">pytest tests/regression/ -v -m regression</div>
      </div>

      {ctx.error && (
        <div className="mb-4 text-[11px] text-ctx-muted">file-context: unavailable</div>
      )}

      <div className="space-y-4">
        {grouped.length === 0 && (
          <div className="text-[11px] text-ctx-muted">No file context rows.</div>
        )}
        {grouped.map((r) => {
          const tests = Array.isArray(r.generated_tests) ? r.generated_tests : [];
          const s = stem(r.file_path);
          return (
            <article
              key={r.file_path}
              className="border border-ctx-border bg-ctx-card"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ctx-border px-3 py-2">
                <div className="text-[11px] text-ctx-text">
                  <span className="text-ctx-accent">{r.file_path}</span>
                  <span className="mx-2 text-ctx-muted">|</span>
                  <span>{r.function_name || "—"}</span>
                  <span className="mx-2 rounded border border-ctx-border px-1 text-[10px] text-ctx-warn">
                    errors {r.error_count ?? 0}
                  </span>
                  <span className="text-ctx-muted">{tests.length} tests</span>
                </div>
              </div>
              <div className="space-y-3 p-3">
                {tests.length === 0 && (
                  <div className="text-[10px] text-ctx-muted">No tests stored.</div>
                )}
                {tests.map((code, i) => (
                  <div key={i} className="space-y-2">
                    <CodeBlock code={code} language="python" />
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="border border-ctx-border px-2 py-1 text-[10px] text-ctx-accent hover:bg-ctx-bg"
                        onClick={() => {
                          const cmd = `pytest tests/regression/${s}_test.py -v`;
                          navigator.clipboard.writeText(cmd);
                          onToast?.("success", "Copied pytest command.");
                        }}
                      >
                        Run (copy)
                      </button>
                      <button
                        type="button"
                        className="border border-ctx-border px-2 py-1 text-[10px] text-ctx-accent hover:bg-ctx-bg"
                        onClick={() => {
                          const cmd = `pytest tests/regression/${s}_test.py -v -m regression`;
                          navigator.clipboard.writeText(cmd);
                          onToast?.("success", "Copied run-all-for-file command.");
                        }}
                      >
                        Run all for this file (copy)
                      </button>
                    </div>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => setModal({ file_path: r.file_path })}
                  className="mt-2 w-full border border-ctx-border py-1.5 text-[10px] text-ctx-muted hover:border-ctx-accent hover:text-ctx-accent"
                >
                  Generate more tests
                </button>
              </div>
            </article>
          );
        })}
      </div>

      {modal && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-md border border-ctx-border bg-ctx-card p-4">
            <div className="text-[11px] font-semibold text-ctx-text">
              Describe scenario
            </div>
            <div className="mt-1 text-[10px] text-ctx-muted">{modal.file_path}</div>
            <textarea
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              rows={5}
              className="mt-3 w-full border border-ctx-border bg-ctx-bg p-2 text-[11px] text-ctx-text outline-none focus:border-ctx-accent"
              placeholder="e.g. invoice with null tax profile…"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setModal(null);
                  setScenario("");
                }}
                className="border border-ctx-border px-3 py-1 text-[10px] text-ctx-muted"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={submitGenerate}
                className="border border-ctx-accent px-3 py-1 text-[10px] text-ctx-accent disabled:opacity-50"
              >
                {submitting ? "…" : "Submit to /api/generate-test"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
