import json

import pytest
import requests
import responses
from responses import matchers

from goldsilver.market import (
    BACKUP_HISTORY_SOURCE,
    GRAMS_PER_TROY_OUNCE,
    SOURCE_NAME,
    GoldApiClient,
    JsonFileStore,
    MarketDataClient,
    MarketDataError,
    MarketService,
    ProviderRejectedError,
    Quote,
    YahooFinanceClient,
)

GOLD_URL = "https://api.gold-api.com/price/XAU/CAD"
SILVER_URL = "https://api.gold-api.com/price/XAG/INR"
HISTORY_URL = "https://api.gold-api.com/history"
YAHOO_GOLD_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
YAHOO_SILVER_URL = "https://query1.finance.yahoo.com/v8/finance/chart/SI=F"
VALUE = {"gold_usd": 2000.0, "silver_usd": 25.0, "usd_cad": 1.4, "usd_inr": 88.0, "as_of": 1}
GOLD = {"price": 2800.0, "exchangeRate": 1.4, "updatedAt": "2023-11-14T22:13:25Z"}
SILVER = {"price": 2200.0, "exchangeRate": 88.0, "updatedAt": "2023-11-14T22:13:20Z"}
DAY_ONE = 1767243600  # 2026-01-01 05:00 UTC
DAY_TWO = DAY_ONE + 86_400


def yahoo_payload(timestamps, closes):
    quote = {"close": closes}
    return {
        "chart": {"result": [{"timestamp": timestamps, "indicators": {"quote": [quote]}}]},
    }


def gold_history(*rows, symbol="XAU"):
    responses.get(
        HISTORY_URL,
        json=list(rows),
        match=[matchers.query_param_matcher({"symbol": symbol}, strict_match=False)],
    )


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
        points = [{"date": "2026-01-01", "gold": 2000.0, "silver": 25.0, "ratio": 80.0}]
        return {"source": "Test source", "points": points}


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
def test_latest_uses_two_keyless_requests():
    responses.get(GOLD_URL, json=GOLD)
    responses.get(SILVER_URL, json=SILVER)

    value = GoldApiClient().latest()

    assert value["gold_usd"] == pytest.approx(2000)
    assert value["silver_usd"] == pytest.approx(25)
    assert value["usd_cad"] == pytest.approx(1.4)
    assert value["usd_inr"] == pytest.approx(88)
    assert value["as_of"] == 1700000000  # The older of the two readings.
    assert len(responses.calls) == 2
    assert "x-api-key" not in responses.calls[0].request.headers


@responses.activate
def test_client_error_counts_as_rejected():
    responses.get(GOLD_URL, json={"error": "banned"}, status=429)
    with pytest.raises(ProviderRejectedError, match="429"):
        GoldApiClient().latest()


@responses.activate
def test_server_error_is_retryable():
    responses.get(GOLD_URL, status=503)
    with pytest.raises(MarketDataError) as caught:
        GoldApiClient().latest()
    assert not isinstance(caught.value, ProviderRejectedError)


@responses.activate
def test_network_failure_does_not_leak_key():
    responses.get(HISTORY_URL, body=requests.ConnectionError("boom with secret"))
    with pytest.raises(MarketDataError) as caught:
        GoldApiClient("secret").history()
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "gold",
    [
        {**GOLD, "price": 0},
        {**GOLD, "exchangeRate": None},
        {**GOLD, "updatedAt": "soon"},
        {"price": 1},
    ],
)
@responses.activate
def test_invalid_prices_are_rejected(gold):
    responses.get(GOLD_URL, json=gold)
    with pytest.raises(MarketDataError):
        GoldApiClient().latest()


def test_history_without_key_makes_no_request():
    with pytest.raises(MarketDataError, match="not configured"):
        GoldApiClient("").history()


@responses.activate
def test_history_merges_metals_skips_bad_days_and_sends_key_only_in_header():
    gold_history(
        {"day": "2026-01-03", "avg_price": 2000},
        {"day": "2026-01-02", "avg_price": None},
        {"day": "2026-01-01", "avg_price": 2000},
    )
    gold_history(
        {"day": "2026-01-01", "avg_price": 25},
        {"day": "2026-01-02", "avg_price": 25},
        {"day": "2026-01-03", "avg_price": 20},
        symbol="XAG",
    )

    points = GoldApiClient("secret").history()

    assert [p["date"] for p in points] == ["2026-01-01", "2026-01-03"]
    assert points[0]["ratio"] == pytest.approx(80)
    assert points[1]["ratio"] == pytest.approx(100)
    call = responses.calls[0].request
    assert call.headers["x-api-key"] == "secret"
    assert "secret" not in call.url
    assert "groupBy=day" in call.url and "aggregation=avg" in call.url


@responses.activate
def test_yahoo_history_aligns_days_and_skips_gaps():
    responses.get(YAHOO_GOLD_URL, json=yahoo_payload([DAY_ONE, DAY_TWO], [2000.0, None]))
    responses.get(YAHOO_SILVER_URL, json=yahoo_payload([DAY_ONE, DAY_TWO], [25.0, 20.0]))

    points = YahooFinanceClient().history()

    assert points == [{"date": "2026-01-01", "gold": 2000.0, "silver": 25.0, "ratio": 80.0}]
    assert responses.calls[0].request.headers["User-Agent"] == YahooFinanceClient.USER_AGENT


@responses.activate
def test_yahoo_error_payload_is_reported():
    responses.get(YAHOO_GOLD_URL, json={"chart": {"result": None, "error": {"code": "x"}}})
    with pytest.raises(MarketDataError):
        YahooFinanceClient().history()


class StubPrimary:
    def __init__(self, has_key, error=None):
        self.has_key = has_key
        self.error = error
        self.calls = 0

    def history(self):
        self.calls += 1
        if self.error:
            raise self.error
        return ["primary"]


class StubBackup:
    def history(self):
        return ["backup"]


def test_history_prefers_gold_api_when_key_is_set():
    client = MarketDataClient(StubPrimary(True), StubBackup())
    assert client.history() == {"source": SOURCE_NAME, "points": ["primary"]}


def test_history_uses_backup_without_a_key():
    primary = StubPrimary(False)
    client = MarketDataClient(primary, StubBackup())
    assert client.history() == {"source": BACKUP_HISTORY_SOURCE, "points": ["backup"]}
    assert primary.calls == 0


def test_history_uses_backup_when_gold_api_fails():
    client = MarketDataClient(StubPrimary(True, MarketDataError("down")), StubBackup())
    assert client.history()["source"] == BACKUP_HISTORY_SOURCE


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


def test_rejection_waits_at_least_the_retry_delay_when_ttl_is_short():
    class Rejected(FakeClient):
        def latest(self):
            self.calls += 1
            raise ProviderRejectedError("banned")

    client, clock = Rejected(), Clock()
    market = MarketService(
        client, MemoryStore(), price_ttl=60, history_ttl=1000, retry_delay=300, clock=clock
    )
    with pytest.raises(MarketDataError):
        market.latest()
    clock.now += 299
    with pytest.raises(MarketDataError):
        market.latest()
    assert client.calls == 1
    clock.now += 1
    with pytest.raises(MarketDataError):
        market.latest()
    assert client.calls == 2


def test_history_reports_its_source(service):
    market, _client, _clock = service
    assert market.history().source == "Test source"


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
