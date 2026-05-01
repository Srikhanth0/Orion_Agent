"""
tools/file_tools.py — Basic file operations.

These tools provide the agent with local file reading and writing capabilities.
They use pure Python standard library functions, keeping them lightweight.
"""
from pathlib import Path
from langchain_core.tools import tool

from utils.logger import get_logger

logger = get_logger(__name__)


@tool
def file_read_text(path: str, max_lines: int = 1000) -> str:
    """
    Read text from a local file.
    Args:
        path: Absolute or relative path to the file.
        max_lines: Maximum number of lines to read (default 1000).
    Returns:
        The text content of the file.
    """
    try:
        p = Path(path).resolve()
        if not p.exists():
            return f"Error: File not found: {p}"
        if not p.is_file():
            return f"Error: Path is not a file: {p}"

        with open(p, "r", encoding="utf-8") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    lines.append(f"\n... (truncated after {max_lines} lines)")
                    break
                lines.append(line)
        return "".join(lines)
    except Exception as e:
        return f"Error reading file {path}: {e}"


@tool
def file_write_text(path: str, content: str, overwrite: bool = False) -> str:
    """
    Write text to a local file.
    Args:
        path: Absolute or relative path to the file.
        content: The text content to write.
        overwrite: If False, will fail if file exists. If True, will overwrite.
    Returns:
        Confirmation message.
    """
    try:
        p = Path(path).resolve()
        
        if p.exists() and not overwrite:
            return f"Error: File already exists at {p}. Set overwrite=True to overwrite."

        # Ensure parent directories exist
        p.parent.mkdir(parents=True, exist_ok=True)

        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        
        return f"Successfully wrote {len(content)} characters to {p}"
    except Exception as e:
        return f"Error writing file {path}: {e}"


@tool
def file_search_directory(directory: str, pattern: str = "*") -> str:
    """
    List files in a directory matching a pattern.
    Args:
        directory: The directory path to search in.
        pattern: Glob pattern, e.g., "*.txt", "**/*.py". Default is "*".
    Returns:
        List of matching file paths.
    """
    try:
        d = Path(directory).resolve()
        if not d.exists():
            return f"Error: Directory not found: {d}"
        if not d.is_dir():
            return f"Error: Path is not a directory: {d}"

        matches = list(d.glob(pattern))
        if not matches:
            return f"No files found matching '{pattern}' in {d}"
            
        # Limit output to prevent massive responses
        max_results = 50
        result_lines = [f"Found {len(matches)} matches (showing up to {max_results}):"]
        for m in matches[:max_results]:
            result_lines.append(f" - {m.relative_to(d) if m.is_relative_to(d) else m}")
            
        if len(matches) > max_results:
            result_lines.append(f"... and {len(matches) - max_results} more.")
            
        return "\n".join(result_lines)
    except Exception as e:
        return f"Error searching directory {directory}: {e}"


FILE_TOOLS = [
    file_read_text,
    file_write_text,
    file_search_directory,
]
