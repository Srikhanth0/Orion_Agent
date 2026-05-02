"""
config.py — Load and validate all environment variables.
Copy .env.example → .env and fill in your values before running.

LLM Providers:
  - NVIDIA NIM (OpenAI-compatible) for Reasoning + Routing
  - OpenRouter (OpenAI-compatible) for Vision (Gemma 4 31B)
"""
import os
import time
import asyncio
import threading
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (same directory as this file)
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")


# ── LLM (NVIDIA NIM) ────────────────────────────────────────────────────────
NVIDIA_NIM_API_KEY: str = os.environ.get("NVIDIA_NIM_API_KEY", "")
NIM_BASE_URL: str = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL: str = os.getenv(
    "NIM_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"
)
NIM_FAST_MODEL: str = os.getenv(
    "NIM_FAST_MODEL", "meta/llama-3.1-8b-instruct"
)
AGENT_TEMPERATURE: float = float(os.getenv("AGENT_TEMPERATURE", "0.1"))
AGENT_MAX_TOKENS: int = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
EXECUTOR_MAX_TOKENS: int = int(os.getenv("EXECUTOR_MAX_TOKENS", "1024"))
VALIDATOR_SKIP_NON_GUI: bool = os.getenv("VALIDATOR_SKIP_NON_GUI", "true").lower() == "true"

# ── LLM (OpenRouter — Vision) ───────────────────────────────────────────────
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
OPENROUTER_VISION_MODEL: str = os.getenv(
    "OPENROUTER_MODEL", "google/gemma-4-31b-it:free"
)

# ── TELEGRAM ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
_raw_ids = os.getenv("ALLOWED_USER_IDS", "")
# Strip comments if they were included by the env loader
if "#" in _raw_ids:
    _raw_ids = _raw_ids.split("#")[0].strip()

ALLOWED_USER_IDS: set[int] = (
    {int(i.strip()) for i in _raw_ids.split(",") if i.strip()}
    if _raw_ids
    else set()
)

# ── SLACK ─────────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")

# ── GOOGLE WORKSPACE ─────────────────────────────────────────────────────────
GOOGLE_CREDENTIALS_PATH: Path = Path(
    os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
)
GOOGLE_TOKEN_PATH: Path = Path(
    os.getenv("GOOGLE_TOKEN_PATH", "token.json")
)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]

# ── MULTI-MCP ─────────────────────────────────────────────────────────────────
WINDOWS_MCP_COMMAND: str = os.getenv("WINDOWS_MCP_COMMAND", "uvx windows-mcp")
PLAYWRIGHT_MCP_COMMAND: str = os.getenv(
    "PLAYWRIGHT_MCP_COMMAND", "npx -y @modelcontextprotocol/server-puppeteer"
)
_default_fincept_mcp = f'"{_PROJECT_ROOT}/.venv/Scripts/python.exe" "{_PROJECT_ROOT}/run_fincept_mcp.py"'
FINCEPT_MCP_COMMAND: str = os.getenv("FINCEPT_MCP_COMMAND", _default_fincept_mcp)

# ── FINTECH ───────────────────────────────────────────────────────────────────
FINTECH_API_KEY: str = os.getenv("FINTECH_API_KEY", "")
FINTECH_API_BASE_URL: str = os.getenv(
    "FINTECH_API_BASE_URL", "https://your-fintech-api.com"
)

# ── MEMORY ────────────────────────────────────────────────────────────────────
CHROMA_PERSIST_PATH: str = os.getenv(
    "CHROMA_PERSIST_PATH", str(_PROJECT_ROOT / "memory" / "chroma_db")
)
SQLITE_PATH: str = os.getenv(
    "SQLITE_PATH", str(_PROJECT_ROOT / "memory" / "tasks.db")
)

# ── AGENT ─────────────────────────────────────────────────────────────────────
AGENT_MAX_ITERATIONS: int = int(os.getenv("AGENT_MAX_ITERATIONS", "15"))
AGENT_MAX_RETRIES: int = int(os.getenv("AGENT_MAX_RETRIES", "3"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── UI Server ─────────────────────────────────────────────────────────────────
UI_ENABLED: bool = os.getenv("UI_ENABLED", "true").lower() == "true"
UI_PORT: int = int(os.getenv("UI_PORT", "8765"))
UI_AUTO_OPEN: bool = os.getenv("UI_AUTO_OPEN", "false").lower() == "true"
LOG_FILE_PATH: str = os.getenv("LOG_FILE_PATH", str(_PROJECT_ROOT / "logs" / "orion.log"))


# ═══════════════════════════════════════════════════════════════════════════════
# OpenRouter Rate Limiter
# Free tier: ~20 requests/min — we enforce a conservative limit.
# ═══════════════════════════════════════════════════════════════════════════════

class _OpenRouterRateLimiter:
    """Thread-safe, async-safe token-bucket rate limiter for OpenRouter free tier."""

    def __init__(self, max_calls: int = 10, period_seconds: float = 60.0):
        self._max_calls = max_calls
        self._period = period_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    async def acquire(self) -> None:
        """Wait until a request slot is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                # Prune timestamps older than the window
                self._timestamps = [
                    t for t in self._timestamps if now - t < self._period
                ]
                if len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    return
                # Calculate wait time until the oldest slot expires
                wait = self._period - (now - self._timestamps[0]) + 0.1
            await asyncio.sleep(wait)


# Singleton rate limiter: 10 calls per 60 seconds (conservative for free tier)
_openrouter_limiter = _OpenRouterRateLimiter(max_calls=10, period_seconds=60.0)


# ═══════════════════════════════════════════════════════════════════════════════
# LLM Factory Functions
# ═══════════════════════════════════════════════════════════════════════════════

def get_llm():
    """Return the primary Reasoning LLM (70B) configured for NVIDIA NIM."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=NIM_MODEL,
        openai_api_key=NVIDIA_NIM_API_KEY,
        openai_api_base=NIM_BASE_URL,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
    )


def get_executor_llm():
    """Return LLM configured for fast executor calls (low max_tokens)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=NIM_MODEL,
        openai_api_key=NVIDIA_NIM_API_KEY,
        openai_api_base=NIM_BASE_URL,
        temperature=AGENT_TEMPERATURE,
        max_tokens=EXECUTOR_MAX_TOKENS,  # 1024 vs 4096 — much faster
    )


def get_fast_llm():
    """Return the fast Routing LLM (8B) for classification tasks."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=NIM_FAST_MODEL,
        openai_api_key=NVIDIA_NIM_API_KEY,
        openai_api_base=NIM_BASE_URL,
        temperature=0.0,
        max_tokens=1024,
    )


def get_vision_llm():
    """
    Return the Vision LLM (Gemma 4 31B) via OpenRouter.

    OpenRouter uses OpenAI-compatible API format, so ChatOpenAI works directly.
    Rate-limited via _openrouter_limiter — callers must await acquire() first.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=OPENROUTER_VISION_MODEL,
        openai_api_key=OPENROUTER_API_KEY,
        openai_api_base=OPENROUTER_BASE_URL,
        temperature=0.0,
        max_tokens=1024,
        default_headers={
            "HTTP-Referer": "https://agent-orion.local",
            "X-Title": "AGENT ORION",
        },
    )


async def get_vision_llm_rate_limited():
    """Acquire a rate-limit slot and return the Vision LLM instance."""
    await _openrouter_limiter.acquire()
    return get_vision_llm()
