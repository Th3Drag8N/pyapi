"""
Anikage provider.
Auth: XOR encode + base64url token, injected into the request path.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import httpx

from app.cache.backend import get_cached_value, get_client

logger = logging.getLogger("th3anime.providers.anikage")

ANIKAGE_API = "https://anikage.cc/api/anime"
_TOKEN = "x9f2k7m4q1w8e3r6t5y0"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}


# ── Auth helpers ─────────────────────────────────────────────────────────────
def _xor_encode(data: bytes, key: bytes) -> bytes:
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))


def _encode_payload(payload: dict) -> str:
    payload["_t"] = str(int(time.time()))
    data = json.dumps(payload, separators=(",", ":")).encode()
    key = _TOKEN.encode()
    xored = _xor_encode(data, key)
    return base64.urlsafe_b64encode(xored).rstrip(b"=").decode()


# ── HTTP ─────────────────────────────────────────────────────────────────────
async def _fetch_json(url: str) -> Any:
    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(client.get(url, headers=_HEADERS), timeout=12.0)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Anikage request timed out: {url}")

    if resp.status_code != 200:
        raise RuntimeError(f"Anikage request failed: {resp.status_code}")

    return resp.json()


# ── Public API ────────────────────────────────────────────────────────────────
async def fetch_anikage_episodes(anime_id: str | int, refresh: bool = False) -> list[dict]:
    token = _encode_payload({"id": str(anime_id), "refresh": "true" if refresh else "false"})
    cache_key = f"anikage:episodes:{anime_id}"

    return await get_cached_value(
        cache_key,
        30 * 60 * 1000,
        lambda: _fetch_json(f"{ANIKAGE_API}/episodes/{token}"),
        force_refresh=refresh,
    )


async def fetch_anikage_sources(
    anime_id: str | int,
    episode: int,
    host: str,
    ep_type: str,
    refresh: bool = False,
) -> dict:
    token = _encode_payload({
        "id": str(anime_id),
        "host": host,
        "epNum": str(episode),
        "type": ep_type,
        "cache": "false" if refresh else "true",
    })
    cache_key = f"anikage:sources:{anime_id}:{episode}:{host}:{ep_type}"

    return await get_cached_value(
        cache_key,
        10 * 60 * 1000,
        lambda: _fetch_json(f"{ANIKAGE_API}/sources/{token}"),
        force_refresh=refresh,
    )


# ── Normalisers ───────────────────────────────────────────────────────────────
def normalize_episode_item(item: dict) -> dict:
    sub_providers = item.get("subProviders") or []
    dub_providers = item.get("dubProviders") or []
    return {
        "number": int(item.get("number", 0)),
        "title": item.get("title") or None,
        "description": item.get("description") or None,
        "img": item.get("img") or None,
        "isFiller": bool(item.get("isFiller")),
        "hasSub": isinstance(sub_providers, list) and len(sub_providers) > 0,
        "hasDub": isinstance(dub_providers, list) and len(dub_providers) > 0,
        "subProviders": sub_providers if isinstance(sub_providers, list) else [],
        "dubProviders": dub_providers if isinstance(dub_providers, list) else [],
    }


def normalize_episode_list(raw: list[dict] | Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [normalize_episode_item(ep) for ep in raw]


def summarize_providers(episodes: list[dict]) -> dict:
    sub_set: set[str] = set()
    dub_set: set[str] = set()
    for ep in episodes:
        for p in ep.get("subProviders") or []:
            sub_set.add(p)
        for p in ep.get("dubProviders") or []:
            dub_set.add(p)
    return {
        "hasSub": bool(sub_set),
        "hasDub": bool(dub_set),
        "subProviders": list(sub_set),
        "dubProviders": list(dub_set),
    }


def pick_host(episode: dict, requested_host: str | None, ep_type: str) -> str | None:
    providers = episode.get("dubProviders" if ep_type == "dub" else "subProviders") or []
    if requested_host and requested_host in providers:
        return requested_host
    return providers[0] if providers else None
