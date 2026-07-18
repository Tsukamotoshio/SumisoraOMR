# core/app/ssl_setup.py — Route SSL verification through the OS trust store.
"""System trust store integration for SSL certificate verification.

Prefer the operating system certificate store (Windows / macOS keychain) via
``truststore`` so that corporate MITM proxies and locally-installed root CAs —
which are **absent from certifi's bundle** — validate correctly. This is the
root fix for the "SSL verification failed behind a MITM proxy" class of
download errors seen on some China-network machines.

Falls back to certifi (and the ``SSL_CERT_FILE`` env vars set at the packaged
entry points) when truststore is unavailable, so behaviour never regresses.

Idempotent and dependency-light: safe to call from every process entry point
(GUI shell, Flet shell, worker subprocess) as the very first network-affecting
step, before any HTTPS request creates an SSL context.
"""
from __future__ import annotations

# Module-level guard so repeated calls (or double entry via re-exec) are cheap
# and never inject twice.
_status: str | None = None


def setup_system_ssl() -> str:
    """Route Python SSL verification through the OS trust store if possible.

    Returns a short status string, useful for diagnostics/logging:

    * ``'truststore'`` — OS trust store active (preferred path);
    * ``'certifi'``    — truststore unavailable, relying on certifi / the
      ``SSL_CERT_FILE`` env fallback configured at the entry point.

    Call once, as early as possible in each process, before the first HTTPS
    request. Subsequent calls are no-ops that return the first result.
    """
    global _status
    if _status is not None:
        return _status
    try:
        import truststore  # noqa: PLC0415 — deferred: keep import cost off cold paths
        truststore.inject_into_ssl()
        _status = 'truststore'
    except Exception:
        # truststore requires Python 3.10+ and the package installed. On any
        # failure the existing certifi SSL_CERT_FILE env setup stays in effect.
        _status = 'certifi'
    return _status
