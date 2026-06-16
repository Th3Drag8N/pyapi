"""
AniList GraphQL client.
Rate-limit: 90 req/min.  Tracks 429 responses and short-circuits until back-off expires.
All queries are cached via the shared backend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from app.cache.backend import get_cached_value, get_client

logger = logging.getLogger("th3anime.clients.anilist")

ANILIST_API = "https://graphql.anilist.co"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Th3AnimeAPI/2.0)",
}

# Track rate-limit windows in monotonic seconds
_rate_limited_until: float = 0.0
_rate_lock = asyncio.Lock()


async def fetch_anilist(
    query: str,
    variables: dict | None = None,
    *,
    ttl_ms: int = 5 * 60 * 1000,
    refresh: bool = False,
) -> dict[str, Any]:
    variables = variables or {}
    cache_key = f"anilist:{json.dumps({'query': query, 'variables': variables}, sort_keys=True)}"

    async def loader() -> dict[str, Any]:
        global _rate_limited_until

        now = time.monotonic()
        if now < _rate_limited_until:
            wait_sec = _rate_limited_until - now
            raise RuntimeError(
                f"AniList rate-limited for another {wait_sec:.0f}s"
            )

        client: httpx.AsyncClient = get_client()
        try:
            resp = await asyncio.wait_for(
                client.post(
                    ANILIST_API,
                    headers=_HEADERS,
                    json={"query": query, "variables": variables},
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("AniList request timed out")

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            async with _rate_lock:
                _rate_limited_until = time.monotonic() + retry_after
            raise RuntimeError(f"AniList rate limit hit — backing off {retry_after}s")

        if resp.status_code != 200:
            raise RuntimeError(f"AniList request failed: {resp.status_code}")

        payload = resp.json()
        if errors := payload.get("errors"):
            raise RuntimeError(errors[0].get("message", "AniList request failed"))

        return payload["data"]

    return await get_cached_value(cache_key, ttl_ms, loader, force_refresh=refresh)
