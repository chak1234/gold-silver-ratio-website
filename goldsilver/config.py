"""Application settings, read from the environment or a local ``.env`` file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    Gold API's live prices are free and need no key. The one-minute default keeps
    well clear of its ban on clients that send bursts of requests.
    """

    gold_api_key: str = ""
    price_ttl_seconds: int = 60
    history_ttl_seconds: int = 86_400
    retry_delay_seconds: int = 300
    request_timeout_seconds: int = 10
    cache_path: Path = PROJECT_ROOT / "instance" / "market-cache.json"
    host: str = "127.0.0.1"
    port: int = 5000

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(PROJECT_ROOT / ".env")
        return cls(
            gold_api_key=os.getenv("GOLD_API_KEY", "").strip(),
            price_ttl_seconds=_int_env("PRICE_CACHE_SECONDS", cls.price_ttl_seconds),
            history_ttl_seconds=_int_env("HISTORY_CACHE_SECONDS", cls.history_ttl_seconds),
            cache_path=Path(os.getenv("CACHE_PATH", "").strip() or cls.cache_path),
            host=os.getenv("HOST", "").strip() or cls.host,
            port=_int_env("PORT", cls.port),
        )
