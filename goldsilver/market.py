"""Market data: the MetalpriceAPI client and a quota-aware persistent cache."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import requests

from .config import Settings

logger = logging.getLogger(__name__)

GRAMS_PER_TROY_OUNCE = 31.1035
HISTORY_DAYS = 365  # MetalpriceAPI's maximum range for a single timeframe request.
SOURCE_NAME = "MetalpriceAPI"
SNAPSHOTS_KEY = "snapshots"


class MarketDataError(Exception):
    """Market data could not be obtained. Messages never contain credentials."""


class ProviderRejectedError(MarketDataError):
    """The provider refused the request, for example because of the plan or quota."""


class History(NamedTuple):
    """Daily ratio history. ``recorded`` means it was collected locally from past quotes."""

    points: list[dict[str, float | str]]
    stale: bool
    recorded: bool


def _inverse(rates: dict[str, Any], code: str) -> float:
    """Convert an "ounces per US dollar" rate into US dollars per ounce."""
    try:
        rate = float(rates[code])
    except (KeyError, TypeError, ValueError):
        raise MarketDataError(f"response has no usable {code} rate") from None
    if rate <= 0:
        raise MarketDataError(f"response has a non-positive {code} rate")
    return 1 / rate


def _rate(rates: dict[str, Any], code: str) -> float:
    return 1 / _inverse(rates, code)


@dataclass(frozen=True)
class Quote:
    """Spot prices in US dollars per troy ounce, plus the FX rates needed to convert them."""

    gold_usd: float
    silver_usd: float
    usd_cad: float
    usd_inr: float
    as_of: int
    stale: bool = False

    @property
    def ratio(self) -> float:
        return self.gold_usd / self.silver_usd

    def rate(self, currency: str) -> float:
        return {"USD": 1.0, "CAD": self.usd_cad, "INR": self.usd_inr}[currency]

    def price_per_ounce(self, metal: str, currency: str) -> float:
        usd = self.gold_usd if metal == "gold" else self.silver_usd
        return usd * self.rate(currency)

    def price_per_gram(self, metal: str, currency: str) -> float:
        return self.price_per_ounce(metal, currency) / GRAMS_PER_TROY_OUNCE


class MetalPriceClient:
    """Thin client for https://metalpriceapi.com.

    The key travels in a header so it cannot end up in URLs, logs or tracebacks.
    """

    BASE_URL = "https://api.metalpriceapi.com/v1"

    def __init__(self, api_key: str, timeout: float = 10) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def _get(self, path: str, **params: str) -> dict[str, Any]:
        if not self._api_key:
            raise MarketDataError("METALPRICE_API_KEY is not configured")
        try:
            response = requests.get(
                f"{self.BASE_URL}/{path}",
                params={"base": "USD", **params},
                headers={"X-API-KEY": self._api_key},
                timeout=self._timeout,
            )
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MarketDataError(f"{path} request failed: {type(exc).__name__}") from None

        if not isinstance(payload, dict) or not payload.get("success"):
            error = payload.get("error") if isinstance(payload, dict) else None
            code = error.get("code") if isinstance(error, dict) else "unknown"
            raise ProviderRejectedError(
                f"{path} rejected with HTTP {response.status_code}, error code {code}"
            )
        return payload

    def latest(self) -> dict[str, float | int]:
        payload = self._get("latest", currencies="XAU,XAG,CAD,INR")
        rates = payload.get("rates")
        if not isinstance(rates, dict):
            raise MarketDataError("latest response has no rates")
        return {
            "gold_usd": _inverse(rates, "XAU"),
            "silver_usd": _inverse(rates, "XAG"),
            "usd_cad": _rate(rates, "CAD"),
            "usd_inr": _rate(rates, "INR"),
            "as_of": int(payload.get("timestamp") or time.time()),
        }

    def history(self, days: int = HISTORY_DAYS) -> list[dict[str, float | str]]:
        end = datetime.now(UTC).date()
        start = end - timedelta(days=days)
        payload = self._get(
            "timeframe",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            currencies="XAU,XAG",
        )
        by_date = payload.get("rates")
        if not isinstance(by_date, dict) or not by_date:
            raise MarketDataError("timeframe response has no rates")

        points = []
        for day, rates in sorted(by_date.items()):
            try:
                date.fromisoformat(day)
                gold, silver = _inverse(rates, "XAU"), _inverse(rates, "XAG")
            except (MarketDataError, TypeError, ValueError):
                continue  # Markets close on some days; skip gaps rather than invent values.
            points.append({"date": day, "gold": gold, "silver": silver, "ratio": gold / silver})
        if not points:
            raise MarketDataError("timeframe response has no usable points")
        return points


class JsonFileStore:
    """Small key/value store that survives restarts, so restarts cost no API quota."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable cache file at %s", self._path)
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._data), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            logger.warning("Could not persist cache to %s", self._path)


