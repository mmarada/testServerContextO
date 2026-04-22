import asyncio
import json
import os
import uuid
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from config import Settings, get_settings

load_dotenv()


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_env_config() -> dict[str, str]:
    env_config = os.environ.copy()
    homebrew_path = "/opt/homebrew/bin"
    env_config["PATH"] = (
        f"{homebrew_path}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:"
        f"{env_config.get('PATH', '')}"
    )
    token = (
        os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        or os.getenv("GITHUB_PERSONAL_TOKEN")
        or ""
    )
    env_config["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    return env_config


def build_mcp_client() -> MultiServerMCPClient:
    env_config = build_env_config()
    return MultiServerMCPClient(
        {
            "github": {
                "command": "/opt/homebrew/bin/npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": env_config,
                "transport": "stdio",
            },
            "live_site": {
                "command": "python3",
                "args": [os.path.join(os.getcwd(), "live_tools_server.py")],
                "env": env_config,
                "transport": "stdio",
            },
        }
    )


class TraceExtract(BaseModel):
    file: str = Field(description="Repository-relative path to the failing source file")
    line: int = Field(description="1-based line number of the failure")
    function: str = Field(default="unknown", description="Best-guess function or method name")
    root_cause: str = Field(default="", description="Short technical root cause")
    user_action: str = Field(default="", description="User-visible action leading to the error")
    log_trace: str = Field(default="", description="Key log lines / error message summary")
    commit_sha: str | None = Field(default=None, description="Optional related commit SHA")


def _final_assistant_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)
    return ""


async def run_tracer(
    error_event: dict[str, Any],
    mcp_client: MultiServerMCPClient,
    llm: ChatGoogleGenerativeAI,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """
    Run the SRE ReAct tracer for one error event and return a structured trace_map dict.
    """
    settings = settings or get_settings()
    all_tools = await mcp_client.get_tools()
    llm_with_tools = llm.bind_tools(all_tools)

    def call_model(state: AgentState):
        prompt = SystemMessage(
            content=(
                "You are an SRE Debugger. Trace ONE error from UI to code.\n"
                "1. You may call get_live_system_logs if you need surrounding context, but the "
                "canonical failing file and line are already provided in the user message.\n"
                "2. Use GitHub tools (get_file_contents) to read the failing file in repository "
                f"{settings.github_owner}/{settings.github_repo}.\n"
                "3. Explain the Trace Map: [User Action] -> [Log Trace] -> [Code Failure Reason].\n"
                "Be concise; cite the exact code path that fails."
            )
        )
        return {"messages": [llm_with_tools.invoke([prompt] + state["messages"])]}

    workflow = StateGraph(AgentState)
    workflow.add_node("assistant", call_model)
    workflow.add_node("tools", ToolNode(all_tools))
    workflow.add_edge(START, "assistant")
    workflow.add_conditional_edges(
        "assistant",
        lambda x: "tools" if x["messages"][-1].tool_calls else END,
    )
    workflow.add_edge("tools", "assistant")

    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)

    payload = json.dumps(error_event, indent=2, default=str)
    query = (
        f"Investigate this single production error. Repository: "
        f"{settings.github_owner}/{settings.github_repo}.\n\n"
        f"ERROR_EVENT_JSON:\n{payload}\n"
    )
    config = {"configurable": {"thread_id": f"tracer_{error_event.get('trace_id', 'unknown')}"}}

    async for _ in app.astream(
        {"messages": [HumanMessage(content=query)]},
        config=config,
    ):
        pass

    state = await app.aget_state(config)
    messages = state.values.get("messages", []) if state.values else []
    analysis = _final_assistant_text(messages)

    extractor = llm.with_structured_output(TraceExtract)
    structured = await extractor.ainvoke(
        [
            SystemMessage(
                content=(
                    "Extract structured fields for the incident database. "
                    "If unsure, use file/line from ERROR_EVENT_JSON."
                )
            ),
            HumanMessage(
                content=f"ERROR_EVENT_JSON:\n{payload}\n\nANALYSIS:\n{analysis or '(empty)'}"
            ),
        ]
    )

    file_path = structured.file or str(error_event.get("file", ""))
    line_number = structured.line or int(error_event.get("line", 0))
    incident_id = str(uuid.uuid4())

    trace_map: dict[str, Any] = {
        "incident_id": incident_id,
        "trace_id": str(error_event.get("trace_id", "")),
        "commit_sha": structured.commit_sha,
        "user_action": structured.user_action
        or str(error_event.get("event", "unknown_user_action")),
        "log_trace": structured.log_trace
        or f"{error_event.get('error', '')} @ {error_event.get('timestamp', '')}",
        "file_path": file_path,
        "line_number": line_number,
        "root_cause": structured.root_cause or str(error_event.get("error", "")),
        "created_at": error_event.get("timestamp"),
        "file": file_path,
        "line": line_number,
        "function": structured.function or "unknown",
    }
    return trace_map


async def main():
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.google_api_key,
    )
    mcp_client = build_mcp_client()
    sample = {
        "timestamp": "",
        "trace_id": "demo",
        "event": "UI_BUTTON_CLICK",
        "error": "demo",
        "file": "billing_logic.py",
        "line": 15,
    }
    print("[ContextO] live_agent standalone: running tracer demo (requires MCP + API keys)")
    tm = await run_tracer(sample, mcp_client, llm, settings)
    print(json.dumps(tm, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
