"""
AniSkip client — fetches OP/ED skip timestamps for a given MAL ID + episode.
Non-critical: always returns safe defaults on failure.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.cache.backend import get_client

logger = logging.getLogger("th3anime.utils.aniskip")

ANISKIP_BASE = "https://api.aniskip.com/v2/skip-times"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Th3AnimeAPI/2.0)",
}

_EMPTY = {"intro": None, "outro": None}


async def fetch_aniskip_data(
    mal_id: int | None,
    episode: int,
    duration: int = 1440,
) -> dict:
    if not mal_id:
        return _EMPTY

    url = f"{ANISKIP_BASE}/{mal_id}/{episode}?types[]=op&types[]=ed&episodeLength={duration}"
    client: httpx.AsyncClient = get_client()

    try:
        resp = await asyncio.wait_for(client.get(url, headers=_HEADERS), timeout=5.0)
        if resp.status_code != 200:
            return _EMPTY

        data = resp.json()
        if not data.get("found") or not isinstance(data.get("results"), list):
            return _EMPTY

        intro = None
        outro = None
        for result in data["results"]:
            interval = result.get("interval") or {}
            if result.get("skipType") == "op" and interval:
                intro = {"start": interval["startTime"], "end": interval["endTime"]}
            elif result.get("skipType") == "ed" and interval:
                outro = {"start": interval["startTime"], "end": interval["endTime"]}

        return {"intro": intro, "outro": outro}

    except Exception as exc:
        if "TimeoutError" not in type(exc).__name__:
            logger.warning("[aniskip] fetch warning: %s", exc)
        return _EMPTY
