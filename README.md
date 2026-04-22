# ContextO

ContextO is an AI-powered SRE agent platform that correlates live application errors with GitHub source code, persists incidents in SQLite, generates regression tests, and can optionally re-run those tests when new commits touch previously failing files.

## Components

| Piece | Role |
| --- | --- |
| `app.py` | Demo Flask app that emits structured errors and serves `/api/logs`, `/api/incidents`, `/api/file-context`, and `/dashboard`. |
| `live_tools_server.py` | FastMCP stdio server: `get_live_system_logs` (HTTP poll) and `get_recent_incidents` (SQLite). |
| `live_agent.py` | LangGraph ReAct tracer exposed as `run_tracer()` for reuse by the pipeline. |
| `contexto/pipeline.py` | Long-running orchestration loop: poll logs → trace → store → generate tests → commit guard. |
| `contexto/memory/context_store.py` | Async `aiosqlite` persistence for `file_context` and `incident_log`. |

## Setup

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create a `.env` file in the project root with:

- `GOOGLE_API_KEY` — Gemini API key
- `GITHUB_OWNER`, `GITHUB_REPO` — target repository
- `GITHUB_PERSONAL_ACCESS_TOKEN` (or `GITHUB_PERSONAL_TOKEN`) — fine-scoped PAT for MCP GitHub server
- Optional: `LOG_SOURCE_URL` (default `http://127.0.0.1:5000/api/logs`), `POLL_INTERVAL`, `COMMIT_POLL_INTERVAL`, `DB_PATH`, `LLM_MODEL`

3. Start the demo app (terminal 1):

```bash
python app.py
```

4. Run the pipeline (terminal 2). By default this also starts the **React UI** (`npm run dev` in `frontend/`) when `frontend/node_modules` exists — open **http://127.0.0.1:5173**. Skip the UI with `CONTEXTO_SKIP_UI=1`.

```bash
python -m contexto.pipeline
# or: CONTEXTO_SKIP_UI=1 python -m contexto.pipeline
```

5. Trigger an error from the Foster portal in the browser (`http://127.0.0.1:5000/`), then watch the pipeline logs. Open `http://127.0.0.1:5000/dashboard` for incidents and file context.

## Standalone tracer

```bash
python live_agent.py
```

Runs a minimal `run_tracer` demo (requires MCP subprocesses and valid API keys).

## Regression tests

Generated tests are written under `tests/regression/` by `commit_guard` when a commit touches a file that has stored `generated_tests`. Shared path setup lives in `tests/conftest.py`.

## Node / MCP

The same MCP layout as before is preserved: GitHub server via `npx @modelcontextprotocol/server-github` and the local `live_tools_server.py` subprocess. Ensure `npx` and `node` are on `PATH` (the code also prepends `/opt/homebrew/bin` on macOS).

## Database

SQLite file defaults to `contexto.db` in the working directory. Incidents are deduplicated on `trace_id` at insert time.

## React frontend (`frontend/`)

Single-page **ContextO** UI (Vite + React + Tailwind): Connect, Live trace, Incidents, and Test suite. It talks to the same Flask APIs (`/api/logs`, `/api/incidents`, `/api/file-context`, `/api/session-events`, `POST /api/generate-test`). Dev server proxies `/api` to Flask so you can use relative URLs like `/api/logs` or the default absolute `http://127.0.0.1:5000/api/logs` (CORS is enabled on `/api/*` in `app.py`).

### Run alongside Flask

Terminal 1 — API + demo logs:

```bash
python app.py
```

Terminal 2 — Vite dev server (from repo root):

```bash
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. Use **Connect** to validate GitHub + log URLs, then **Start monitoring** to enable Live trace polling. Production build:

```bash
cd frontend
npm run build
```

Static output is written to `frontend/dist/` (serve with any static host, or point Vite preview at the same machine as Flask).
