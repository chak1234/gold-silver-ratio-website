import pytest

from goldsilver.config import Settings
from goldsilver.market import History, MarketDataError, Quote
from goldsilver.web import MAX_QUESTION_LENGTH, MAX_REQUEST_BYTES, create_app


class FakeMarket:
    def __init__(self, quote=None, points=None):
        self.quote = quote
        self.points = points

    def latest(self):
        if self.quote is None:
            raise MarketDataError("down")
        return self.quote

    def history(self):
        if self.points is None:
            raise MarketDataError("down")
        return History(self.points, False, False, "Test source")


QUOTE = Quote(gold_usd=2000.0, silver_usd=25.0, usd_cad=1.4, usd_inr=88.0, as_of=1700000000)
POINTS = [{"date": "2026-01-01", "gold": 2000.0, "silver": 25.0, "ratio": 80.0}]


def client_for(market):
    return create_app(Settings(), market).test_client()


@pytest.fixture
def client():
    return client_for(FakeMarket(QUOTE, POINTS))


@pytest.fixture
def offline_client():
    return client_for(FakeMarket())


def test_index_has_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_prices_payload(client):
    response = client.get("/api/prices")
    data = response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    assert data["ratio"] == 80
    assert data["stale"] is False
    assert data["as_of"] == "2023-11-14T22:13:20+00:00"
    assert data["prices"]["USD"]["gold"]["ounce"] == 2000
    assert data["prices"]["INR"]["silver"]["gram"] == round(25 * 88 / 31.1035, 2)
    assert data["reference"]["long_run_average"] == 60


def test_history_payload(client):
    data = client.get("/api/history").get_json()
    assert data["points"] == POINTS
    assert data["source"] == "Test source"
    assert data["stale"] is False
    assert data["recorded"] is False


@pytest.mark.parametrize("path", ["/api/prices", "/api/history"])
def test_unavailable_data_returns_503_without_fake_values(offline_client, path):
    response = offline_client.get(path)
    assert response.status_code == 503
    data = response.get_json()
    assert data["error"]["code"] == "market_data_unavailable"
    assert "prices" not in data and "points" not in data


def test_chat(client):
    response = client.post("/api/chat", json={"question": "gold price"})
    assert response.status_code == 200
    assert response.get_json()["title"] == "Gold price"


def test_chat_works_when_offline(offline_client):
    response = offline_client.post("/api/chat", json={"question": "gold price"})
    assert response.get_json()["title"] == "Prices are unavailable right now"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": 7},
        {"question": "x" * (MAX_QUESTION_LENGTH + 1)},
        ["question"],
    ],
)
def test_chat_rejects_bad_input(client, body):
    assert client.post("/api/chat", json=body).status_code == 400


def test_chat_rejects_oversized_body(client):
    response = client.post(
        "/api/chat", data="x" * (MAX_REQUEST_BYTES + 1), content_type="application/json"
    )
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "request_entity_too_large"


def test_unknown_api_route_is_json(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_health(client):
    assert client.get("/api/health").get_json()["status"] == "ok"
