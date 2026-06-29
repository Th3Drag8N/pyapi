"""
Animex provider.
Resolves an anime slug by scraping the Animex page, then calls their REST API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import unicodedata
from typing import Any
from urllib.parse import quote

import httpx

from app.cache.backend import get_cached_value, get_cf_client

logger = logging.getLogger("th3anime.providers.animex")

ANIMEX_BASE = "https://animex.one"
ANIMEX_API  = "https://pp.animex.one/rest/api"

_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

_API_HEADERS = {
    "User-Agent": _PAGE_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": ANIMEX_BASE,
    "Referer": f"{ANIMEX_BASE}/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[\s_]+", "-", value)


def _page_candidates(anime_id: str | int, title_data: dict) -> list[str]:
    raw = [
        title_data.get("english"),
        title_data.get("romaji"),
        title_data.get("native"),
    ]
    slugs = list(dict.fromkeys(
        _slugify(t) for t in raw if t
    ))
    return [f"{ANIMEX_BASE}/anime/{slug}-{anime_id}" for slug in slugs if slug]


def _extract_slug(html: str) -> str:
    # Strategy 1: inline JS bundle (new format since Animex dropped __NEXT_DATA__)
    # Matches: slug:"one-piece-p8k27" or slug:"attack-on-titan-f3x9q"
    for pat in [
        r'slug:"([a-z0-9][a-z0-9\-]*[a-z0-9])"',
        r"slug:'([a-z0-9][a-z0-9\-]*[a-z0-9])'",
        r'\\bslug:"([^"]+)"',
        r'"slug":"([^"]+)"',
        r'slug:\s*"([^"]+)"',
        r"slug:\s*'([^']+)'",
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)

    # Strategy 2: __NEXT_DATA__ (legacy, kept for forward compat)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
    if m:
        try:
            nd = json.loads(m.group(1))
            slug = (
                (nd.get("props") or {}).get("pageProps", {}).get("slug")
                or (nd.get("props") or {}).get("pageProps", {}).get("anime", {}).get("slug")
                or (nd.get("props") or {}).get("pageProps", {}).get("data", {}).get("slug")
                or (nd.get("query") or {}).get("slug")
            )
            if slug and isinstance(slug, str):
                return slug
            # Deep search
            serialized = json.dumps(nd)
            dm = re.search(r'"slug":"([a-z0-9][a-z0-9\-]*[a-z0-9])"', serialized)
            if dm:
                return dm.group(1)
        except Exception:
            pass

    raise RuntimeError("Could not extract Animex slug")


async def _fetch_text(url: str, _retry: int = 3) -> str:
    client = get_cf_client()
    for attempt in range(_retry):
        try:
            resp = await asyncio.wait_for(
                client.get(url, headers=_PAGE_HEADERS),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            if attempt < _retry - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Animex page request timed out: {url}")
        if resp.status_code == 200:
            return resp.text
        if resp.status_code in (403, 429) and attempt < _retry - 1:
            wait = (2 ** attempt) + random.uniform(0.5, 1.5)
            logger.warning("Animex page %s returned %d, retrying in %.1fs...", url, resp.status_code, wait)
            await asyncio.sleep(wait)
            continue
        raise RuntimeError(f"Animex page request failed: {resp.status_code}")
    raise RuntimeError(f"Animex page request failed after {_retry} retries")


async def _fetch_json(url: str, _retry: int = 3) -> Any:
    client = get_cf_client()
    for attempt in range(_retry):
        try:
            resp = await asyncio.wait_for(
                client.get(url, headers=_API_HEADERS),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            if attempt < _retry - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Animex API request timed out: {url}")
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (403, 429) and attempt < _retry - 1:
            wait = (2 ** attempt) + random.uniform(0.5, 1.5)
            logger.warning("Animex API %s returned %d, retrying in %.1fs...", url, resp.status_code, wait)
            await asyncio.sleep(wait)
            continue
        raise RuntimeError(f"Animex API request failed: {resp.status_code}")
    raise RuntimeError(f"Animex API request failed after {_retry} retries")


# ── Context (slug) resolution ─────────────────────────────────────────────────
async def _fetch_context(anime_id: str | int, title_data: dict, refresh: bool = False) -> dict:
    cache_key = f"animex:context:{anime_id}"

    async def loader() -> dict:
        candidates = _page_candidates(anime_id, title_data)
        if not candidates:
            raise RuntimeError("No Animex title candidates")
        last_err: Exception | None = None
        for url in candidates:
            try:
                html = await _fetch_text(url)
                return {"url": url, "slug": _extract_slug(html)}
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError("Animex page not found")

    # Slug is stable — cache for 7 days to minimize scraping requests per IP
    return await get_cached_value(cache_key, 7 * 24 * 60 * 60 * 1000, loader, force_refresh=refresh)


# ── Public API ────────────────────────────────────────────────────────────────
async def fetch_animex_episodes(anime_id: str | int, title_data: dict, refresh: bool = False) -> list:
    ctx = await _fetch_context(anime_id, title_data, refresh)
    cache_key = f"animex:episodes:{ctx['slug']}"
    return await get_cached_value(
        cache_key,
        15 * 60 * 1000,
        lambda: _fetch_json(f"{ANIMEX_API}/episodes?id={quote(ctx['slug'])}"),
        force_refresh=refresh,
    )


async def fetch_animex_servers(
    anime_id: str | int,
    title_data: dict,
    episode: int = 1,
    refresh: bool = False,
) -> dict:
    ctx = await _fetch_context(anime_id, title_data, refresh)
    cache_key = f"animex:servers:{ctx['slug']}:{episode}"
    raw = await get_cached_value(
        cache_key,
        10 * 60 * 1000,
        lambda: _fetch_json(
            f"{ANIMEX_API}/servers?id={quote(ctx['slug'])}&epNum={quote(str(episode))}"
        ),
        force_refresh=refresh,
    )
    return normalize_server_lists(raw)


async def fetch_animex_sources(
    anime_id: str | int,
    title_data: dict,
    episode: int,
    provider_id: str,
    ep_type: str,
    refresh: bool = False,
) -> dict:
    ctx = await _fetch_context(anime_id, title_data, refresh)
    cache_key = f"animex:sources:{ctx['slug']}:{episode}:{provider_id}:{ep_type}"
    raw = await get_cached_value(
        cache_key,
        2 * 60 * 1000,
        lambda: _fetch_json(
            f"{ANIMEX_API}/sources"
            f"?id={quote(ctx['slug'])}"
            f"&epNum={quote(str(episode))}"
            f"&type={quote(ep_type)}"
            f"&providerId={quote(provider_id)}"
        ),
        force_refresh=refresh,
    )
    return normalize_sources(raw)


# ── Normalisers ───────────────────────────────────────────────────────────────
def normalize_episode_item(item: dict) -> dict:
    titles = item.get("titles", {})
    return {
        "number": int(item.get("number", 0)),
        "hasSub": bool(item.get("hasSub")),
        "hasDub": bool(item.get("hasDub")),
        "title": titles.get("en") or titles.get("x-jat") or titles.get("ja"),
        "description": item.get("description"),
        "img": item.get("img"),
        "isFiller": bool(item.get("isFiller"))
    }


def normalize_episode_list(raw: list | Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [
        normalize_episode_item(ep)
        for ep in raw
        if (n := int(ep.get("number", 0))) > 0
    ]


def normalize_server_item(item: dict) -> dict | None:
    server_id = item.get("id")
    if not server_id:
        return None
    return {
        "id": server_id,
        "default": bool(item.get("default")),
        "tip": item.get("tip") or None,
    }


def normalize_server_lists(raw: dict | Any) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    sub = [s for s in (normalize_server_item(x) for x in (raw.get("subProviders") or [])) if s]
    dub = [s for s in (normalize_server_item(x) for x in (raw.get("dubProviders") or [])) if s]
    return {"subProviders": sub, "dubProviders": dub}


def normalize_sources(payload: dict | Any) -> dict:
    if not isinstance(payload, dict):
        payload = {}

    sources = [
        {"url": s["url"], "quality": s.get("quality") or "auto"}
        for s in (payload.get("sources") or [])
        if s.get("url")
    ]

    tracks_raw = payload.get("tracks") or payload.get("subtitles") or []
    subtitles = [
        {
            "file": t.get("file") or t.get("url"),
            "url": t.get("url") or t.get("file"),
            "lang": t.get("lang") or t.get("label") or "Unknown",
            "label": t.get("label") or t.get("lang") or "Unknown",
            "kind": t.get("kind") or "captions",
            "default": bool(t.get("default")),
        }
        for t in tracks_raw
        if t.get("file") or t.get("url")
    ]

    return {
        "sources": sources,
        "subtitles": subtitles,
        "thumbnails": [],
        "intro": payload.get("intro") or None,
        "outro": payload.get("outro") or None,
        "headers": payload.get("headers") or {},
    }


def pick_animex_provider(servers: dict, requested: str | None, ep_type: str) -> str | None:
    normalized = normalize_server_lists(servers)
    providers = normalized["dubProviders"] if ep_type == "dub" else normalized["subProviders"]
    if requested:
        match = next((p for p in providers if p["id"] == requested), None)
        if match:
            return match["id"]
    default = next((p for p in providers if p.get("default")), None)
    if default:
        return default["id"]
    return providers[0]["id"] if providers else None
