"""
tools/fincept_tools.py — Tools for interacting directly with the Fincept Terminal application.

Provides LangChain tools to open the Fincept Terminal GUI application.
"""
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from config import _PROJECT_ROOT
from utils.logger import get_logger

logger = get_logger(__name__)


@tool
def launch_fincept_terminal() -> str:
    """
    Launch the Fincept Terminal GUI application.
    
    Call this tool when the user asks to "open" or "start" Fincept Terminal.
    This runs the application independently while Agent Orion continues to run.
    """
    # Look for the compiled executable in the build folder
    exe_path = _PROJECT_ROOT / "FinceptTerminal" / "fincept-qt" / "build" / "FinceptTerminal.exe"
    
    if not exe_path.exists():
        # Fallback to looking in the root or program files if not built from source
        fallback_path = Path("C:/Program Files/Fincept/FinceptTerminal/FinceptTerminal.exe")
        if fallback_path.exists():
            exe_path = fallback_path
        else:
            return (
                "Error: Fincept Terminal executable not found. "
                "Ensure it has been built from source in `FinceptTerminal/fincept-qt/build/FinceptTerminal.exe` "
                "or installed system-wide."
            )
            
    try:
        # Launch detached so it doesn't block the agent
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        logger.info("Launched Fincept Terminal: %s", exe_path)
        return "Fincept Terminal GUI has been successfully launched in the background."
    except Exception as e:
        logger.exception("Failed to launch Fincept Terminal")
        return f"Failed to launch Fincept Terminal: {e}"

