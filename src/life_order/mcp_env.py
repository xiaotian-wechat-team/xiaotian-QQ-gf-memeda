"""Load life_order MCP credentials from env file. Never log secret values."""
from __future__ import annotations

import os
from pathlib import Path

from config import ROOT

DEFAULT_ENV_FILE = ROOT / "config" / "life_order_mcp.env"

# Public endpoints (no secrets).
LUCKIN_MCP_URL = "https://gwmcp.lkcoffee.com/order/user/mcp"
MCD_MCP_URL = "https://mcp.mcd.cn"

# Env var names (values must never be printed).
LUCKIN_KEY_VARS = ("LUCKIN_MCP_KEY", "LUCKIN_MCP_TOKEN")
MCD_KEY_VARS = ("MCD_MCP_KEY", "MCD_MCP_TOKEN")


def load_dotenv_file(path: Path | str | None = None) -> dict[str, str]:
    """Parse KEY=VALUE lines into a dict. Does not mutate os.environ unless asked."""
    path = Path(path or os.environ.get("LIFE_ORDER_MCP_ENV") or DEFAULT_ENV_FILE)
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def apply_env_file(path: Path | str | None = None, *, override: bool = False) -> list[str]:
    """Load file into os.environ. Returns list of keys set (names only)."""
    loaded = []
    for k, v in load_dotenv_file(path).items():
        if override or k not in os.environ or not os.environ.get(k):
            os.environ[k] = v
            loaded.append(k)
    return loaded


def get_secret(*names: str) -> str | None:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return None


def luckin_token() -> str | None:
    apply_env_file()
    return get_secret(*LUCKIN_KEY_VARS)


def mcd_token() -> str | None:
    apply_env_file()
    return get_secret(*MCD_KEY_VARS)


def secret_status() -> dict:
    """Safe status for logs/tests — lengths only, never values."""
    apply_env_file()
    lt = luckin_token()
    mt = mcd_token()
    return {
        "luckin_set": bool(lt),
        "luckin_len": len(lt) if lt else 0,
        "mcd_set": bool(mt),
        "mcd_len": len(mt) if mt else 0,
        "luckin_url": LUCKIN_MCP_URL,
        "mcd_url": MCD_MCP_URL,
        "env_file": str(DEFAULT_ENV_FILE),
        "env_file_exists": DEFAULT_ENV_FILE.exists(),
    }
