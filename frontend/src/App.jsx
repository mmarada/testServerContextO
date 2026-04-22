import { useCallback, useState } from "react";
import { AlertToast } from "./components/AlertToast.jsx";
import { Connect, defaultConfig } from "./pages/Connect.jsx";
import { Incidents } from "./pages/Incidents.jsx";
import { LiveTrace } from "./pages/LiveTrace.jsx";
import { TestSuite } from "./pages/TestSuite.jsx";

const NAV = [
  { id: "connect", label: "Connect" },
  { id: "live", label: "Live trace" },
  { id: "incidents", label: "Incidents" },
  { id: "tests", label: "Test suite" },
];

export default function App() {
  const [page, setPage] = useState("connect"); // connect | live | incidents | tests
  const [config, setConfig] = useState(() => ({ ...defaultConfig }));
  const [focusTraceId, setFocusTraceId] = useState(null);
  const [toasts, setToasts] = useState([]);

  const pushToast = useCallback((tone, message) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setToasts((t) => [...t, { id, tone, message }].slice(-3));
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const onViewTrace = useCallback((traceId) => {
    setFocusTraceId(traceId);
    setPage("live");
  }, []);

  const clearFocusTrace = useCallback(() => setFocusTraceId(null), []);

  return (
    <div className="flex h-full min-h-0 bg-ctx-bg text-[12px]">
      <aside className="flex w-[200px] shrink-0 flex-col border-r border-ctx-border bg-ctx-card">
        <div className="border-b border-ctx-border px-3 py-4 font-mono text-[13px] tracking-wide">
          <span className="text-ctx-text">CONTEXT</span>
          <span className="text-ctx-accent">O</span>
        </div>
        <nav className="flex flex-col gap-1 p-2">
          {NAV.map((item) => {
            const active = page === item.id;
            const pulse =
              item.id === "live" && config.monitoringActive
                ? "bg-ctx-ok animate-pulse-dot"
                : "bg-ctx-muted";
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setPage(item.id)}
                className={`flex items-center gap-2 border px-2 py-2 text-left text-[11px] ${
                  active
                    ? "border-ctx-accent text-ctx-accent"
                    : "border-transparent text-ctx-muted hover:border-ctx-border hover:text-ctx-text"
                }`}
              >
                <span className={`h-2 w-2 shrink-0 rounded-full ${pulse}`} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="mt-auto border-t border-ctx-border p-2 text-[9px] leading-snug text-ctx-muted">
          Repo config is in-memory only. API base: same origin (Vite proxy → Flask).
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-auto">
        {page === "connect" && (
          <Connect
            config={config}
            setConfig={setConfig}
            onStartMonitoring={() => setPage("live")}
          />
        )}
        {page === "live" && (
          <LiveTrace
            config={config}
            focusTraceId={focusTraceId}
            clearFocusTrace={clearFocusTrace}
            onNewIncident={(msg) => pushToast("error", msg)}
            onTestGenerated={(msg) => pushToast("success", msg)}
          />
        )}
        {page === "incidents" && <Incidents onViewTrace={onViewTrace} />}
        {page === "tests" && <TestSuite onToast={pushToast} />}
      </main>

      <AlertToast toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
