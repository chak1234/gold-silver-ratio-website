# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through this repository's
[GitHub security advisories](../../security/advisories/new) rather than in a public issue.
Include steps to reproduce and the affected version.

## How this project handles secrets

- The optional Gold API key is read from the server environment and is never sent to the browser.
- The key is sent to the provider in a request header, so it stays out of URLs and logs.
- `.env` is excluded from version control. If a key is ever exposed, rotate it with the provider.

## Deployment checklist

- Serve the application over HTTPS, behind a reverse proxy or platform that terminates TLS.
- Never use `--debug` in production. Flask's debugger can execute arbitrary code.
- Add rate limiting at the proxy if the site is public.
