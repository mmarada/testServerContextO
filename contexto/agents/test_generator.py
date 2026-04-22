"""Generate regression pytest code after a trace map is produced (LangGraph)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, TypedDict

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from config import Settings
from contexto.memory.context_store import ContextStore

DUPLICATE_TEST_SENTINEL = "DUPLICATE - NO NEW TEST NEEDED"


def _error_type_from_event(error: dict[str, Any]) -> str:
    msg = str(error.get("error", "")).strip()
    if not msg:
        return ""
    return msg.split(":", 1)[0].strip()


def _existing_test_covers_signature(
    test_code: str,
    *,
    function: str,
    file_path: str,
    error_type: str,
) -> bool:
    """Heuristic: same exception family and same target function/module as stored tests."""
    if not error_type:
        return False
    et = re.escape(error_type)
    has_error = error_type in test_code or re.search(
        rf"pytest\.raises\(\s*{et}\s*\)", test_code
    )
    if not has_error:
        return False

    fn = (function or "").strip()
    stem = Path(file_path).stem
    if fn and fn != "unknown":
        return re.search(rf"\b{re.escape(fn)}\b", test_code) is not None
    return stem in test_code and "@pytest.mark.regression" in test_code


def _parse_tool_output(raw: Any) -> Any:
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


def _extract_python_code(text: str) -> str:
    fence = re.search(r"```(?:python)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text.strip()


class _GeneratedTest(BaseModel):
    code: str = Field(description="Complete pytest module or single test function as valid Python")


class _GenState(TypedDict, total=False):
    file_path: str
    trace_map: dict[str, Any]
    error_event: dict[str, Any]
    source: str
    existing_tests: list[str]
    code: str


async def _github_get_file(
    tools: list[Any],
    settings: Settings,
    path: str,
) -> str:
    tool = next((t for t in tools if t.name == "get_file_contents"), None)
    if tool is None:
        raise RuntimeError("get_file_contents tool not found on GitHub MCP server")

    arg_sets = (
        {"owner": settings.github_owner, "repo": settings.github_repo, "path": path},
        {
            "owner": settings.github_owner,
            "repo": settings.github_repo,
            "path": path,
            "branch": "main",
        },
    )
    last: Exception | None = None
    for args in arg_sets:
        try:
            raw = await tool.ainvoke(args)
            data = _parse_tool_output(raw)
            if isinstance(data, dict) and "content" in data:
                return str(data["content"])
            if isinstance(data, str):
                return data
        except Exception as e:  # noqa: BLE001
            last = e
            continue
    if last:
        raise last
    return ""


async def run_test_generator(
    trace_map: dict[str, Any],
    mcp_client: MultiServerMCPClient,
    llm: ChatGoogleGenerativeAI,
    context_store: ContextStore,
    settings: Settings,
    error_event: dict[str, Any],
) -> str:
    """
    LangGraph flow: fetch GitHub source + prior tests → LLM draft → persist.
    """
    file_path = str(trace_map.get("file_path") or trace_map.get("file", ""))
    if not file_path:
        raise ValueError("trace_map missing file_path")

    error_type = _error_type_from_event(error_event)
    fn = str(trace_map.get("function", "unknown"))
    existing_guard = await context_store.get_context_for_file(file_path)
    prior_tests: list[str] = []
    if existing_guard and isinstance(existing_guard.get("generated_tests"), list):
        prior_tests = existing_guard["generated_tests"]
    for t in prior_tests:
        if not isinstance(t, str) or not t.strip():
            continue
        if _existing_test_covers_signature(
            t, function=fn, file_path=file_path, error_type=error_type
        ):
            print(
                "[ContextO] test_generator: duplicate guard (existing test covers "
                "this function + error type); skipping LLM"
            )
            return DUPLICATE_TEST_SENTINEL

    print(f"[ContextO] test_generator: running LangGraph for {file_path}")

    tools = await mcp_client.get_tools()

    async def node_fetch(state: _GenState) -> dict[str, Any]:
        fp = state["file_path"]
        print(f"[ContextO] test_generator(fetch): reading {fp} from GitHub")
        source = await _github_get_file(tools, settings, fp)
        existing = await context_store.get_context_for_file(fp)
        existing_tests: list[str] = []
        if existing and isinstance(existing.get("generated_tests"), list):
            existing_tests = existing["generated_tests"]
        return {"source": source, "existing_tests": existing_tests}

    async def node_draft(state: _GenState) -> dict[str, Any]:
        incident_id = str(state["trace_map"].get("incident_id", ""))
        trace_id = str(state["trace_map"].get("trace_id", ""))
        fn = str(state["trace_map"].get("function", "unknown"))
        source = state.get("source", "")
        existing_tests = state.get("existing_tests") or []

        structured = llm.with_structured_output(_GeneratedTest)
        prompt = SystemMessage(
            content=(
                "You write minimal, runnable pytest. Output structured code only.\n"
                "Requirements:\n"
                "- Decorate the test with @pytest.mark.regression\n"
                "- Docstring must reference incident_id and trace_id exactly as provided\n"
                "- Import the module under test using the repository file path (typically the stem)\n"
                "- Call the failing function with arguments that reproduce the crash scenario; "
                "use pytest.raises only if the correct behavior is an expected exception; "
                "otherwise assert the function completes without raising\n"
                "- The test must be syntactically valid and pass if the bug is fixed\n"
                "- Prefer a single test function named test_regression_<short_suffix>\n"
            )
        )
        human = HumanMessage(
            content=(
                f"incident_id={incident_id}\n"
                f"trace_id={trace_id}\n"
                f"function_guess={fn}\n"
                f"file_path={state['file_path']}\n"
                f"line_number={state['trace_map'].get('line_number')}\n"
                f"root_cause={state['trace_map'].get('root_cause')}\n"
                f"error_event_json={json.dumps(state['error_event'], default=str)}\n\n"
                f"EXISTING_GENERATED_TESTS (avoid exact duplicates):\n"
                f"{json.dumps(existing_tests, indent=2)[:8000]}\n\n"
                f"SOURCE_FILE:\n```python\n{source[:12000]}\n```\n"
            )
        )
        out = await structured.ainvoke([prompt, human])
        code = _extract_python_code(out.code)
        if not code.strip():
            raise RuntimeError("LLM returned empty test code")
        return {"code": code}

    graph = StateGraph(_GenState)
    graph.add_node("fetch", node_fetch)
    graph.add_node("draft", node_draft)
    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "draft")
    graph.add_edge("draft", END)
    app = graph.compile()

    final = await app.ainvoke(
        {
            "file_path": file_path,
            "trace_map": trace_map,
            "error_event": error_event,
        }
    )
    code = str(final.get("code", "")).strip()
    ast.parse(code)

    await context_store.upsert_file_context(
        file_path,
        str(trace_map.get("function", "unknown")),
        error_event,
        [code],
        bump_error_count=False,
    )
    print(f"[ContextO] test_generator: stored new regression test for {file_path}")
    return code


async def poll_logs(log_url: str) -> list[dict[str, Any]]:
    """Reuse live_tools_server behavior: HTTP GET JSON list of log dicts."""
    headers = {"User-Agent": "ContextO-Pipeline/1.0"}
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
        resp = await client.get(log_url)
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []
