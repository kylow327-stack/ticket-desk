"""Runtime configuration. The API key is read from the environment only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Official DeepSeek chat model as of 2026-11. `deepseek-chat` was retired on 2026-07-24.
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_STEPS_DEFAULT = 5

# Peak-hour list prices for deepseek-flash, USD per million tokens.
# Source: https://api-docs.deepseek.com/quick_start/pricing (retrieved 2026-11-16)
FLASH_PRICING_USD = {
    "input_cache_hit_peak": 0.006,
    "input_cache_hit_offpeak": 0.003,
    "input_cache_miss_peak": 0.30,
    "input_cache_miss_offpeak": 0.15,
    "output_peak": 1.20,
    "output_offpeak": 0.60,
}


def project_root() -> Path:
    env = os.environ.get("TICKET_AGENT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for candidate in (here.parents[2], here.parents[1], Path.cwd()):
        if (candidate / "pyproject.toml").exists() or (candidate / "data" / "tickets.json").exists():
            return candidate
    return Path.cwd()


def data_dir() -> Path:
    override = os.environ.get("TICKET_AGENT_DATA")
    path = Path(override).expanduser().resolve() if override else project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Settings:
    model: str
    base_url: str
    max_steps: int
    data_dir: Path
    db_path: Path
    traces_path: Path
    host: str
    port: int

    @property
    def api_key(self) -> str:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Export it in the environment "
                "(or use a gitignored .env loaded by your shell). Do not paste it into chat or the repo."
            )
        return key

    def api_key_present(self) -> bool:
        return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


def get_settings() -> Settings:
    root_data = data_dir()
    traces = root_data / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    return Settings(
        model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        max_steps=int(os.environ.get("TICKET_AGENT_MAX_STEPS", MAX_STEPS_DEFAULT)),
        data_dir=root_data,
        db_path=Path(os.environ.get("TICKET_AGENT_DB", root_data / "app.db")),
        traces_path=Path(os.environ.get("TICKET_AGENT_TRACES", traces / "runs.jsonl")),
        host=os.environ.get("TICKET_AGENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT") or os.environ.get("TICKET_AGENT_PORT", "8000")),
    )
