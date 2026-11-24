# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in **Health By**, please report it privately rather than opening a public issue or pull request:

1. Use [GitHub Security Advisories](../../security/advisories/new) on this repository to submit a private report.
2. Include the affected endpoint, module, reproduction steps, and impact assessment.

Please avoid posting sensitive details, exploit payloads, OAuth secrets, or health exports publicly until a fix is available.

## Supported Versions

Health By is currently in pre-1.0 (`0.0.x`) development. Only the latest `0.0.x` release and `main` receive security fixes.

## Self-Hosted Security Model & Boundaries

- **Local-first binding**: The web server binds to `127.0.0.1:39441` by default and checks the HTTP `Host` header against loopback addresses and `HEALTH_BY_ALLOWED_HOSTS`. Never expose the service directly to the public internet without a reverse proxy, TLS, and authentication.
- **CSRF & browser headers**: State-changing routes and `/oauth/start` enforce same-origin `Origin` / `Sec-Fetch-Site` / `Referer` checks, and responses emit `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and `Cache-Control: no-store` on API and HTML page routes (`Cache-Control: no-cache` on `/static/*`).
- **Credential & database protection**: OAuth secrets, refresh tokens, and optional BYOK AI API keys are encrypted at rest using Fernet (`SecretStore`). By default, the database and `secret.key` are created with `0600` permissions; production containers may inject an external key via `HEALTH_BY_SECRET_KEY` or `HEALTH_BY_SECRET_KEY_PATH`.
- **Optional BYOK AI egress**: Disabled by default (`ai_enabled=false`). Non-loopback endpoints with an API key require `HTTPS`, link-local/metadata/unspecified addresses are blocked after DNS resolution, and only aggregated statistics leave the host on explicit user action.
- **Non-medical scope**: Health By is a personal data self-check tool, not a medical device or clinical diagnostic service.

## Known Design Boundaries & Non-Goals (Single-User Scope)

Health By is architected as a **single-user, local-first, self-hosted** application. Several hardening measures that are standard in multi-tenant cloud services are intentionally deferred and documented here as explicit design boundaries (not active vulnerabilities under the default single-user threat model):

1. **Outbound AI DNS rebinding (TOCTOU)**: `health_by/ai/client.py` resolves and validates target hostnames immediately before opening the HTTP connection, but does not pin the resolved IP at the transport socket layer (which would require a custom `httpcore` transport with manual TLS SNI/certificate-hostname plumbing). In a single-user BYOK deployment where the owner configures `ai_base_url` directly, this complexity is deferred unless multi-tenant or untrusted-URL support is ever introduced.
2. **Default-port `Origin` scheme comparison**: `health_by/api/security.py` validates `Origin` host/port alongside `Sec-Fetch-Site`. On default HTTP/HTTPS ports (80/443), scheme equality is not separately compared in `_same_authority`, as modern browsers already enforce `Sec-Fetch-Site: cross-site` on cross-scheme requests. Tightening explicit scheme matching remains a low-priority defense-in-depth option.
3. **Switching Google accounts inside the same `data/` directory**: The SQLite schema assumes one personal archive per `data/` directory (`days.date` is primary key and same-source sync merges by source name `google-health`). Users switching Google accounts should use a separate `data/` directory or clear existing data first. Recording a hashed remote account identifier in `meta` to warn on mismatch is tracked as a future convenience guard.
4. **Concurrent AI settings save vs. in-flight analysis startup**: In `health_by/ai/service.py`, `analyze()` reads `settings` and encrypted `credentials` in separate SQLite queries before opening the outbound request. Because only the local owner triggers both actions, reading both in one explicit read-snapshot transaction is tracked as a low-priority future cleanup.
5. **Transitive `opentelemetry-api` dependency**: FastAPI pulls in `opentelemetry-api` transitively, though Health By does not import, configure, or enable OpenTelemetry tracing middleware. Dependency versions are kept current via `requirements-lock.txt` and Dependabot.
6. **Simulated DOM unit tests vs. full browser layout verification**: Fast local tests (`tests/test_web_render.py`, `tests/test_e2e_smoke.py`) pair a Node DOM harness with static HTML ID verification (`scripts/check_web_refs.py`), complemented by real Chromium smoke coverage in CI (`tests/test_browser_e2e.py`). Pixel-level visual screenshot diffing across viewports remains a future non-blocking enhancement.

See also [Architecture — Known Trade-offs & Deferred Enhancements](docs/architecture.md#known-trade-offs--deferred-enhancements) ([中文](docs/zh/architecture.md#已知权衡与后续可选项)).
