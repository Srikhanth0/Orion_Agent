"""tools/windows_enhanced_tools.py — UI Automation and element-finding tools.

Provides accessibility-based element finding (more reliable than pixel
guessing), window title detection, and process verification.
"""
from langchain_core.tools import tool

from tools.mcp_client import multi_mcp_client


@tool
async def find_ui_element_coordinates(element_name: str, control_type: str = "Any") -> str:
    """
    Find the center screen coordinates of a Windows UI element by its accessibility name.
    Use this INSTEAD of guessing coordinates from screenshots when possible.
    More reliable than pixel-based vision coordinate extraction.
    Args:
        element_name: The visible text, AutomationId, or Name of the UI element.
        control_type: Windows control type: Button, Edit, MenuItem, Window, Text, etc.
    Returns:
        JSON string: {"found": bool, "x": int, "y": int, "width": int, "height": int}
    """
    ps = f"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$nameCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, "{element_name}",
    [System.Windows.Automation.PropertyConditionFlags]::IgnoreCase
)
$el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $nameCond)
if ($el -ne $null) {{
    $r = $el.Current.BoundingRectangle
    @{{found=$true; x=[int]($r.X + $r.Width/2); y=[int]($r.Y + $r.Height/2); width=[int]$r.Width; height=[int]$r.Height}} | ConvertTo-Json -Compress
}} else {{
    @{{found=$false; x=0; y=0; width=0; height=0}} | ConvertTo-Json -Compress
}}
"""
    result = await multi_mcp_client.call_tool("windows", "PowerShell", {"command": ps})
    return str(result)


@tool
async def get_focused_window_title() -> str:
    """
    Get the title of the currently focused/foreground window.
    Use after launching an app to confirm it opened correctly.
    Returns: Window title string.
    """
    ps = "(Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | Sort-Object CPU -Descending | Select-Object -First 1 -ExpandProperty MainWindowTitle)"
    result = await multi_mcp_client.call_tool("windows", "PowerShell", {"command": ps})
    return str(result)


@tool
async def verify_app_is_running(app_name: str) -> str:
    """
    Check whether an application is currently running.
    Use after LAUNCH actions to confirm the app actually started.
    Args:
        app_name: Process name (e.g., 'notepad', 'chrome', 'explorer').
    Returns:
        "RUNNING: <process details>" or "NOT_RUNNING"
    """
    ps = f"$p = Get-Process '{app_name}' -ErrorAction SilentlyContinue; if ($p) {{ 'RUNNING: ' + ($p | Select-Object -First 1 | Format-List Id,Name,CPU | Out-String) }} else {{ 'NOT_RUNNING' }}"
    result = await multi_mcp_client.call_tool("windows", "PowerShell", {"command": ps})
    return str(result)


WINDOWS_ENHANCED_TOOLS = [
    find_ui_element_coordinates,
    get_focused_window_title,
    verify_app_is_running,
]
