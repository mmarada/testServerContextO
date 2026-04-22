"""Run stored regression tests when a commit touches files with known incidents."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import Settings
from contexto.memory.context_store import ContextStore


def _stem_from_repo_path(path: str) -> str:
    base = os.path.basename(path.strip())
    stem, _ext = os.path.splitext(base)
    return stem or "unknown"


def _parse_tool_output(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


async def _fetch_changed_files_github_api(
    settings: Settings,
    sha: str,
) -> list[str]:
    url = f"https://api.github.com/repos/{settings.github_owner}/{settings.github_repo}/commits/{sha}"
    headers = {
        "Authorization": f"Bearer {settings.github_personal_access_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ContextO-CommitGuard/1.0",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
    files = data.get("files") or []
    return [str(f.get("filename", "")) for f in files if f.get("filename")]


async def _fetch_changed_files_mcp(
    tools: list[Any],
    settings: Settings,
    sha: str,
) -> list[str] | None:
    tool = next((t for t in tools if t.name == "get_commit"), None)
    if tool is None:
        return None
    for args in (
        {"owner": settings.github_owner, "repo": settings.github_repo, "sha": sha},
        {"owner": settings.github_owner, "repo": settings.github_repo, "commit_sha": sha},
    ):
        try:
            raw = await tool.ainvoke(args)
            data = _parse_tool_output(raw)
            if isinstance(data, dict):
                files = data.get("files") or data.get("changed_files")
                if isinstance(files, list) and files and isinstance(files[0], str):
                    return files
                if files and isinstance(files[0], dict):
                    return [str(f.get("filename", "")) for f in files if f.get("filename")]
        except Exception:  # noqa: BLE001
            continue
    return None


async def run_commit_guard(
    sha: str,
    mcp_client: MultiServerMCPClient,
    context_store: ContextStore,
    settings: Settings,
    repo_root: Path | None = None,
) -> None:
    """
    For each file touched in the commit, if context_store has generated_tests,
    write tests/regression/<stem>_test.py and run pytest.
    """
    root = repo_root or Path(os.getcwd())
    regression_dir = root / "tests" / "regression"
    regression_dir.mkdir(parents=True, exist_ok=True)

    print(f"[ContextO] commit_guard: analyzing commit {sha[:7]}")

    tools = await mcp_client.get_tools()
    changed = await _fetch_changed_files_mcp(tools, settings, sha)
    if changed is None:
        changed = await _fetch_changed_files_github_api(settings, sha)

    if not changed:
        print(f"[ContextO] commit_guard: no changed files for {sha[:7]}")
        return

    for path in changed:
        if not path:
            continue
        basename = os.path.basename(path)
        ctx = await context_store.get_context_for_file(basename) or await context_store.get_context_for_file(path)
        tests = (ctx or {}).get("generated_tests") or []
        if not tests:
            continue

        stem = _stem_from_repo_path(path)
        out_file = regression_dir / f"{stem}_test.py"
        combined = "\n\n".join(str(t) for t in tests if str(t).strip())
        out_file.write_text(combined + "\n", encoding="utf-8")
        print(
            f"[ContextO] commit_guard: COMMIT {sha[:7]} touched {path} "
            f"which has known incidents. Running pytest on {out_file.name} ..."
        )

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(out_file), "-v"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        ok = proc.returncode == 0
        status = "PASSED" if ok else "FAILED"
        print(
            f"[ContextO] commit_guard: COMMIT {sha[:7]} touched {path} "
            f"which has known incidents. Tests: {status}"
        )
        if not ok:
            print(proc.stdout[-4000:] if proc.stdout else "")
            print(proc.stderr[-4000:] if proc.stderr else "")
            inc = (ctx or {}).get("last_error") or {}
            tid = inc.get("trace_id") if isinstance(inc, dict) else None
            print(
                f"[ContextO] commit_guard: failing tests in {out_file} — "
                f"link to original incident trace_id={tid!r} "
                f"(see incident_log / dashboard)"
            )
