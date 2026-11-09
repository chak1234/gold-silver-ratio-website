import json

import pytest
import requests
import responses

from goldsilver.market import (
    GRAMS_PER_TROY_OUNCE,
    JsonFileStore,
    MarketDataError,
    MarketService,
    MetalPriceClient,
    ProviderRejectedError,
    Quote,
)

LATEST_URL = "https://api.metalpriceapi.com/v1/latest"
TIMEFRAME_URL = "https://api.metalpriceapi.com/v1/timeframe"
RATES = {"XAU": 0.0005, "XAG": 0.04, "CAD": 1.4, "INR": 88.0}
VALUE = {"gold_usd": 2000.0, "silver_usd": 25.0, "usd_cad": 1.4, "usd_inr": 88.0, "as_of": 1}


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def latest(self):
        self.calls += 1
        if self.fail:
            raise MarketDataError("down")
        return dict(VALUE)

    def history(self):
        return [{"date": "2026-01-01", "gold": 2000.0, "silver": 25.0, "ratio": 80.0}]


class MemoryStore:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def service():
    client, clock = FakeClient(), Clock()
    market = MarketService(
        client, MemoryStore(), price_ttl=100, history_ttl=100, retry_delay=50, clock=clock
    )
    return market, client, clock


@responses.activate
def test_latest_inverts_rates_and_sends_key_only_in_header():
    responses.get(LATEST_URL, json={"success": True, "timestamp": 1700000000, "rates": RATES})

    value = MetalPriceClient("secret").latest()

    assert value["gold_usd"] == pytest.approx(2000)
    assert value["silver_usd"] == pytest.approx(25)
    assert value["usd_cad"] == pytest.approx(1.4)
    assert value["usd_inr"] == pytest.approx(88)
    assert value["as_of"] == 1700000000
    call = responses.calls[0].request
    assert call.headers["X-API-KEY"] == "secret"
    assert "secret" not in call.url


@responses.activate
def test_provider_error_is_reported_by_code_only():
    responses.get(
        LATEST_URL,
        json={"success": False, "error": {"code": 105, "info": "limit reached"}},
        status=429,
    )
    with pytest.raises(MarketDataError, match="105"):
        MetalPriceClient("secret").latest()


@responses.activate
def test_network_failure_does_not_leak_key():
    responses.get(LATEST_URL, body=requests.ConnectionError("boom with secret"))
    with pytest.raises(MarketDataError) as caught:
        MetalPriceClient("secret").latest()
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("rates", [{"XAU": 0, "XAG": 0.04, "CAD": 1, "INR": 1}, {"XAU": 0.001}])
@responses.activate
def test_invalid_rates_are_rejected(rates):
    responses.get(LATEST_URL, json={"success": True, "rates": rates})
    with pytest.raises(MarketDataError):
        MetalPriceClient("secret").latest()


def test_missing_key_makes_no_request():
    with pytest.raises(MarketDataError, match="not configured"):
        MetalPriceClient("").latest()


@responses.activate
def test_history_is_sorted_and_skips_bad_days():
    responses.get(
        TIMEFRAME_URL,
        json={
            "success": True,
            "rates": {
                "2026-01-03": {"XAU": 0.0005, "XAG": 0.05},
                "2026-01-01": {"XAU": 0.0005, "XAG": 0.04},
                "2026-01-02": {"XAU": None, "XAG": 0.04},
            },
        },
    )
    points = MetalPriceClient("secret").history()
    assert [p["date"] for p in points] == ["2026-01-01", "2026-01-03"]
    assert points[0]["ratio"] == pytest.approx(80)
    assert points[1]["ratio"] == pytest.approx(100)


def test_quote_conversions():
    quote = Quote(**VALUE)
    assert quote.ratio == 80
    assert quote.price_per_ounce("gold", "INR") == pytest.approx(176_000)
    assert quote.price_per_gram("silver", "CAD") == pytest.approx(35 / GRAMS_PER_TROY_OUNCE)


def test_cache_prevents_repeat_calls_until_expiry(service):
    market, client, clock = service
    market.latest()
    clock.now += 99
    market.latest()
    assert client.calls == 1
    clock.now += 1
    market.latest()
    assert client.calls == 2


def test_failure_serves_stale_value_and_backs_off(service):
    market, client, clock = service
    market.latest()
    client.fail = True
    clock.now += 100

    assert market.latest().stale is True
    assert client.calls == 2
    assert market.latest().stale is True
    assert client.calls == 2  # Within the retry delay, so no extra upstream call.

    clock.now += 50
    market.latest()
    assert client.calls == 3


def test_failure_with_no_cached_value_raises(service):
    market, client, _clock = service
    client.fail = True
    with pytest.raises(MarketDataError):
        market.latest()


class RejectingHistoryClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.history_calls = 0

    def history(self):
        self.history_calls += 1
        raise ProviderRejectedError("plan")


def test_rejected_history_falls_back_to_recorded_readings():
    client = RejectingHistoryClient()
    market = MarketService(client, MemoryStore(), price_ttl=100, history_ttl=100, clock=Clock())

    with pytest.raises(MarketDataError):
        market.history()

    market.latest()
    result = market.history()
    assert result.recorded is True
    assert result.points == [
        {"date": "1970-01-01", "gold": 2000.0, "silver": 25.0, "ratio": 80.0},
    ]


def test_rejected_request_waits_full_ttl_before_retrying():
    client, clock = RejectingHistoryClient(), Clock()
    market = MarketService(
        client, MemoryStore(), price_ttl=1000, history_ttl=1000, retry_delay=50, clock=clock
    )
    market.latest()
    market.history()
    clock.now += 999
    market.history()
    assert client.history_calls == 1
    clock.now += 1
    market.history()
    assert client.history_calls == 2


def test_one_reading_is_recorded_per_day(service):
    market, client, clock = service
    market.latest()
    clock.now += 100
    market.latest()
    assert client.calls == 2
    assert len(market._store.get("snapshots")) == 1


def test_file_store_survives_restart(tmp_path):
    path = tmp_path / "nested" / "cache.json"
    JsonFileStore(path).set("quote", {"fetched_at": 1, "value": {"a": 1}})
    assert JsonFileStore(path).get("quote") == {"fetched_at": 1, "value": {"a": 1}}
    assert "key" not in json.loads(path.read_text(encoding="utf-8"))


def test_file_store_ignores_corrupt_file(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not json", encoding="utf-8")
    assert JsonFileStore(path).get("quote") is None