class MarketService:
    """Serves market data from cache and refreshes it only when due.

    If the upstream fails, the last good value is returned and marked stale, and
    retries are spaced out so an outage cannot burn through the request quota.
    """

    def __init__(
        self,
        client: Any,
        store: Any,
        price_ttl: int,
        history_ttl: int,
        retry_delay: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._store = store
        self._price_ttl = price_ttl
        self._history_ttl = history_ttl
        self._retry_delay = retry_delay
        self._clock = clock
        self._lock = threading.Lock()
        self._retry_at: dict[str, float] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> MarketService:
        return cls(
            client=MetalPriceClient(settings.api_key, settings.request_timeout_seconds),
            store=JsonFileStore(settings.cache_path),
            price_ttl=settings.price_ttl_seconds,
            history_ttl=settings.history_ttl_seconds,
            retry_delay=settings.retry_delay_seconds,
        )

    def _fetch(self, key: str, ttl: int, load: Callable[[], Any]) -> tuple[Any, bool]:
        """Return ``(value, is_stale)``. The lock ensures one upstream call at a time."""
        with self._lock:
            now = self._clock()
            entry = self._store.get(key)
            if entry and now - entry["fetched_at"] < ttl:
                return entry["value"], False

            if now >= self._retry_at.get(key, 0):
                try:
                    value = load()
                except MarketDataError as exc:
                    logger.warning("Market data refresh failed (%s): %s", key, exc)
                    # A refusal will not change within minutes, so don't spend quota asking.
                    delay = ttl if isinstance(exc, ProviderRejectedError) else self._retry_delay
                    self._retry_at[key] = now + delay
                else:
                    self._store.set(key, {"fetched_at": now, "value": value})
                    return value, False

            if entry:
                return entry["value"], True
            raise MarketDataError(f"no {key} data available")

    def latest(self) -> Quote:
        value, stale = self._fetch("quote", self._price_ttl, self._client.latest)
        quote = Quote(**value, stale=stale)
        self._record(quote)
        return quote

    def _record(self, quote: Quote) -> None:
        """Keep one reading per day, giving a real history on plans without a history API."""
        day = datetime.fromtimestamp(quote.as_of, UTC).date().isoformat()
        with self._lock:
            days = dict(self._store.get(SNAPSHOTS_KEY) or {})
            if day in days:
                return
            days[day] = {"gold": quote.gold_usd, "silver": quote.silver_usd, "ratio": quote.ratio}
            kept = sorted(days)[-(HISTORY_DAYS + 1) :]
            self._store.set(SNAPSHOTS_KEY, {key: days[key] for key in kept})

    def history(self) -> History:
        try:
            points, stale = self._fetch("history", self._history_ttl, self._client.history)
        except MarketDataError:
            with self._lock:
                days = self._store.get(SNAPSHOTS_KEY) or {}
            if not days:
                raise
            return History([{"date": d, **days[d]} for d in sorted(days)], False, True)
        return History(points, stale, False)


def quote_to_json(quote: Quote) -> dict[str, Any]:
    """Shape a quote for the public API."""
    payload = asdict(quote)
    return {
        "source": SOURCE_NAME,
        "as_of": datetime.fromtimestamp(payload["as_of"], UTC).isoformat(),
        "stale": payload["stale"],
        "ratio": round(quote.ratio, 2),
        "prices": {
            currency: {
                "rate": round(quote.rate(currency), 4),
                **{
                    metal: {
                        "ounce": round(quote.price_per_ounce(metal, currency), 2),
                        "gram": round(quote.price_per_gram(metal, currency), 2),
                    }
                    for metal in ("gold", "silver")
                },
            }
            for currency in ("USD", "CAD", "INR")
        },
    }
