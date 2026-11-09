"""Flask application: the web page and its JSON API."""

from __future__ import annotations

from typing import Any

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from . import __version__
from .chatbot import (
    LONG_RUN_AVERAGE_RATIO,
    MODERN_HIGH_RATIO,
    MODERN_LOW_RATIO,
    respond,
)
from .config import Settings
from .market import SOURCE_NAME, MarketDataError, MarketService, Quote, quote_to_json

MAX_QUESTION_LENGTH = 500
MAX_REQUEST_BYTES = 16 * 1024

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def _api_error(status: int, code: str, message: str) -> tuple[Response, int]:
    return jsonify(error={"code": code, "message": message}), status


def create_app(settings: Settings | None = None, market: Any = None) -> Flask:
    settings = settings or Settings.from_env()
    market = market or MarketService.from_settings(settings)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

    def unavailable() -> tuple[Response, int]:
        return _api_error(503, "market_data_unavailable", "Market data is temporarily unavailable.")

    def latest_or_none() -> Quote | None:
        try:
            return market.latest()
        except MarketDataError:
            return None

    @app.get("/")
    def index() -> str:
        return render_template("index.html", version=__version__, source=SOURCE_NAME)

    @app.get("/api/prices")
    def prices() -> Response | tuple[Response, int]:
        quote = latest_or_none()
        if quote is None:
            return unavailable()
        return jsonify(
            **quote_to_json(quote),
            reference={
                "long_run_average": LONG_RUN_AVERAGE_RATIO,
                "modern_low": MODERN_LOW_RATIO,
                "modern_high": MODERN_HIGH_RATIO,
            },
        )

    @app.get("/api/history")
    def history() -> Response | tuple[Response, int]:
        try:
            result = market.history()
        except MarketDataError:
            return unavailable()
        return jsonify(
            source=result.source,
            stale=result.stale,
            recorded=result.recorded,
            points=result.points,
        )

    @app.post("/api/chat")
    def chat() -> Response | tuple[Response, int]:
        body = request.get_json(silent=True)
        question = body.get("question") if isinstance(body, dict) else None
        if not isinstance(question, str) or not question.strip():
            return _api_error(400, "invalid_question", "A question is required.")
        if len(question) > MAX_QUESTION_LENGTH:
            return _api_error(
                400,
                "invalid_question",
                f"Questions are limited to {MAX_QUESTION_LENGTH} characters.",
            )
        return jsonify(respond(question, latest_or_none()).to_dict())

    @app.get("/api/health")
    def health() -> Response:
        return jsonify(status="ok", version=__version__)

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> Response | tuple[Response, int] | HTTPException:
        if request.path.startswith("/api/"):
            return _api_error(error.code or 500, error.name.lower().replace(" ", "_"), error.name)
        return error

    @app.after_request
    def add_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app
