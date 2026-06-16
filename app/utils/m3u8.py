"""
M3U8 / HLS playlist utilities.
Expands a single 'auto' quality source into multiple quality variants
by parsing the master playlist.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin

import httpx

from app.cache.backend import get_client

logger = logging.getLogger("th3anime.utils.m3u8")


def _parse_quality(meta_line: str = "") -> str:
    m = re.search(r'NAME="([^"]+)"', meta_line, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'RESOLUTION=\d+x(\d+)', meta_line, re.IGNORECASE)
    if m:
        return f"{m.group(1)}p"
    return "auto"


async def _fetch_m3u8_qualities(master_url: str, headers: dict) -> dict:
    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(client.get(master_url, headers=headers), timeout=10.0)
        if resp.status_code != 200:
            return {"sources": [], "audios": []}
        return _parse_m3u8_text(resp.text, master_url)
    except Exception:
        return {"sources": [], "audios": []}


def _parse_m3u8_text(text: str, base_url: str) -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    sources = []
    audios = []

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
            uri_m = re.search(r'URI="([^"]+)"', line, re.IGNORECASE)
            name_m = re.search(r'NAME="([^"]+)"', line, re.IGNORECASE)
            lang_m = re.search(r'LANGUAGE="([^"]+)"', line, re.IGNORECASE)
            if uri_m:
                audios.append({
                    "url": urljoin(base_url, uri_m.group(1)),
                    "name": name_m.group(1) if name_m else "Unknown",
                    "language": lang_m.group(1) if lang_m else "Unknown",
                })
            continue

        if line.startswith("#EXT-X-STREAM-INF"):
            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                sources.append({
                    "url": urljoin(base_url, lines[i + 1]),
                    "quality": _parse_quality(line),
                })

    return {"sources": sources, "audios": audios}


def _looks_like_m3u8(url: str) -> bool:
    return bool(
        ".m3u8" in url
        or "/proxy/" in url
        or re.search(r'/(hls|master|playlist|stream)\b', url, re.IGNORECASE)
        or re.search(r'[?&](type|format)=hls', url, re.IGNORECASE)
    )


async def expand_sources(sources: list[dict], headers: dict) -> dict:
    """
    If only one 'auto' quality source exists, try to expand it into
    multiple quality variants by fetching the M3U8 master playlist.
    """
    result = {"sources": sources, "audios": []}

    if not sources or not (len(sources) == 1 and sources[0].get("quality") == "auto"):
        return result

    url = sources[0].get("url", "")
    if not isinstance(url, str):
        return result

    if _looks_like_m3u8(url):
        extracted = await _fetch_m3u8_qualities(url, headers)
        if extracted["sources"]:
            result["sources"] = [sources[0], *extracted["sources"]]
        result["audios"] = extracted["audios"]
        return result

    # Probe: fetch first chunk and check for #EXTM3U
    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(client.get(url, headers=headers), timeout=8.0)
        ct = resp.headers.get("content-type", "")
        is_m3u8_content_type = any(
            ct_part in ct for ct_part in ("mpegurl", "x-mpegurl", "octet-stream")
        )
        text = resp.text
        if not text.lstrip().startswith("#EXTM3U"):
            return result

        parsed = _parse_m3u8_text(text, url)
        if parsed["sources"]:
            result["sources"] = [sources[0], *parsed["sources"]]
        result["audios"] = parsed["audios"]
    except Exception:
        pass

    return result
