"""Poll GitHub for commits newer than `.last_commit` via MCP `list_commits`."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = ".last_commit"


def _parse_tool_output(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(s)
        except (SyntaxError, ValueError):
            return s


class CommitWatcher:
    """Uses GitHub MCP `list_commits` to discover new SHAs since last run."""

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        state_path: str | Path = DEFAULT_STATE_PATH,
        per_page: int = 30,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.state_path = Path(state_path)
        self.per_page = per_page

    def _read_last_seen(self) -> str | None:
        if not self.state_path.exists():
            return None
        sha = self.state_path.read_text(encoding="utf-8").strip()
        return sha or None

    def _write_last_seen(self, sha: str) -> None:
        self.state_path.write_text(sha.strip() + "\n", encoding="utf-8")

    async def _invoke_list_commits(self, list_commits_tool: Any, page: int) -> list[dict[str, Any]]:
        candidates = (
            {"owner": self.owner, "repo": self.repo, "page": page, "perPage": self.per_page},
            {"owner": self.owner, "repo": self.repo, "page": page, "per_page": self.per_page},
        )
        last_err: Exception | None = None
        for args in candidates:
            try:
                raw = await list_commits_tool.ainvoke(args)
                data = _parse_tool_output(raw)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "commits" in data:
                    return list(data["commits"])
                if isinstance(data, str):
                    continue
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        if last_err:
            raise last_err
        return []

    async def poll(self, list_commits_tool: Any) -> list[str]:
        """
        Return new commit SHAs oldest-first since last_seen (stored in state file).
        On first run, seeds state with HEAD and returns [].
        """
        _ = os.getenv("COMMIT_POLL_INTERVAL", "60")  # documented env; pipeline controls cadence

        last_seen = self._read_last_seen()
        commits: list[dict[str, Any]] = []
        for page in range(1, 15):
            page_rows = await self._invoke_list_commits(list_commits_tool, page)
            if not page_rows:
                break
            commits.extend(page_rows)
            if len(page_rows) < self.per_page:
                break

        if not commits:
            print("[ContextO] commit_watcher: list_commits returned no data")
            return []

        def _sha(c: dict[str, Any]) -> str | None:
            if "sha" in c and isinstance(c["sha"], str):
                return c["sha"]
            if "commit" in c and isinstance(c["commit"], dict):
                return c["commit"].get("sha") or c.get("sha")
            return None

        head_sha = _sha(commits[0])
        if not head_sha:
            print("[ContextO] commit_watcher: could not resolve HEAD sha")
            return []

        if last_seen is None:
            self._write_last_seen(head_sha)
            print(f"[ContextO] commit_watcher: initialized .last_commit to {head_sha[:7]}")
            return []

        if head_sha == last_seen:
            return []

        new_shas: list[str] = []
        for c in commits:
            sha = _sha(c)
            if not sha:
                continue
            if sha == last_seen:
                break
            new_shas.append(sha)

        if not new_shas and head_sha != last_seen:
            print(
                "[ContextO] commit_watcher: last_seen not in recent pages; "
                "advancing pointer to HEAD (may skip guards)"
            )

        self._write_last_seen(head_sha)
        new_shas.reverse()
        print(
            f"[ContextO] commit_watcher: {len(new_shas)} new commit(s) since {last_seen[:7]}"
        )
        return new_shas
