"""agents/calibrator.py — Screen resolution and DPI calibration node.

Runs once per task before any GUI execution to capture the current
screen geometry. All coordinate calculations use these values.
"""
import json
from typing import Any

from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)

_RESOLUTION_PS = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
    "@{Width=$s.Width; Height=$s.Height} | ConvertTo-Json -Compress"
)

_DPI_PS = (
    "Add-Type -AssemblyName System.Drawing; "
    "$g = [System.Drawing.Graphics]::FromHwnd([System.IntPtr]::Zero); "
    "[int]$g.DpiX"
)


async def calibrator_node(state: AgentState, mcp_client: Any = None) -> dict:
    """Run once per task — captures screen geometry for accurate click coords."""
    if state.get("calibrated"):
        return {"calibrated": True}  # Already calibrated for this task

    if mcp_client is None or not mcp_client.is_connected("windows"):
        logger.warning("Calibrator: Windows MCP not connected, using defaults")
        return {
            "screen_width": 1920,
            "screen_height": 1080,
            "dpi_scale": 1.0,
            "calibrated": True,
        }

    width, height = 1920, 1080
    dpi_scale = 1.0

    try:
        res_result = await mcp_client.call_tool(
            "windows", "PowerShell", {"command": _RESOLUTION_PS}
        )
        res_data = json.loads(str(res_result))
        width = int(res_data["Width"])
        height = int(res_data["Height"])
    except Exception as exc:
        logger.warning("Resolution detection failed: %s — using 1920x1080", exc)

    try:
        dpi_result = await mcp_client.call_tool(
            "windows", "PowerShell", {"command": _DPI_PS}
        )
        dpi = float(str(dpi_result).strip())
        dpi_scale = dpi / 96.0
    except Exception as exc:
        logger.warning("DPI detection failed: %s — assuming 1.0 scale", exc)

    logger.info("Calibration: %dx%d @ %.2fx DPI scale", width, height, dpi_scale)
    return {
        "screen_width": width,
        "screen_height": height,
        "dpi_scale": dpi_scale,
        "calibrated": True,
    }
