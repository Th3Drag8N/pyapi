"""
Anidap provider.
Resolves an anime slug by scraping the Anidap watch page,
then fetches server/source lists from their API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.cache.backend import get_cached_value, get_client

logger = logging.getLogger("th3anime.providers.anidap")

ANIDAP_BASE   = "https://anidap.se"
ANIDAP_API    = f"{ANIDAP_BASE}/api/anime"
ANIDAP_CORS   = "https://cors.otakuhg.site"
STREAM_ORIGIN = "https://otakuhg.site/"

_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": ANIDAP_BASE,
    "Referer": f"{ANIDAP_BASE}/",
}

_API_HEADERS = {
    "User-Agent": _PAGE_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": ANIDAP_BASE,
    "Referer": f"{ANIDAP_BASE}/",
}


# ── HTTP helpers ──────────────────────────────────────────────────────────────
async def _fetch_text(url: str) -> str:
    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(client.get(url, headers=_PAGE_HEADERS), timeout=15.0)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Anidap page request timed out: {url}")
    if resp.status_code != 200:
        raise RuntimeError(f"Anidap page request failed: {resp.status_code}")
    return resp.text


async def _fetch_json(url: str) -> Any:
    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(client.get(url, headers=_API_HEADERS), timeout=15.0)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Anidap API request timed out: {url}")
    if resp.status_code != 200:
        raise RuntimeError(f"Anidap API request failed: {resp.status_code}")
    return resp.json()


# ── Slug extraction ───────────────────────────────────────────────────────────
def _extract_slug(html: str) -> str:
    # Strategy 1: __NEXT_DATA__
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
            serialized = json.dumps(nd)
            dm = re.search(r'"slug":"([a-z0-9][a-z0-9\-]*[a-z0-9])"', serialized)
            if dm:
                return dm.group(1)
        except Exception:
            pass

    # Strategy 2: currentUrl in query string
    m = re.search(r'currentUrl[^"]*"https?://anidap\.se/watch\?id=([^"&\\]+)', html)
    if m:
        decoded = m.group(1)
        if re.match(r'^[a-z0-9\-]+$', decoded, re.IGNORECASE):
            return decoded

    # Strategy 3: escaped JSON patterns
    for pat in [
        r'\\"slug\\":\\"([a-z0-9][a-z0-9\-]*[a-z0-9])\\"',
        r'"slug":"([a-z0-9][a-z0-9\-]*[a-z0-9])"',
        r'slug[\'\":\s]+[\'""]([a-z0-9][a-z0-9\-]*[a-z0-9])[\'"]',
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return m.group(1)

    # Strategy 4 & 5: legacy/remix patterns
    for pat in [
        r'\\"id\\",\\"([a-z0-9\-]+)\\",\\"slug\\"',
        r'\\"id\\",\\"([a-z0-9\-]+)\\",\\"anilistId\\"',
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return m.group(1)

    raise RuntimeError("Could not extract Anidap slug")


def _build_media_url(path_or_token: str) -> str:
    if path_or_token.startswith("http"):
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(path_or_token)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if "origin" not in params:
            params["origin"] = [STREAM_ORIGIN]
        new_query = urlencode({k: v[0] for k, v in params.items()})
        return urlunparse(parsed._replace(query=new_query))
    url = f"{ANIDAP_CORS}/media/{path_or_token}"
    return f"{url}?origin={quote(STREAM_ORIGIN)}"


def _parse_quality(meta_line: str = "") -> str:
    m = re.search(r'NAME="([^"]+)"', meta_line, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'RESOLUTION=\d+x(\d+)', meta_line, re.IGNORECASE)
    if m:
        return f"{m.group(1)}p"
    return "auto"


# ── Slug caching ──────────────────────────────────────────────────────────────
async def _fetch_slug(anime_id: str | int, refresh: bool = False) -> str:
    cache_key = f"anidap:slug:{anime_id}"

    async def loader() -> str:
        urls = [
            f"{ANIDAP_BASE}/watch?id={quote(str(anime_id))}&ep=1&type=sub",
            f"{ANIDAP_BASE}/watch?id={quote(str(anime_id))}&ep=1",
        ]
        last_err: Exception | None = None
        for url in urls:
            try:
                html = await _fetch_text(url)
                return _extract_slug(html)
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError("Could not resolve Anidap slug")

    return await get_cached_value(cache_key, 30 * 24 * 60 * 60 * 1000, loader, force_refresh=refresh)


async def _fetch_playlist(master_url: str) -> dict:
    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(
            client.get(master_url, headers=_PAGE_HEADERS), timeout=15.0
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Anidap playlist request timed out")
    if resp.status_code != 200:
        raise RuntimeError(f"Anidap playlist request failed: {resp.status_code}")

    lines = [l.strip() for l in resp.text.split("\n") if l.strip()]
    sources = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        if i + 1 >= len(lines) or lines[i + 1].startswith("#"):
            continue
        next_line = lines[i + 1]
        sources.append({
            "url": _build_media_url(next_line),
            "quality": _parse_quality(line),
        })
    return {"sources": sources}


# ── Public API ────────────────────────────────────────────────────────────────
async def fetch_anidap_servers(
    anime_id: str | int, episode: int, refresh: bool = False
) -> dict:
    slug = await _fetch_slug(anime_id, refresh)
    cache_key = f"anidap:servers:{slug}:{episode}"

    async def loader() -> dict:
        payload = await _fetch_json(
            f"{ANIDAP_API}/servers?id={quote(slug)}&ep={quote(str(episode))}"
        )
        if not payload.get("success") or not payload.get("data"):
            raise RuntimeError("Anidap servers not found")
        data = payload["data"]
        return {
            "subProviders": data.get("subProviders") if isinstance(data.get("subProviders"), list) else [],
            "dubProviders": data.get("dubProviders") if isinstance(data.get("dubProviders"), list) else [],
        }

    return await get_cached_value(cache_key, 10 * 60 * 1000, loader, force_refresh=refresh)


async def fetch_anidap_sources(
    anime_id: str | int,
    episode: int,
    host: str,
    ep_type: str,
    refresh: bool = False,
) -> dict:
    slug = await _fetch_slug(anime_id, refresh)
    cache_key = f"anidap:sources:{slug}:{episode}:{host}:{ep_type}"

    async def loader() -> dict:
        payload = await _fetch_json(
            f"{ANIDAP_API}/sources"
            f"?id={quote(slug)}&ep={quote(str(episode))}"
            f"&host={quote(host)}&type={quote(ep_type)}"
        )
        if not payload.get("success") or not payload.get("data"):
            raise RuntimeError("Anidap source token not found")

        master_url = _build_media_url(payload["data"])
        playlist = await _fetch_playlist(master_url)

        return {
            "sources": [{"url": master_url, "quality": "auto"}, *playlist["sources"]],
            "subtitles": [],
            "thumbnails": [],
            "headers": {
                "Origin": STREAM_ORIGIN,
                "Referer": f"{ANIDAP_BASE}/",
                "User-Agent": _PAGE_HEADERS["User-Agent"],
            },
        }

    return await get_cached_value(cache_key, 2 * 60 * 1000, loader, force_refresh=refresh)
