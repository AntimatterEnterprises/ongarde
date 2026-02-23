"""Programmatic uvicorn entry point for OnGarde — E-001-S-005.

Reads host and port from the loaded config (127.0.0.1:4242 by default) and starts
uvicorn with the hardened defaults mandated by architecture.md §9.3:

  --limit-concurrency 100  Max 100 concurrent connections; HTTP 503 when exceeded
  --backlog 50             OS connection queue depth; limits SYN flood exposure
  --timeout-keep-alive 5   Reduces Slow Loris attack window (default uvicorn = 5)

Usage:
    python -m app.run          # production (reads .ongarde/config.yaml)
    ongarde                    # via pyproject.toml [project.scripts]

The binding host is sourced from config.proxy.host (default: "127.0.0.1").
Configuring proxy.host: "0.0.0.0" is allowed but logs a SECURITY WARNING at startup
(see app/config.py:load_config and story AC-E001-05).

Architecture reference: architecture.md §9.3
Story: E-001-S-005
"""

from __future__ import annotations

import uvicorn

from app.config import load_config

# ─── Uvicorn hardened defaults (architecture.md §9.3) ────────────────────────
# These values are non-negotiable. Do not weaken them without an architecture review.

# Maximum number of concurrent connections accepted by uvicorn.
# New connections receive HTTP 503 when this limit is exceeded.
# Must match httpx connection pool size (POOL_MAX_CONNECTIONS = 100 in engine.py).
UVICORN_LIMIT_CONCURRENCY: int = 100

# OS-level TCP connection backlog queue size.
# Limits the number of pending connections waiting to be accepted.
UVICORN_BACKLOG: int = 50

# HTTP keep-alive timeout in seconds.
# Low value reduces the Slow Loris attack window.
UVICORN_TIMEOUT_KEEP_ALIVE: int = 5


def main() -> None:
    """Start the OnGarde proxy server with hardened uvicorn defaults.

    Loads config first to read the binding host and port.  All other uvicorn
    settings use the hardened defaults defined above.

    Raises:
        SystemExit: Propagated from load_config() on config parse errors.
    """
    config = load_config()

    uvicorn.run(
        "app.main:app",
        host=config.proxy.host,
        port=config.proxy.port,
        limit_concurrency=UVICORN_LIMIT_CONCURRENCY,
        backlog=UVICORN_BACKLOG,
        timeout_keep_alive=UVICORN_TIMEOUT_KEEP_ALIVE,
    )


if __name__ == "__main__":
    main()
