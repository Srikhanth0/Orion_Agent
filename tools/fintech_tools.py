"""
tools/fintech_tools.py — Financial terminal integration.

REST calls via httpx to the configured FINTECH_API_BASE_URL.
Includes a 5-minute TTL cache to avoid hammering the API on
repeated queries within the same session.
"""
import time
from typing import Any

import httpx
from langchain_core.tools import tool

import config
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Simple TTL Cache ──────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _get_cached(key: str) -> Any | None:
    """Return cached value if it exists and hasn't expired."""
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val
        del _cache[key]
    return None


def _set_cached(key: str, value: Any) -> None:
    """Store a value in the cache with current timestamp."""
    _cache[key] = (time.time(), value)


# ── HTTP Client ───────────────────────────────────────────────────────────────

def _get_client() -> httpx.AsyncClient:
    """Build an httpx async client with auth headers."""
    headers = {}
    if config.FINTECH_API_KEY:
        headers["Authorization"] = f"Bearer {config.FINTECH_API_KEY}"
    return httpx.AsyncClient(
        base_url=config.FINTECH_API_BASE_URL,
        headers=headers,
        timeout=30.0,
    )


async def _api_get(endpoint: str, params: dict | None = None) -> dict:
    """Make a GET request to the fintech API with caching."""
    cache_key = f"{endpoint}:{params}"
    cached = _get_cached(cache_key)
    if cached is not None:
        logger.debug("Cache hit: %s", cache_key)
        return cached

    async with _get_client() as client:
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()

    _set_cached(cache_key, data)
    return data


# ── Fintech Tools ─────────────────────────────────────────────────────────────

@tool
async def fintech_get_portfolio() -> str:
    """
    Retrieve the user's current portfolio holdings.
    Returns a formatted summary of positions with symbol, quantity,
    current value, and P&L.
    """
    try:
        data = await _api_get("/portfolio")
        holdings = data.get("holdings", [])
        if not holdings:
            return "Portfolio is empty."

        lines = ["Symbol  | Qty   | Value     | P&L"]
        lines.append("-" * 45)
        for h in holdings:
            lines.append(
                f"{h.get('symbol', '?'):<8}| "
                f"{h.get('quantity', 0):<6}| "
                f"${h.get('value', 0):>9,.2f} | "
                f"${h.get('pnl', 0):>+8,.2f}"
            )
        total = data.get("total_value", 0)
        lines.append(f"\nTotal Portfolio Value: ${total:,.2f}")
        return "\n".join(lines)
    except httpx.HTTPError as e:
        return f"Fintech API error: {e}"
    except Exception as e:
        return f"Portfolio fetch failed: {e}"


@tool
async def fintech_get_price(symbol: str) -> str:
    """
    Get the current price and daily change for a stock/crypto symbol.
    Args:
        symbol: Ticker symbol, e.g. 'AAPL', 'BTC', 'TSLA'.
    Returns:
        Current price, daily change, and volume.
    """
    try:
        data = await _api_get(f"/price/{symbol.upper()}")
        price = data.get("price", 0)
        change = data.get("change", 0)
        change_pct = data.get("change_pct", 0)
        volume = data.get("volume", 0)
        return (
            f"{symbol.upper()}: ${price:,.2f}\n"
            f"Change: ${change:+,.2f} ({change_pct:+.2f}%)\n"
            f"Volume: {volume:,.0f}"
        )
    except httpx.HTTPError as e:
        return f"Price fetch error for {symbol}: {e}"
    except Exception as e:
        return f"Price fetch failed for {symbol}: {e}"


@tool
async def fintech_get_market_summary() -> str:
    """
    Get a summary of major market indices (S&P 500, NASDAQ, DOW, etc.).
    Returns formatted table of indices with current values and changes.
    """
    try:
        data = await _api_get("/market/summary")
        indices = data.get("indices", [])
        if not indices:
            return "No market data available."

        lines = ["Index           | Value      | Change"]
        lines.append("-" * 50)
        for idx in indices:
            lines.append(
                f"{idx.get('name', '?'):<16}| "
                f"{idx.get('value', 0):>10,.2f} | "
                f"{idx.get('change_pct', 0):>+6.2f}%"
            )
        return "\n".join(lines)
    except httpx.HTTPError as e:
        return f"Market summary error: {e}"
    except Exception as e:
        return f"Market summary failed: {e}"


# ── Export ────────────────────────────────────────────────────────────────────

FINTECH_TOOLS = [
    fintech_get_portfolio,
    fintech_get_price,
    fintech_get_market_summary,
]
