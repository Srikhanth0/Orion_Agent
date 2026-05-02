import sys
import os
import mcp

# Add the script directory to path so it can import its dependencies if needed
script_dir = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "FinceptTerminal", "fincept-qt", "scripts", "agents", "rdagents"
))
sys.path.insert(0, script_dir)

from mcp_server import build_mcp_server

if __name__ == "__main__":
    server = build_mcp_server()
    # Run the server on standard stdio
    server.run(transport="stdio")
