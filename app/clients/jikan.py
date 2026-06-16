"""
Jikan v4 client (MyAnimeList unofficial API).
Rate-limit: 3 req/sec, 60 req/min.
Fetches filler/recap flags for every episode of an anime.
Cached for 7 days — filler classification never changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.cache.backend import get_cached_value, get_client

logger = logging.getLogger("th3anime.clients.jikan")

JIKAN_API = "https://api.jikan.moe/v4"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Th3AnimeAPI/2.0)",
    "Accept": "application/json",
}
_MIN_INTERVAL = 0.4  # seconds between requests
_last_request: float = 0.0
_jikan_lock = asyncio.Lock()


async def _fetch_json(url: str) -> dict:
    global _last_request
    async with _jikan_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()

    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(client.get(url, headers=_HEADERS), timeout=12.0)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Jikan request timed out: {url}")

    if resp.status_code != 200:
        raise RuntimeError(f"Jikan request failed: {resp.status_code}")

    return resp.json()


async def fetch_jikan_episode_data(mal_id: int) -> dict[str, Any]:
    """
    Returns a dict keyed by episode number (str):
      { "1": { "isFiller": bool, "isRecap": bool, "title": str|None, "titleJapanese": str|None }, ... }
    Handles pagination (100 eps/page, safety cap at 20 pages).
    """
    cache_key = f"jikan:episodes:{mal_id}"

    async def loader() -> dict[str, Any]:
        record: dict[str, Any] = {}
        page = 1

        while True:
            payload = await _fetch_json(f"{JIKAN_API}/anime/{mal_id}/episodes?page={page}")
            data = payload.get("data") or []

            if not data:
                break

            for ep in data:
                ep_id = ep.get("mal_id")
                if not ep_id:
                    continue
                record[str(ep_id)] = {
                    "isFiller": bool(ep.get("filler")),
                    "isRecap": bool(ep.get("recap")),
                    "title": ep.get("title") or ep.get("title_romanji") or None,
                    "titleJapanese": ep.get("title_japanese") or None,
                }

            if not payload.get("pagination", {}).get("has_next_page"):
                break

            page += 1
            if page > 20:
                break

            await asyncio.sleep(0.5)

        return record

    return await get_cached_value(
        cache_key,
        7 * 24 * 60 * 60 * 1000,  # 7 days
        loader,
    )
