"""
tools/mcp_client.py — Multi-MCP Stdio client.

Spawns and manages THREE concurrent MCP server connections:
  1. Windows MCP  — OS-level actions (screenshots, clicks, PowerShell, files)
  2. Playwright MCP — Browser automation (navigate, click DOM, fill forms, scrape)
  3. Fincept MCP — SEC EDGAR filings and financial data

Uses the official MCP Python SDK to:
  - Spawn each server as a subprocess via stdio transport
  - Connect and handshake concurrently
  - Discover all tools from every server
  - Merge into a single flat list of LangChain StructuredTool objects

CRITICAL: Only Windows MCP is mandatory. Playwright and Fincept degrade gracefully.
"""
import asyncio
import shlex
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

import config
from utils.logger import get_logger

logger = get_logger(__name__)


class MultiMCPClient:
    """
    Manages multiple concurrent MCP server connections.

    Each server gets its own ClientSession, stdio transport, and
    subprocess. Tools from all servers are merged into one flat list.
    """

    def __init__(self):
        # Dict of server_name -> {"session", "stdio_ctx", "session_ctx", "tools"}
        self._servers: dict[str, dict[str, Any]] = {}
        self._all_tools: list[StructuredTool] = []

    async def initialize_all(self) -> list[StructuredTool]:
        """
        Spawn and connect to all configured MCP servers concurrently.

        Returns:
            Flat list of all LangChain StructuredTool objects from all servers.
        """
        servers_to_start = []

        # Windows MCP — mandatory
        if config.WINDOWS_MCP_COMMAND:
            servers_to_start.append(("windows", config.WINDOWS_MCP_COMMAND))
        else:
            raise RuntimeError("WINDOWS_MCP_COMMAND is not set — cannot start.")

        # Playwright MCP — optional
        if config.PLAYWRIGHT_MCP_COMMAND:
            servers_to_start.append(("playwright", config.PLAYWRIGHT_MCP_COMMAND))

        # Fincept MCP — optional
        if config.FINCEPT_MCP_COMMAND:
            servers_to_start.append(("fincept", config.FINCEPT_MCP_COMMAND))

        # Start all servers concurrently
        results = await asyncio.gather(
            *[self._start_server(name, cmd) for name, cmd in servers_to_start],
            return_exceptions=True,
        )

        # Process results
        for (name, _cmd), result in zip(servers_to_start, results):
            if isinstance(result, Exception):
                if name == "windows":
                    raise RuntimeError(
                        f"Mandatory Windows MCP server failed to start: {result}"
                    )
                logger.warning(
                    "Optional MCP server '%s' failed to start (degraded): %s",
                    name, result,
                )
            else:
                tool_schemas = result
                wrapped = self._wrap_tools(name, tool_schemas)
                self._all_tools.extend(wrapped)
                logger.info(
                    "MCP server '%s' ready with %d tools", name, len(wrapped)
                )

        logger.info(
            "Multi-MCP initialized: %d total tools from %d servers",
            len(self._all_tools),
            len(self._servers),
        )
        return self._all_tools

    async def _start_server(self, name: str, cmd_string: str) -> list:
        """Spawn a single MCP server and return its tool schemas."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parts = shlex.split(cmd_string)
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        logger.info("Starting MCP server '%s': %s %s", name, command, args)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=None,
        )

        # Open stdio connection
        stdio_ctx = stdio_client(server_params)
        read, write = await stdio_ctx.__aenter__()

        # Create session
        session_ctx = ClientSession(read, write)
        session = await session_ctx.__aenter__()

        # Handshake
        await session.initialize()
        logger.info("MCP '%s' handshake complete", name)

        # Discover tools
        tools_response = await session.list_tools()
        tool_schemas = tools_response.tools

        logger.info(
            "MCP '%s' exposes %d tools: %s",
            name,
            len(tool_schemas),
            [t.name for t in tool_schemas],
        )

        # Store references for lifetime management
        self._servers[name] = {
            "session": session,
            "stdio_ctx": stdio_ctx,
            "session_ctx": session_ctx,
            "tool_names": [t.name for t in tool_schemas],
        }

        return tool_schemas

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Call an MCP tool on a specific server.

        Args:
            server_name: Which server to route to ("windows", "playwright", "fincept").
            tool_name: The MCP tool name.
            arguments: Dict of arguments matching the tool's input schema.

        Returns:
            The tool's text response.
        """
        server = self._servers.get(server_name)
        if server is None:
            raise RuntimeError(f"MCP server '{server_name}' is not connected.")

        session = server["session"]
        logger.info("MCP call [%s]: %s(%s)", server_name, tool_name, arguments)

        result = await session.call_tool(tool_name, arguments=arguments)

        # Check for MCP-level errors
        is_error = getattr(result, "isError", False)

        # Extract text content
        parts = []
        if hasattr(result, "content") and result.content:
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(f"[binary data: {len(block.data)} bytes]")
                else:
                    parts.append(str(block))

        content_str = "\n".join(parts) if parts else str(result)

        if is_error:
            raise RuntimeError(f"MCP tool error [{server_name}]: {content_str}")

        return content_str

    async def call_tool_raw(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Call an MCP tool and return the raw result object (for binary data like screenshots).
        """
        server = self._servers.get(server_name)
        if server is None:
            raise RuntimeError(f"MCP server '{server_name}' is not connected.")

        session = server["session"]
        return await session.call_tool(tool_name, arguments=arguments)

    def _wrap_tools(self, server_name: str, tool_schemas: list) -> list[StructuredTool]:
        """Convert MCP tool schemas into LangChain StructuredTool objects."""
        langchain_tools = []

        for schema in tool_schemas:
            tool_name = schema.name
            tool_desc = schema.description or f"MCP tool: {tool_name}"
            # Prefix description with server source for clarity
            tool_desc = f"[{server_name.upper()} MCP] {tool_desc}"
            input_schema = schema.inputSchema if hasattr(schema, "inputSchema") else {}

            # Build Pydantic fields from JSON Schema properties
            properties = input_schema.get("properties", {})
            required_fields = set(input_schema.get("required", []))

            pydantic_fields = {}
            for prop_name, prop_def in properties.items():
                prop_type = _json_type_to_python(prop_def.get("type", "string"))
                prop_desc = prop_def.get("description", "")
                default_val = prop_def.get("default", ...)

                if prop_name in required_fields:
                    pydantic_fields[prop_name] = (
                        prop_type,
                        Field(description=prop_desc),
                    )
                else:
                    if default_val is ...:
                        default_val = None
                        prop_type = prop_type | None
                    pydantic_fields[prop_name] = (
                        prop_type,
                        Field(default=default_val, description=prop_desc),
                    )

            # Create dynamic Pydantic model
            if pydantic_fields:
                ArgsModel = create_model(f"{tool_name}_args", **pydantic_fields)
            else:
                ArgsModel = create_model(f"{tool_name}_args")

            # Create the async tool function (closure captures server_name + tool_name)
            _server = server_name
            _name = tool_name

            async def _tool_func(_server=_server, _name=_name, **kwargs) -> str:
                cleaned = {}
                for k, v in kwargs.items():
                    if v is None:
                        continue
                    if isinstance(v, str) and v.strip().lower() in ("null", "none", "undefined", ""):
                        continue
                    cleaned[k] = v
                return await multi_mcp_client.call_tool(_server, _name, cleaned)

            st = StructuredTool.from_function(
                coroutine=_tool_func,
                name=tool_name,
                description=tool_desc,
                args_schema=ArgsModel,
            )
            langchain_tools.append(st)

        return langchain_tools

    async def shutdown_all(self) -> None:
        """Gracefully shut down all MCP sessions and subprocesses."""
        logger.info("Shutting down all MCP clients...")

        for name, server in self._servers.items():
            try:
                if server.get("session_ctx") is not None:
                    await server["session_ctx"].__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("Error closing MCP session '%s': %s", name, exc)

            try:
                if server.get("stdio_ctx") is not None:
                    await server["stdio_ctx"].__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("Error closing MCP stdio '%s': %s", name, exc)

        self._servers.clear()
        self._all_tools.clear()
        logger.info("All MCP clients shut down")

    def get_server_names(self) -> list[str]:
        """Return list of connected server names."""
        return list(self._servers.keys())

    def is_connected(self, server_name: str) -> bool:
        """Check if a specific server is connected."""
        return server_name in self._servers


# ── Module-level singleton ────────────────────────────────────────────────────

multi_mcp_client = MultiMCPClient()


def _json_type_to_python(json_type: str) -> type:
    """Map JSON Schema type strings to Python types."""
    mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return mapping.get(json_type, str)
