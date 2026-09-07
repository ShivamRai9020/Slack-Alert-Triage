"""
Simple API-key auth for endpoints that can mutate state (create Jira
tickets, rewrite routing config, resolve incidents) or read data an
unauthenticated caller shouldn't see (channel mappings, tracked
incidents). This is intentionally minimal -- a single shared key via the
`X-API-Key` header, not per-user auth -- appropriate for an internal
service-to-service boundary, not a substitute for real RBAC if this ever
grows multiple distinct callers who need different permissions.

If INTERNAL_API_KEY is unset, auth is a no-op (open) -- fine for local
dev against a mock generator, dangerous anywhere reachable outside your
own machine. main.py logs a loud startup warning when this is the case.
"""
from fastapi import Header, HTTPException

from app.config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.internal_api_key:
        # No key configured -- auth disabled. Startup warning covers this.
        return

    if x_api_key != settings.internal_api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")
