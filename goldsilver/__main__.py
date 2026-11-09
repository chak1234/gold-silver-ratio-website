"""Run the website: ``python -m goldsilver`` (add ``--debug`` for local development)."""

from __future__ import annotations

import argparse
import logging

from waitress import serve

from .config import Settings
from .web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Gold/Silver Ratio Tracker web server")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="use Flask's auto-reloading debug server, bound to localhost only",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    if not settings.api_key:
        logging.warning("METALPRICE_API_KEY is not set, so prices will be unavailable.")

    app = create_app(settings)
    if args.debug:
        # Flask's debugger can execute code, so it must never listen beyond localhost.
        app.run(host="127.0.0.1", port=settings.port, debug=True)
    else:
        logging.info("Serving on http://%s:%s", settings.host, settings.port)
        serve(app, host=settings.host, port=settings.port, threads=4)


if __name__ == "__main__":
    main()
