# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Commands

```bash
# Run the proxy server
python src/proxy_app/main.py

# Run with options
python src/proxy_app/main.py --host 0.0.0.0 --port 8000
python src/proxy_app/main.py --enable-request-logging
python src/proxy_app/main.py --enable-raw-logging
python src/proxy_app/main.py --add-credential   # Launch OAuth credential tool

# Install dependencies
pip install -r requirements.txt

# Install rotator_library in editable mode (done automatically via requirements.txt)
pip install -e src/rotator_library

# Docker
docker-compose up
```

No test suite is present in this repository.

## Architecture

This is a **Python/FastAPI reverse proxy** that accepts OpenAI-compatible API requests and forwards them to LLM providers using intelligent API key rotation and retry logic. It is split into two components with separate licenses:

### Two-Package Structure

- **`src/proxy_app/`** (MIT) — FastAPI server, CLI entrypoint, TUI launcher, request logging
- **`src/rotator_library/`** (LGPL-3.0) — Standalone Python library handling key rotation, retries, usage tracking, OAuth, and provider abstraction. Installed as a package via `pip install -e`.

### Request Flow

1. **`src/proxy_app/main.py`** — Entrypoint. Parses args, loads `.env` files from CWD, optionally launches `launcher_tui` (when run with no args), then starts uvicorn with the FastAPI app.
2. **FastAPI app** (`main.py`) — Defines `/v1/chat/completions`, `/v1/embeddings`, `/v1/models`, and passthrough routes. Validates requests against `PROXY_API_KEY` / `PROXY_API_KEY_N` env vars.
3. **`RotatingClient`** (`src/rotator_library/client.py`) — Core class. Receives a request, selects a provider+key using fair-cycle rotation, calls LiteLLM, handles errors, rotates keys on failure, retries up to `max_retries`.
4. **Provider plugins** (`src/rotator_library/providers/`) — Auto-discovered via `pkgutil`. Each implements `ProviderInterface`. Handles provider-specific auth, request transformation, and response parsing.
5. **`UsageManager`** — Tracks per-key token/request usage, persisted to disk.
6. **`CooldownManager`** — Places exhausted/failed keys into timed cooldown.
7. **`CredentialManager`** — Manages OAuth credentials (stored in `oauth/` dir).
8. **`BackgroundRefresher`** — Refreshes OAuth tokens before expiry.

### Provider Plugin System

Providers live in `src/rotator_library/providers/` and are auto-discovered at startup. Each file that defines a class implementing `ProviderInterface` is registered in `PROVIDER_PLUGINS` dict (keyed by provider name).

Special providers:
- **`openai_compatible_provider.py`** — Base for generic OpenAI-compatible endpoints; also used dynamically for any `<NAME>_API_BASE` env var not matching a known LiteLLM provider.
- **`gemini_cli_provider.py` / `antigravity_provider.py` / `qwen_code_provider.py`** — OAuth-based providers with their own auth base classes under `*_auth_base.py`.
- **`firmware_provider.py`** — Internal quota-tracking virtual provider (`firmware/_quota` model name).

### Key Configuration (`.env`)

- `PROXY_API_KEY` — Single key for backward compatibility (insecure if unset)
- `PROXY_API_KEY_1`, `PROXY_API_KEY_2`, ... — Multiple proxy auth keys; any one is accepted. Can be combined with `PROXY_API_KEY`
- `<PROVIDER>_API_KEY` / `<PROVIDER>_API_KEYS` — Comma-separated keys per provider
- `<PROVIDER>_API_BASE` — Override API base URL for known providers, or define a custom OpenAI-compatible provider for unknown names
- Multiple `.env` files in CWD are loaded automatically (non-overriding)

### Adding a New Provider

1. Create `src/rotator_library/providers/<name>_provider.py` implementing `ProviderInterface`
2. The plugin is auto-discovered — no registration needed
3. For OAuth providers, extend the appropriate auth base class (`google_oauth_base.py`, `qwen_auth_base.py`, etc.)
