"""Market data: the Gold API and Yahoo Finance clients and a persistent cache."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NamedTuple

import requests

from .config import Settings

logger = logging.getLogger(__name__)

GRAMS_PER_TROY_OUNCE = 31.1035
HISTORY_DAYS = 365
SOURCE_NAME = "Gold API"
BACKUP_HISTORY_SOURCE = "Yahoo Finance (COMEX futures)"
HISTORY_KEY = "daily_history"
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
    source: str = SOURCE_NAME


def _get_json(
    url: str,
    label: str,
    timeout: float,
    params: dict[str, str | int] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise MarketDataError(f"{label} request failed: {type(exc).__name__}") from None
    if 400 <= response.status_code < 500:
        raise ProviderRejectedError(f"{label} rejected with HTTP {response.status_code}")
    if response.status_code != 200:
        raise MarketDataError(f"{label} failed with HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise MarketDataError(f"{label} returned invalid JSON") from None


def _positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MarketDataError(f"response has no usable {label}") from None
    if not number > 0:
        raise MarketDataError(f"response has a non-positive {label}")
    return number


def _merge_daily(gold: dict[str, float], silver: dict[str, float]) -> list[dict[str, float | str]]:
    """Keep only days with both prices, skipping gaps rather than inventing values."""
    points: list[dict[str, float | str]] = [
        {"date": day, "gold": gold[day], "silver": silver[day], "ratio": gold[day] / silver[day]}
        for day in sorted(gold.keys() & silver.keys())
    ]
    if not points:
        raise MarketDataError("history response has no usable points")
    return points


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


class GoldApiClient:
    """Client for https://gold-api.com.

    Live prices need no key. The optional history key travels in a header so it
    cannot end up in URLs, logs or tracebacks.
    """

    BASE_URL = "https://api.gold-api.com"

    def __init__(self, api_key: str = "", timeout: float = 10) -> None:
        self._api_key = api_key
        self._timeout = timeout

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    def _price(self, symbol: str, currency: str) -> tuple[float, float, int]:
        """Return ``(USD price, USD exchange rate, updated-at timestamp)``."""
        payload = _get_json(f"{self.BASE_URL}/price/{symbol}/{currency}", "price", self._timeout)
        if not isinstance(payload, dict):
            raise MarketDataError("price response has an unexpected shape")
        rate = _positive(payload.get("exchangeRate"), f"{currency} rate")
        price = _positive(payload.get("price"), f"{symbol} price")
        try:
            updated = int(datetime.fromisoformat(payload["updatedAt"]).timestamp())
        except (KeyError, TypeError, ValueError):
            raise MarketDataError("price response has no usable timestamp") from None
        return price / rate, rate, updated

    def latest(self) -> dict[str, float | int]:
        # Each response also carries an exchange rate, so two requests cover both
        # metals and both currencies. The provider bans clients that send bursts.
        gold_usd, usd_cad, gold_updated = self._price("XAU", "CAD")
        silver_usd, usd_inr, silver_updated = self._price("XAG", "INR")
        return {
            "gold_usd": gold_usd,
            "silver_usd": silver_usd,
            "usd_cad": usd_cad,
            "usd_inr": usd_inr,
            "as_of": min(gold_updated, silver_updated),
        }

    def _daily_average(self, symbol: str, start: int, end: int) -> dict[str, float]:
        payload = _get_json(
            f"{self.BASE_URL}/history",
            "history",
            self._timeout,
            params={
                "symbol": symbol,
                "startTimestamp": start,
                "endTimestamp": end,
                "groupBy": "day",
                "aggregation": "avg",
                "orderBy": "asc",
            },
            headers={"x-api-key": self._api_key},
        )
        if not isinstance(payload, list):
            raise MarketDataError("history response has an unexpected shape")
        prices = {}
        for row in payload:
            try:
                day = str(row["day"])[:10]
                date.fromisoformat(day)
                prices[day] = _positive(row["avg_price"], f"{symbol} price")
            except (KeyError, TypeError, ValueError, MarketDataError):
                continue
        return prices

    def history(self, days: int = HISTORY_DAYS) -> list[dict[str, float | str]]:
        if not self._api_key:
            raise MarketDataError("GOLD_API_KEY is not configured")
        end = int(time.time())
        start = end - days * 86_400
        return _merge_daily(
            self._daily_average("XAU", start, end),
            self._daily_average("XAG", start, end),
        )


class YahooFinanceClient:
    """Backup daily history from Yahoo's unofficial chart endpoint (COMEX futures)."""

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    USER_AGENT = "GoldSilverRatioTracker/1.0"

    def __init__(self, timeout: float = 10) -> None:
        self._timeout = timeout

    def _daily_close(self, symbol: str) -> dict[str, float]:
        payload = _get_json(
            f"{self.BASE_URL}/{symbol}",
            "backup history",
            self._timeout,
            params={"range": "1y", "interval": "1d"},
            headers={"User-Agent": self.USER_AGENT},
        )
        try:
            result = payload["chart"]["result"][0]
            timestamps = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]
        except (KeyError, IndexError, TypeError):
            raise MarketDataError("backup history response has an unexpected shape") from None

        prices = {}
        for timestamp, close in zip(timestamps, closes, strict=False):
            try:
                day = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
                prices[day] = _positive(close, f"{symbol} close")
            except (TypeError, ValueError, OverflowError, OSError, MarketDataError):
                continue
        return prices

    def history(self) -> list[dict[str, float | str]]:
        return _merge_daily(self._daily_close("GC=F"), self._daily_close("SI=F"))


class MarketDataClient:
    """Gold API for live prices, with Yahoo Finance as the backup for history."""

    def __init__(self, primary: GoldApiClient, backup: YahooFinanceClient) -> None:
        self._primary = primary
        self._backup = backup

    def latest(self) -> dict[str, float | int]:
        return self._primary.latest()

    def history(self) -> dict[str, Any]:
        if self._primary.has_key:
            try:
                return {"source": SOURCE_NAME, "points": self._primary.history()}
            except MarketDataError as exc:
                logger.warning("Gold API history failed, trying the backup: %s", exc)
        return {"source": BACKUP_HISTORY_SOURCE, "points": self._backup.history()}


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
            client=MarketDataClient(
                GoldApiClient(settings.gold_api_key, settings.request_timeout_seconds),
                YahooFinanceClient(settings.request_timeout_seconds),
            ),
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
                    rejected = isinstance(exc, ProviderRejectedError)
                    delay = max(ttl, self._retry_delay) if rejected else self._retry_delay
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
            value, stale = self._fetch(HISTORY_KEY, self._history_ttl, self._client.history)
        except MarketDataError:
            with self._lock:
                days = self._store.get(SNAPSHOTS_KEY) or {}
            if not days:
                raise
            return History([{"date": d, **days[d]} for d in sorted(days)], False, True)
        return History(value["points"], stale, False, value["source"])


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
