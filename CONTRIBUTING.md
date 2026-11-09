# Contributing

## Set up

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env             # then add your API key
python -m goldsilver --debug
```

## Before opening a pull request

```bash
ruff check .
ruff format .
pytest
```

Tests never call the real provider, so they need no API key and use no quota.

## Ground rules

- Keep API keys on the server. Browser code talks only to this application's `/api/*` routes.
- Never display invented market data. If data is missing, say so in the interface.
- Build page content with `textContent`, not `innerHTML`. The Content Security Policy forbids inline scripts.
- Any change to caching must keep the defaults within the provider's free-plan quota.
