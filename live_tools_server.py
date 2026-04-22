import json
import os
import sqlite3

import httpx
from mcp.server.fastmcp import FastMCP

from contexto.memory.context_store import SCHEMA, read_recent_incidents_sync

mcp = FastMCP("LiveBridge")


@mcp.tool()
async def get_live_system_logs() -> str:
    """Fetch the most recent error logs from the live Flask website."""
    # Adding a header makes the request look like a real browser
    headers = {"User-Agent": "MCP-Debug-Agent/1.0"}
    url = os.getenv("LOG_SOURCE_URL", "http://127.0.0.1:5000/api/logs")
    async with httpx.AsyncClient(headers=headers) as client:
        response = await client.get(url)
        
        if response.status_code != 200:
            return f"Access Denied (HTTP {response.status_code}). Check if app.py is running."
            
        return str(response.json())


@mcp.tool()
async def get_recent_incidents() -> str:
    """Returns the 20 most recent incidents from ContextO's context store."""
    db_path = os.getenv("DB_PATH", "contexto.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    rows = read_recent_incidents_sync(db_path, limit=20)
    return json.dumps(rows, indent=2, default=str)


if __name__ == "__main__":
    mcp.run(transport="stdio")