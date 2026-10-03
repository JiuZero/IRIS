"""Who is calling the API, and whose emulations they are allowed to touch.

Three facts made this module necessary rather than decorative:

* ``iris serve start`` defaulted its bind address to ``0.0.0.0``, so every route
  below was reachable from the whole LAN even though ``README.md`` documented the
  docs URL as ``http://127.0.0.1:9000/docs`` -- the documented safety was not in
  the code.
* Nothing checked a caller. ``DELETE /api/v1/emulate/{iid}`` stopped whichever
  container the iid named, so two callers sharing an iid could each kill the
  other's run.
* ``/api/v1/pipeline`` read the upload with ``firmware.read()``, pulling an
  unbounded body into memory before any limit applied.

The ownership model is deliberately small. A caller is identified by a stable
*client id* derived from the token it presented -- never the token itself, so a
row written to the database cannot be replayed by anyone who reads it. When no
token is configured the server is in local mode: every caller is the single
``LOCAL_CLIENT``, because the only thing that can reach it is loopback. That
invariant is enforced at startup in :func:`iris.cli.serve_start`, which refuses
to bind a non-loopback address without a token -- so "no token" and "exposed to
the network" can never be true at the same time, and this module can trust the
second half of that sentence instead of re-checking it on every request.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from iris.config import Settings, get_settings

__all__ = [
    "CLIENT_HEADER",
    "LOCAL_CLIENT",
    "MAX_HEADER_TOKEN",
    "client_id_of",
    "is_loopback_host",
    "token_presented",
]

#: Alternative to ``Authorization: Bearer`` for clients that cannot set headers
#: (curl on some shells, minimal HTTP clients). Both are accepted because a
#: product that only accepts one spelling gets reported as "no way to call it".
CLIENT_HEADER = "X-IRIS-Token"

#: Identity used when no token is configured. Loopback-only by the startup gate,
#: so a single shared name is accurate rather than a hole.
LOCAL_CLIENT = "local"

#: An unbounded header is a free way to make every request allocate whatever the
#: client felt like sending. 4 KiB is far above any real token.
MAX_HEADER_TOKEN = 4096


def configured_token(settings: Settings | None = None) -> str:
    """The API token, or ``""`` when the server runs without one."""
    return (settings or get_settings()).api_token.strip()


def is_loopback_host(host: str) -> bool:
    """Whether ``host`` keeps the server on this machine.

    ``localhost`` is resolved rather than pattern-matched because it can be
    pointed at a non-loopback address via ``/etc/hosts``; the address that
    actually gets bound is the question, and for the wildcard the answer is
    always no.
    """
    candidate = host.strip().strip("[]").lower()
    if not candidate:
        return False
    if candidate in {"localhost", "0.0.0.0", "::", "*"}:
        return candidate == "localhost"
    try:
        import ipaddress

        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def token_presented(
    authorization: str | None,
    header_token: str | None,
) -> str:
    """Pull the token out of either accepted spelling, or ``""`` for none."""
    if header_token:
        candidate = header_token.strip()
    elif authorization:
        candidate = authorization.strip()
        if candidate.lower().startswith("bearer "):
            candidate = candidate[7:].strip()
    else:
        candidate = ""
    if len(candidate) > MAX_HEADER_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token too long",
        )
    return candidate


def client_id_of(token: str) -> str:
    """A stable, non-replayable identity for a token.

    Hashed rather than stored so that reading the database cannot leak the
    credential itself; truncated to 16 hex chars because this is a *label* used
    to compare ownership, not a secret, and a 64-char column would invite
    someone to treat it as one.
    """
    if not token:
        return LOCAL_CLIENT
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def require_client(
    authorization: Annotated[str | None, Header()] = None,
    header_token: Annotated[str | None, Header(alias=CLIENT_HEADER)] = None,
) -> str:
    """FastAPI dependency: the caller's client id, or 401.

    Compares in constant time so a wrong token cannot be narrowed down one
    character at a time. Returns the client id on success so handlers get
    ownership for free instead of re-parsing the header.
    """
    expected = configured_token()
    presented = token_presented(authorization, header_token)
    if not expected:
        # Local mode. The startup gate makes this unreachable from the network.
        return LOCAL_CLIENT
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API token",
        )
    return client_id_of(presented)