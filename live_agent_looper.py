import asyncio
import os
import ast
from typing import Annotated, TypedDict
from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

load_dotenv()

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

# --- ENVIRONMENT CONFIGURATION ---
env_config = os.environ.copy()
homebrew_path = "/opt/homebrew/bin"
env_config["PATH"] = f"{homebrew_path}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{env_config.get('PATH', '')}"
env_config["GITHUB_PERSONAL_ACCESS_TOKEN"] = os.getenv("GITHUB_PERSONAL_TOKEN")

mcp_client = MultiServerMCPClient({
    "github": {
        "command": "/opt/homebrew/bin/npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": env_config,
        "transport": "stdio"
    },
    "live_site": {
        "command": "python3",
        "args": [os.path.join(os.getcwd(), "live_tools_server.py")],
        "env": env_config,
        "transport": "stdio"
    }
})

async def main():
    # Model preserved as Gemini 2.5 Flash
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
    
    # Persistent SQLite database for short-term and long-term memory
    async with AsyncSqliteSaver.from_conn_string("agent_memory.db") as saver:
        
        # Load tools from both MCP servers
        all_tools = await mcp_client.get_tools()
        llm_with_tools = llm.bind_tools(all_tools)

        def call_model(state: AgentState):
            prompt = SystemMessage(content=(
                "You are an SRE Debugger. Your mission is to trace errors from UI to Code:\n"
                "1. First, call 'get_live_system_logs' to identify recent site errors.\n"
                "2. Find the filename and line number in the log trace.\n"
                "3. Use 'github' tools to read that specific file from the repository.\n"
                "4. Synthesize the 'Trace Map': [User Action] -> [Log Trace] -> [Code Failure Reason]."
            ))
            return {"messages": [llm_with_tools.invoke([prompt] + state["messages"])]}

        # Setup Graph
        workflow = StateGraph(AgentState)
        workflow.add_node("assistant", call_model)
        workflow.add_node("tools", ToolNode(all_tools))

        workflow.add_edge(START, "assistant")
        workflow.add_conditional_edges(
            "assistant", 
            lambda x: "tools" if x["messages"][-1].tool_calls else END
        )
        workflow.add_edge("tools", "assistant")
        
        app = workflow.compile(checkpointer=saver)
        
        GITHUB_OWNER = "mmarada" 
        GITHUB_REPO = "ContextO"
        
        # Track session-specific processed IDs in local RAM to avoid redundant log-parsing
        processed_trace_ids = set()

        print(f"\n🕵️ Watchdog active for {GITHUB_OWNER}/{GITHUB_REPO}...")
        print("Polling http://127.0.0.1:5001/api/logs every 10 seconds...")

        while True:
            try:
                # 1. Direct tool call to poll logs (Standard Python logic, zero LLM tokens)
                log_tool = next(t for t in all_tools if t.name == "get_live_system_logs")
                tool_output = await log_tool.ainvoke({})
                
                # Normalize tool_output safely
                if isinstance(tool_output, dict):
                    logs = tool_output 
                elif isinstance(tool_output, list) and hasattr(tool_output[0], 'content'):
                    logs = ast.literal_eval(tool_output[0].content)
                else:
                    logs = ast.literal_eval(str(tool_output))

                # Ensure logs is iterable
                if isinstance(logs, dict):
                    logs = [logs]

                for entry in logs:
                    trace_id = entry.get('trace_id')
                    
                    if trace_id and trace_id not in processed_trace_ids:
                        # We identify a trace_id we haven't seen this session
                        processed_trace_ids.add(trace_id)
                        
                        # Set up the thread config for memory lookup
                        config = {"configurable": {"thread_id": f"incident_{trace_id}"}}
                        
                        # --- 2. DATABASE MEMORY CHECK ---
                        # Query the SQLite checkpointer for an existing thread state
                        state = await app.aget_state(config)
                        
                        existing_report = None
                        if state.values and "messages" in state.values:
                            # Search history for a previous successful analysis (AIMessage)
                            for msg in reversed(state.values["messages"]):
                                if isinstance(msg, AIMessage) and msg.content:
                                    existing_report = msg.content
                                    break

                        if existing_report:
                            print(f"\n🧠 [MEMORY RECALL] Recognized TraceID {trace_id}:")
                            print(f"I found a previous analysis in agent_memory.db.")
                            print(f"--- PREVIOUS SRE REPORT ---\n{existing_report}")
                            continue # Skip the LLM investigation loop

                        # --- 3. TRIGGER LLM INVESTIGATION (ONLY IF NEW) ---
                        print(f"\n🚨 [NEW INCIDENT] No record found. Investigating TraceID: {trace_id}...")
                        query = f"A user just reported a crash. Trace it from the live site to the code in my repo '{GITHUB_OWNER}/{GITHUB_REPO}'."
                        
                        async for output in app.astream({"messages": [HumanMessage(content=query)]}, config=config):
                            for node, value in output.items():
                                if node == "assistant":
                                    msg = value["messages"][-1]
                                    if msg.content:
                                        print(f"\n--- SRE REPORT (GENERATED) ---\n{msg.content}")

            except Exception as e:
                print(f"Monitoring heartbeat failed: {e}")
            
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())