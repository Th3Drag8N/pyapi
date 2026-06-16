from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from fastapi import APIRouter, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist
from app.providers.anikage import (
    fetch_anikage_episodes,
    fetch_anikage_sources,
    normalize_episode_list,
)
from app.providers.animex import (
    fetch_animex_servers,
    fetch_animex_sources,
    normalize_server_lists,
)
from app.providers.anidap import fetch_anidap_servers, fetch_anidap_sources
from app.utils.aniskip import fetch_aniskip_data
from app.utils.chapters import fetch_chapter_skip_data
from app.utils.headers import build_stream_headers
from app.utils.m3u8 import expand_sources
from app.utils.source_config import get_source_order, normalize_source_name

router = APIRouter()
logger = logging.getLogger("th3anime.routers.stream")

# Bounded LRU cache for intro/outro timings
_SKIP_CACHE_MAX = 500
_skip_cache: OrderedDict[str, dict] = OrderedDict()


def _to_bool(value: str | None, fallback: bool = False) -> bool:
    if value is None or value == "":
        return fallback
    return str(value).lower() in ("1", "true", "yes")


def _get_skip(anime_id: str, episode: int) -> dict | None:
    return _skip_cache.get(f"{anime_id}:{episode}")


def _set_skip(anime_id: str, episode: int, intro, outro) -> None:
    if not intro and not outro:
        return
    key = f"{anime_id}:{episode}"
    if len(_skip_cache) >= _SKIP_CACHE_MAX and key not in _skip_cache:
        _skip_cache.popitem(last=False)
    _skip_cache[key] = {"intro": intro or None, "outro": outro or None}
    _skip_cache.move_to_end(key)


@router.get("/stream")
async def stream(
    response: Response,
    id: str = Query(..., description="AniList anime ID"),
    episode: int = Query(default=1, alias="ep", ge=1),
    ep: int | None = None,
    type: str = Query(default="sub"),
    site: str | None = None,
    host: str | None = None,
    refresh: str | None = None,
    anilistId: str | None = None,
):
    set_cache_headers(response, CACHE_POLICIES.stream)

    anime_id = id or anilistId
    if not anime_id:
        return {"success": False, "error": "id is required"}

    episode = ep if ep is not None else episode
    ep_type = "dub" if str(type).lower() == "dub" else "sub"
    requested_site = normalize_source_name(site)
    requested_host = host.lower() if host else None
    do_refresh = _to_bool(refresh)

    attempts: list[str] = []
    stream_data = None
    source_order = get_source_order(requested_site)

    # Fetch media metadata once (used by animex + aniskip fallback)
    media_meta = None
    try:
        media_meta = await _fetch_media_meta(anime_id, do_refresh)
    except Exception as exc:
        attempts.append(f"media-data: {exc}")

    media_title = (media_meta or {}).get("title") or {}

    for source in source_order:
        if stream_data:
            break
        try:
            if source == "anikage":
                stream_data = await _resolve_anikage(
                    anime_id, episode, ep_type, requested_host, do_refresh, attempts
                )
            elif source == "animex":
                stream_data = await _resolve_animex(
                    anime_id, media_title, episode, ep_type, requested_host, do_refresh, attempts
                )
                if stream_data and media_meta:
                    stream_data["idMal"] = media_meta.get("idMal")
                    stream_data["duration"] = media_meta.get("duration")
            elif source == "anidap":
                stream_data = await _resolve_anidap(
                    anime_id, episode, ep_type, requested_host, do_refresh, attempts
                )
        except Exception as exc:
            msg = f"{source}: {exc}"
            if msg not in attempts:
                attempts.append(msg)
            logger.warning("[stream] %s failed for %s/ep%d: %s", source, anime_id, episode, exc)

    if not stream_data:
        return {"success": False, "error": "No stream source available", "details": attempts}

    first_url = (stream_data.get("sources") or [{}])[0].get("url", "")
    clean_headers = build_stream_headers(
        stream_data.get("headers") or {},
        stream_data.get("host") or "",
        first_url,
    )

    expanded = await expand_sources(stream_data.get("sources") or [], clean_headers)

    # Normalise subtitles
    subtitles = [
        {
            "file": s.get("url") or s.get("file"),
            "url": s.get("url") or s.get("file"),
            "lang": s.get("lang") or s.get("label") or "Unknown",
            "label": s.get("label") or s.get("lang") or "Unknown",
            "kind": s.get("kind") or "captions",
            "default": bool(s.get("default")),
        }
        for s in (stream_data.get("subtitles") or [])
        if s.get("url") or s.get("file")
    ]

    # Intro / outro resolution chain
    intro = stream_data.get("intro") or None
    outro = stream_data.get("outro") or None

    if not intro or not outro:
        chapters_track = next(
            (s for s in subtitles if s.get("kind") == "chapters" and s.get("url")), None
        )
        if chapters_track:
            skip = await fetch_chapter_skip_data(chapters_track["url"], clean_headers)
            intro = intro or skip.get("intro")
            outro = outro or skip.get("outro")

    if not intro or not outro:
        mal_id = stream_data.get("idMal") or (media_meta or {}).get("idMal")
        duration = stream_data.get("duration") or (media_meta or {}).get("duration")
        if mal_id:
            dur_sec = int(duration * 60) if duration else 1440
            aniskip = await fetch_aniskip_data(mal_id, episode, dur_sec)
            intro = intro or aniskip.get("intro")
            outro = outro or aniskip.get("outro")

    if not intro or not outro:
        cached = _get_skip(str(anime_id), episode)
        intro = intro or (cached or {}).get("intro")
        outro = outro or (cached or {}).get("outro")

    _set_skip(str(anime_id), episode, intro, outro)

    return {
        "success": True,
        "data": {
            "id": int(anime_id) if str(anime_id).isdigit() else anime_id,
            "episode": episode,
            "type": ep_type,
            "host": stream_data.get("host"),
            "sourceSite": stream_data.get("sourceSite"),
            "sources": expanded["sources"],
            "audios": expanded["audios"],
            "subtitles": subtitles,
            "intro": intro,
            "outro": outro,
            "thumbnails": stream_data.get("thumbnails") or [],
            "headers": clean_headers,
        },
    }


