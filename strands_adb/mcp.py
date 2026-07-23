#!/usr/bin/env python3
"""MCP server entrypoint for strands-adb.

Exposes strands-adb tools (adb, recorder) via the Model Context Protocol,
so they can be used from Claude Code, Claude Desktop, Kiro, Cursor,
or any MCP-compatible client.

Built on strands-mcp-server (https://github.com/cagataycali/strands-mcp-server).

Usage:
    # stdio mode (Claude Code / Claude Desktop) — default
    strands-adb-mcp

    # HTTP mode (multi-client, background-capable)
    strands-adb-mcp --http --port 8000

    # Expose only a subset (comma-separated)
    strands-adb-mcp --tools adb

Claude Code:
    claude mcp add strands-adb -- strands-adb-mcp

Claude Desktop config:
    {
      "mcpServers": {
        "strands-adb": {
          "command": "strands-adb-mcp"
        }
      }
    }
"""
from __future__ import annotations

import argparse
import logging
import sys

# MCP stdio servers MUST log to stderr — stdout is the protocol channel.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("strands_adb.mcp")

# Tool groups → module attr names. Lazy import so a missing optional dep
# only disables its group, not the server.
TOOL_GROUPS: dict[str, tuple[str, list[str]]] = {
    "adb": ("strands_adb.adb_tool", ["adb"]),
    "recorder": ("strands_adb.recorder", ["recorder"]),
}


def collect_tools(skip: set[str], only: set[str] | None) -> list:
    """Import tool groups lazily; skip groups whose deps are missing."""
    import importlib

    tools = []
    for group, (module_path, names) in TOOL_GROUPS.items():
        if group in skip:
            logger.info(f"⏭  group '{group}' skipped by flag")
            continue
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:
            logger.warning(f"⏭  group '{group}' unavailable ({type(e).__name__}: {e})")
            continue
        for name in names:
            if only and name not in only:
                continue
            fn = getattr(mod, name, None)
            if fn is not None:
                tools.append(fn)
    return tools


def main() -> None:
    parser = argparse.ArgumentParser(
        description="strands-adb MCP server — Android device control over MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in (__doc__ or "") else "",
    )
    parser.add_argument("--http", action="store_true",
                        help="Run HTTP transport instead of stdio (default: stdio)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    parser.add_argument("--stateless", action="store_true",
                        help="Stateless HTTP mode (multi-node scalable)")
    parser.add_argument("--tools", type=str, default=None,
                        help="Comma-separated tool names to expose (default: all available)")
    parser.add_argument("--skip", type=str, default="",
                        help="Comma-separated groups to skip: " + ",".join(TOOL_GROUPS))
    parser.add_argument("--agent-invocation", action="store_true",
                        help="Also expose invoke_agent for full conversations (default: off — tools only)")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        from strands import Agent
        from strands_mcp_server.mcp_server import mcp_server
    except ImportError as e:
        logger.error(
            f"Missing dependency: {e}\n"
            "Install with: pip install strands-mcp-server strands-agents"
        )
        sys.exit(1)

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    only = {t.strip() for t in args.tools.split(",")} if args.tools else None

    tools = collect_tools(skip, only)
    if not tools:
        logger.error("No tools collected — check --tools/--skip flags and installed deps")
        sys.exit(1)

    logger.info(f"🤖 strands-adb MCP server: {len(tools)} tools ready")

    agent = Agent(
        name="strands-adb-mcp",
        tools=tools + [mcp_server],  # mcp_server must be registered to invoke it
        load_tools_from_directory=False,
        system_prompt="strands-adb tool server: Android device control via adb — UI automation, screenshots, apps, sensors, media, security.",
        callback_handler=None,
    )

    transport = "http" if args.http else "stdio"
    logger.info(f"Starting MCP server (transport={transport})")
    # Call the raw tool function directly (NOT agent.tool.mcp_server) —
    # agent.tool.* marks the agent as mid-invocation, and since stdio mode
    # blocks forever, all nested tool calls would then be rejected by the SDK.
    _fn = getattr(mcp_server, "_tool_func", None) or getattr(mcp_server, "original_function", None) or mcp_server
    _fn(
        action="start",
        transport=transport,
        port=args.port,
        stateless=args.stateless,
        expose_agent=args.agent_invocation,
        agent=agent,
    )

    if args.http:
        # HTTP runs in background thread — keep process alive
        import time
        logger.info(f"HTTP MCP server live at http://localhost:{args.port}/mcp (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Shutting down")


if __name__ == "__main__":
    main()
