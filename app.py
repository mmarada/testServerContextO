import asyncio
import datetime
import os
import traceback
import uuid

import archive_service
import billing_logic
import notification_service
import pricing_engine
import report_engine
import user_service
from flask import Flask, jsonify, render_template_string, request

from contexto.memory.context_store import (
    ContextStore,
    read_all_file_contexts_sync,
    read_recent_incidents_sync,
)

app = Flask(__name__)


@app.after_request
def _cors_api(resp):
    if request.path.startswith("/api"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _db_path() -> str:
    try:
        from config import get_settings

        return get_settings().db_path
    except Exception:  # noqa: BLE001
        return os.getenv("DB_PATH", "contexto.db")


def _ensure_schema() -> None:
    async def _go() -> None:
        store = ContextStore(_db_path())
        await store.init()

    asyncio.run(_go())


_ensure_schema()

# This simulates our persistent log database
logs_db = []


def _log_scenario_error(event: str, fn, expected_file: str):
    """Run fn(), capture real traceback in this repo, append to logs_db, return JSON error."""
    trace_id = str(uuid.uuid4())[:8]
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        tb = traceback.extract_tb(e.__traceback__)
        frame = None
        for fr in reversed(tb):
            if fr.filename.endswith(expected_file):
                frame = fr
                break
        if frame is None:
            frame = tb[-1]
        err = "".join(traceback.format_exception_only(type(e), e)).strip()
        logs_db.append(
            {
                "timestamp": datetime.datetime.now().isoformat(),
                "trace_id": trace_id,
                "event": event,
                "error": err,
                "file": os.path.basename(frame.filename),
                "line": frame.lineno,
                "function": frame.name,
            }
        )
        return jsonify({"status": "error", "trace_id": trace_id}), 500
    return jsonify({"status": "ok", "trace_id": trace_id}), 200


@app.route("/")
def index():
    return render_template_string(
        """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Acme Business Portal</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, sans-serif;
      background: #0d0d0d;
      color: #d4d4d8;
      min-height: 100vh;
    }
    header {
      border-bottom: 1px solid #27272a;
      padding: 1.25rem 1.5rem;
      background: #141414;
    }
    header h1 { margin: 0; font-size: 1.25rem; font-weight: 600; color: #fafafa; }
    header p { margin: 0.35rem 0 0; font-size: 0.8rem; color: #71717a; }
    main { padding: 1.5rem; max-width: 1100px; margin: 0 auto; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1rem;
    }
    .card {
      border: 1px solid #27272a;
      background: #141414;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .card h2 { margin: 0; font-size: 0.95rem; color: #fafafa; }
    .card p.desc { margin: 0; font-size: 0.75rem; color: #71717a; line-height: 1.4; flex: 1; }
    button.action {
      border: 1px solid #3f3f46;
      background: #18181b;
      color: #d4d4d8;
      padding: 0.5rem 0.75rem;
      font-size: 0.8rem;
      cursor: pointer;
      text-align: left;
    }
    button.action:hover:not(:disabled) { border-color: #52525b; color: #fafafa; }
    button.action:disabled { opacity: 0.5; cursor: wait; }
    .out { font-family: ui-monospace, monospace; font-size: 0.7rem; color: #a1a1aa; min-height: 2.5rem; word-break: break-all; }
    .out.err { color: #f87171; }
    .out.ok { color: #4ade80; }
  </style>
</head>
<body>
  <header>
    <h1>Acme Business Portal</h1>
    <p>Demo flows — each action logs a production-style exception for ContextO tracing.</p>
  </header>
  <main>
    <div class="grid" id="grid"></div>
  </main>
  <script>
    const cards = [
      { id: 'c1', title: 'Invoice processing', desc: 'Quarterly invoice tax calculation for configured customers.', url: '/api/process-invoice?id=8821' },
      { id: 'c2', title: 'User profile load', desc: 'Fetch profile fields for support and billing (legacy email key).', url: '/api/load-profile?user_id=99' },
      { id: 'c3', title: 'Export report', desc: 'Build revenue export for finance (XLSX / CSV).', url: '/api/export-report?format=xlsx' },
      { id: 'c4', title: 'Send notification', desc: 'Dispatch outbound message on email or Slack channel.', url: '/api/send-notification?channel=email' },
      { id: 'c5', title: 'Apply discount', desc: 'Apply promotional code at checkout.', url: '/api/apply-discount?code=SAVE50' },
      { id: 'c6', title: 'Archive records', desc: 'Soft-delete records before a cutoff date.', url: '/api/archive-records?before=2024-01-01' },
    ];
    const grid = document.getElementById('grid');
    cards.forEach(cfg => {
      const el = document.createElement('div');
      el.className = 'card';
      el.id = cfg.id;
      el.innerHTML = '<h2>' + cfg.title + '</h2><p class="desc">' + cfg.desc + '</p>' +
        '<button class="action" type="button">Run</button><div class="out"></div>';
      grid.appendChild(el);
      const btn = el.querySelector('button');
      const out = el.querySelector('.out');
      btn.addEventListener('click', () => {
        btn.disabled = true;
        out.textContent = 'Loading…';
        out.className = 'out';
        fetch(cfg.url).then(r => r.json().then(j => ({ ok: r.ok, j }))).then(({ ok, j }) => {
          btn.disabled = false;
          if (j && j.trace_id) {
            out.className = 'out ' + (ok ? 'ok' : 'err');
            out.textContent = (ok ? 'OK ' : 'Error ') + 'trace_id: ' + j.trace_id;
          } else {
            out.className = 'out err';
            out.textContent = 'Unexpected response';
          }
        }).catch(e => {
          btn.disabled = false;
          out.className = 'out err';
          out.textContent = String(e);
        });
      });
    });
  </script>
</body>
</html>
        """
    )


@app.route("/api/process-invoice")
def process_invoice():
    return _log_scenario_error(
        "INVOICE_PROCESS_CLICK",
        lambda: billing_logic.process_invoice(str(request.args.get("id", "8821"))),
        "billing_logic.py",
    )


@app.route("/api/load-profile")
def load_profile():
    return _log_scenario_error(
        "PROFILE_LOAD_CLICK",
        lambda: user_service.load_user_profile(request.args.get("user_id", "99")),
        "user_service.py",
    )


@app.route("/api/export-report")
def export_report():
    return _log_scenario_error(
        "EXPORT_REPORT_CLICK",
        lambda: report_engine.generate_export(request.args.get("format", "xlsx")),
        "report_engine.py",
    )


@app.route("/api/send-notification")
def send_notification_route():
    return _log_scenario_error(
        "SEND_NOTIFICATION_CLICK",
        lambda: notification_service.send_notification(
            request.args.get("channel", "email")
        ),
        "notification_service.py",
    )


@app.route("/api/apply-discount")
def apply_discount_route():
    return _log_scenario_error(
        "APPLY_DISCOUNT_CLICK",
        lambda: pricing_engine.apply_discount(request.args.get("code", "SAVE50")),
        "pricing_engine.py",
    )


@app.route("/api/archive-records")
def archive_records_route():
    return _log_scenario_error(
        "ARCHIVE_RECORDS_CLICK",
        lambda: archive_service.archive_records(
            request.args.get("before", "2024-01-01")
        ),
        "archive_service.py",
    )

@app.route('/api/logs')
def get_logs():
    return jsonify(logs_db)


@app.route("/api/incidents")
def get_incidents():
    rows = read_recent_incidents_sync(_db_path(), limit=50)
    return jsonify(rows)


@app.route("/api/file-context")
def get_file_context():
    rows = read_all_file_contexts_sync(_db_path())
    return jsonify(rows)


@app.route("/api/session-events")
def session_events():
    """Optional session stream; empty until wired to a real source."""
    return jsonify([])


@app.route("/api/generate-test", methods=["POST", "OPTIONS"])
def generate_test():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    return jsonify(
        {
            "ok": True,
            "message": "Stub: connect pipeline / LLM to honor this request.",
            "file_path": body.get("file_path"),
            "scenario": (body.get("scenario") or "")[:2000],
        }
    )


@app.route("/dashboard")
def dashboard():
    return render_template_string(
        """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>ContextO Dashboard</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #58a6ff;
    }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 1.25rem 1.5rem;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }
    h1 { margin: 0; font-size: 1.25rem; font-weight: 600; }
    p.sub { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.9rem; }
    main { padding: 1.25rem 1.5rem 2rem; }
    h2 { font-size: 1rem; margin: 1.5rem 0 0.75rem; color: var(--muted); font-weight: 600; }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }
    th, td {
      text-align: left;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
      font-size: 0.85rem;
    }
    th { color: var(--muted); font-weight: 600; background: #131820; }
    tr:last-child td { border-bottom: none; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 0.8rem; }
    .pill {
      display: inline-block;
      padding: 0.1rem 0.45rem;
      border-radius: 999px;
      background: #21262d;
      color: var(--accent);
      font-size: 0.75rem;
    }
  </style>
</head>
<body>
  <header>
    <h1>ContextO <span class="pill">refresh 15s (AJAX)</span></h1>
    <p class="sub">Recent incidents and per-file memory from SQLite</p>
  </header>
  <main>
    <h2>Recent incidents</h2>
    <table>
      <thead>
        <tr>
          <th>trace_id</th>
          <th>file</th>
          <th>line</th>
          <th>root_cause</th>
          <th>timestamp</th>
        </tr>
      </thead>
      <tbody id="incidents-body"></tbody>
    </table>

    <h2>File context</h2>
    <table>
      <thead>
        <tr>
          <th>file</th>
          <th>errors</th>
          <th>tests</th>
          <th>last_seen</th>
        </tr>
      </thead>
      <tbody id="context-body"></tbody>
    </table>
  </main>
  <script>
    async function load() {
      const [inc, ctx] = await Promise.all([
        fetch('/api/incidents').then(r => r.json()),
        fetch('/api/file-context').then(r => r.json()),
      ]);
      const ib = document.getElementById('incidents-body');
      ib.innerHTML = (inc || []).map(r => `
        <tr>
          <td class="mono">${(r.trace_id || '')}</td>
          <td class="mono">${(r.file_path || '')}</td>
          <td class="mono">${r.line_number ?? ''}</td>
          <td class="mono">${(r.root_cause || '').substring(0, 400)}</td>
          <td class="mono">${(r.created_at || '')}</td>
        </tr>`).join('') || '<tr><td colspan="5">No incidents yet</td></tr>';

      const cb = document.getElementById('context-body');
      cb.innerHTML = (ctx || []).map(r => {
        const n = Array.isArray(r.generated_tests) ? r.generated_tests.length : 0;
        return `<tr>
          <td class="mono">${(r.file_path || '')}</td>
          <td class="mono">${r.error_count ?? 0}</td>
          <td class="mono">${n}</td>
          <td class="mono">${(r.updated_at || '')}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="4">No file context yet</td></tr>';
    }
    load();
    setInterval(load, 15000);
  </script>
</body>
</html>
        """
    )


if __name__ == '__main__':
    # Using 127.0.0.1 explicitly and disabling the reloader can help stability
    app.run(host='127.0.0.1', port=5000, debug=False)