# ── Provider resolvers ────────────────────────────────────────────────────────
async def _resolve_anikage(anime_id, episode, ep_type, requested_host, refresh, attempts):
    try:
        raw = await fetch_anikage_episodes(anime_id, refresh)
        eps = normalize_episode_list(raw)
    except Exception as exc:
        attempts.append(f"anikage:fetch: {exc}")
        return None

    current = next((e for e in eps if e["number"] == episode), None)
    if not current:
        attempts.append(f"anikage: episode {episode} not found in list")
        return None

    providers = current.get("dubProviders" if ep_type == "dub" else "subProviders") or []
    if not providers:
        attempts.append(f"anikage: no {ep_type} providers for episode {episode}")
        return None

    ordered = (
        [requested_host, *[p for p in providers if p != requested_host]]
        if requested_host and requested_host in providers
        else providers
    )

    for host in ordered:
        try:
            src = await fetch_anikage_sources(anime_id, episode, host, ep_type, refresh)
            if isinstance(src.get("sources"), list) and src["sources"]:
                return {
                    "sourceSite": "anikage",
                    "host": host,
                    "sources": src["sources"],
                    "subtitles": src.get("subtitles") or src.get("tracks") or [],
                    "thumbnails": src.get("thumbnails") or [],
                    "intro": src.get("intro"),
                    "outro": src.get("outro"),
                    "headers": src.get("headers") or {},
                }
            attempts.append(f"anikage:{host}: empty sources")
        except Exception as exc:
            attempts.append(f"anikage:{host}: {exc}")

    return None


async def _resolve_animex(anime_id, media_title, episode, ep_type, requested_host, refresh, attempts):
    server_data = await fetch_animex_servers(anime_id, media_title, episode, refresh)
    providers = server_data.get("dubProviders" if ep_type == "dub" else "subProviders") or []

    if not providers:
        raise RuntimeError(f"No {ep_type} providers on Animex for episode {episode}")

    ordered = (
        [*[p for p in providers if p["id"] == requested_host],
         *[p for p in providers if p["id"] != requested_host]]
        if requested_host
        else providers
    )

    for provider in ordered:
        try:
            src = await fetch_animex_sources(
                anime_id, media_title, episode, provider["id"], ep_type, refresh
            )
            if isinstance(src.get("sources"), list) and src["sources"]:
                return {
                    "sourceSite": "animex",
                    "host": provider["id"],
                    "sources": src["sources"],
                    "subtitles": src.get("subtitles") or [],
                    "thumbnails": src.get("thumbnails") or [],
                    "intro": src.get("intro"),
                    "outro": src.get("outro"),
                    "headers": src.get("headers") or {},
                }
            attempts.append(f"animex:{provider['id']}: empty sources")
        except Exception as exc:
            attempts.append(f"animex:{provider['id']}: {exc}")

    raise RuntimeError(f"All Animex providers failed for episode {episode}")


async def _resolve_anidap(anime_id, episode, ep_type, requested_host, refresh, attempts):
    lists = await fetch_anidap_servers(anime_id, episode, refresh)
    providers = lists.get("dubProviders" if ep_type == "dub" else "subProviders") or []

    if not providers:
        raise RuntimeError(f"No {ep_type} providers on Anidap for episode {episode}")

    ordered = (
        [requested_host, *[p for p in providers if p != requested_host]]
        if requested_host and requested_host in providers
        else providers
    )

    for host in ordered:
        try:
            src = await fetch_anidap_sources(anime_id, episode, host, ep_type, refresh)
            if isinstance(src.get("sources"), list) and src["sources"]:
                return {
                    "sourceSite": "anidap",
                    "host": host,
                    "sources": src["sources"],
                    "subtitles": src.get("subtitles") or [],
                    "thumbnails": src.get("thumbnails") or [],
                    "intro": src.get("intro"),
                    "outro": src.get("outro"),
                    "headers": src.get("headers") or {},
                }
            attempts.append(f"anidap:{host}: empty sources")
        except Exception as exc:
            attempts.append(f"anidap:{host}: {exc}")

    raise RuntimeError(f"All Anidap providers failed for episode {episode}")


async def _fetch_media_meta(anime_id: str, refresh: bool) -> dict:
    resp = await fetch_anilist(
        """query ($id: Int) {
          Media(id: $id, type: ANIME) { idMal duration title { english romaji native } }
        }""",
        {"id": int(anime_id)},
        ttl_ms=CACHE_POLICIES.info.ttl_ms,
        refresh=refresh,
    )
    return resp.get("Media") or {}
