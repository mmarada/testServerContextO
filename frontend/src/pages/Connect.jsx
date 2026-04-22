import { useCallback, useState } from "react";

export const defaultConfig = {
  githubRepo: "",
  githubToken: "",
  logSourceUrl: "http://127.0.0.1:5000/api/logs",
  sessionEventsUrl: "",
  externalMonitorUrl: "",
  storageType: "local",
  storageDsn: "",
  monitoringActive: false,
};

function dotClass(s) {
  if (s === "ok") return "bg-ctx-ok";
  if (s === "error") return "bg-ctx-err";
  return "bg-ctx-muted";
}

function parseGithubRepo(input) {
  const raw = (input || "").trim().replace(/^https?:\/\//, "").replace(/^www\./, "");
  if (raw.includes("github.com/")) {
    const tail = raw.split("github.com/")[1]?.split(/[/?#]/)[0];
    const [o, r] = (tail || "").split("/");
    if (o && r) return `${o}/${r}`;
  }
  const parts = raw.split(/[/?#]/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0]}/${parts[1]}`;
  return null;
}

export function Connect({ config, setConfig, onStartMonitoring }) {
  const [status, setStatus] = useState(() => ({
    githubRepo: "idle",
    githubToken: "idle",
    logSourceUrl: "idle",
    sessionEventsUrl: "idle",
    externalMonitorUrl: "idle",
    storageDsn: "idle",
  }));
  const [showToken, setShowToken] = useState(false);
  const [busy, setBusy] = useState(null);

  const patch = useCallback(
    (k, v) => {
      setConfig((c) => ({ ...c, [k]: v }));
      setStatus((s) => ({ ...s, [k]: "idle" }));
    },
    [setConfig]
  );

  const testLogs = async () => {
    setBusy("logs");
    try {
      const u = config.logSourceUrl || "/api/logs";
      const res = await fetch(u);
      if (!res.ok) throw new Error(String(res.status));
      setStatus((s) => ({ ...s, logSourceUrl: "ok" }));
    } catch {
      setStatus((s) => ({ ...s, logSourceUrl: "error" }));
    } finally {
      setBusy(null);
    }
  };

  const testSession = async () => {
    if (!config.sessionEventsUrl?.trim()) {
      setStatus((s) => ({ ...s, sessionEventsUrl: "idle" }));
      return;
    }
    setBusy("session");
    try {
      const res = await fetch(config.sessionEventsUrl);
      if (res.status === 404) throw new Error("404");
      if (!res.ok) throw new Error(String(res.status));
      await res.json();
      setStatus((s) => ({ ...s, sessionEventsUrl: "ok" }));
    } catch {
      setStatus((s) => ({ ...s, sessionEventsUrl: "error" }));
    } finally {
      setBusy(null);
    }
  };

  const testGithub = async () => {
    setBusy("github");
    try {
      const slug = parseGithubRepo(config.githubRepo);
      if (!slug || !config.githubToken?.trim()) throw new Error("missing");
      const res = await fetch(`https://api.github.com/repos/${slug}`, {
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${config.githubToken.trim()}`,
        },
      });
      if (!res.ok) throw new Error(String(res.status));
      setStatus((s) => ({
        ...s,
        githubRepo: "ok",
        githubToken: "ok",
      }));
    } catch {
      setStatus((s) => ({
        ...s,
        githubRepo: "error",
        githubToken: "error",
      }));
    } finally {
      setBusy(null);
    }
  };

  const testExternal = async () => {
    if (!config.externalMonitorUrl?.trim()) {
      setStatus((s) => ({ ...s, externalMonitorUrl: "idle" }));
      return;
    }
    setBusy("ext");
    try {
      const res = await fetch(config.externalMonitorUrl, { method: "HEAD" }).catch(
        () => null
      );
      if (res && res.ok) {
        setStatus((s) => ({ ...s, externalMonitorUrl: "ok" }));
        return;
      }
      const get = await fetch(config.externalMonitorUrl);
      if (!get.ok) throw new Error();
      setStatus((s) => ({ ...s, externalMonitorUrl: "ok" }));
    } catch {
      setStatus((s) => ({ ...s, externalMonitorUrl: "error" }));
    } finally {
      setBusy(null);
    }
  };

  const field = (label, key, opts = {}) => (
    <label className="block">
      <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-ctx-muted">
        <span className={`h-2 w-2 rounded-full ${dotClass(status[key])}`} />
        {label}
      </div>
      {opts.type === "password" ? (
        <div className="flex gap-2">
          <input
            type={showToken ? "text" : "password"}
            value={config[key] || ""}
            onChange={(e) => patch(key, e.target.value)}
            className="w-full border border-ctx-border bg-ctx-bg px-2 py-1.5 text-[11px] text-ctx-text outline-none focus:border-ctx-accent"
            autoComplete="off"
          />
          <button
            type="button"
            onClick={() => setShowToken((v) => !v)}
            className="shrink-0 border border-ctx-border px-2 text-[10px] text-ctx-muted hover:border-ctx-accent hover:text-ctx-accent"
          >
            {showToken ? "hide" : "show"}
          </button>
        </div>
      ) : (
        <input
          type="text"
          value={config[key] || ""}
          onChange={(e) => patch(key, e.target.value)}
          className="w-full border border-ctx-border bg-ctx-bg px-2 py-1.5 text-[11px] text-ctx-text outline-none focus:border-ctx-accent"
          placeholder={opts.placeholder}
        />
      )}
    </label>
  );

  return (
    <div className="ctx-grid min-h-full p-6">
      <header className="mb-6 border-b border-ctx-border pb-4">
        <h1 className="text-sm font-semibold tracking-wide text-ctx-text">
          Connect
        </h1>
        <p className="mt-1 text-[11px] text-ctx-muted">
          Wire GitHub + log sources. State stays in this session only.
        </p>
      </header>

      <div className="mx-auto max-w-xl space-y-4">
        {field("GitHub repo URL", "githubRepo", {
          placeholder: "github.com/owner/repo",
        })}
        {field("GitHub personal access token", "githubToken", { type: "password" })}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy === "github"}
            onClick={testGithub}
            className="border border-ctx-border px-3 py-1 text-[11px] text-ctx-accent hover:bg-ctx-card disabled:opacity-50"
          >
            {busy === "github" ? "…" : "Test connection"}
          </button>
        </div>

        {field("Production logs URL", "logSourceUrl", {
          placeholder: "/api/logs",
        })}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy === "logs"}
            onClick={testLogs}
            className="border border-ctx-border px-3 py-1 text-[11px] text-ctx-accent hover:bg-ctx-card disabled:opacity-50"
          >
            {busy === "logs" ? "…" : "Test connection"}
          </button>
        </div>

        {field("Session events URL (optional)", "sessionEventsUrl", {
          placeholder: "/api/session-events",
        })}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy === "session"}
            onClick={testSession}
            className="border border-ctx-border px-3 py-1 text-[11px] text-ctx-accent hover:bg-ctx-card disabled:opacity-50"
          >
            {busy === "session" ? "…" : "Test connection"}
          </button>
        </div>

        {field("External monitor URL (optional)", "externalMonitorUrl", {
          placeholder: "datadog / cloudwatch / gcp URL",
        })}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy === "ext"}
            onClick={testExternal}
            className="border border-ctx-border px-3 py-1 text-[11px] text-ctx-accent hover:bg-ctx-card disabled:opacity-50"
          >
            {busy === "ext" ? "…" : "Test connection"}
          </button>
        </div>

        <div>
          <div className="mb-2 text-[10px] uppercase tracking-wide text-ctx-muted">
            Storage
          </div>
          <div className="flex gap-4 text-[11px]">
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="radio"
                name="st"
                checked={config.storageType === "local"}
                onChange={() => patch("storageType", "local")}
              />
              Local SQLite (default)
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="radio"
                name="st"
                checked={config.storageType === "custom"}
                onChange={() => patch("storageType", "custom")}
              />
              Custom DSN
            </label>
          </div>
          {config.storageType === "custom" && (
            <input
              type="text"
              value={config.storageDsn || ""}
              onChange={(e) => patch("storageDsn", e.target.value)}
              placeholder="postgres://… or s3://…"
              className="mt-2 w-full border border-ctx-border bg-ctx-bg px-2 py-1.5 text-[11px] outline-none focus:border-ctx-accent"
            />
          )}
        </div>

        <div className="border-t border-ctx-border pt-6">
          <button
            type="button"
            onClick={() => {
              setConfig((c) => ({ ...c, monitoringActive: true }));
              onStartMonitoring();
            }}
            className="w-full border border-ctx-accent py-2 text-[12px] text-ctx-accent hover:bg-[rgba(34,211,238,0.08)]"
          >
            Start monitoring
          </button>
          <p className="mt-2 text-center text-[10px] text-ctx-muted">
            Opens Live Trace · polling uses URLs above
          </p>
        </div>
      </div>
    </div>
  );
}
