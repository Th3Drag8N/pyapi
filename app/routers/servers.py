from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist
from app.providers.anikage import fetch_anikage_episodes, normalize_episode_list
from app.providers.animex import fetch_animex_servers, normalize_server_lists
from app.providers.anidap import fetch_anidap_servers
from app.utils.source_config import get_source_order, normalize_source_name

router = APIRouter()
logger = logging.getLogger("th3anime.routers.servers")


def _to_bool(value: str | None, fallback: bool = False) -> bool:
    if value is None or value == "":
        return fallback
    return str(value).lower() in ("1", "true", "yes")


def _map_providers(providers: list, site: str, ep_type: str) -> list[dict]:
    return [
        {
            "site": site,
            "id": p if isinstance(p, str) else p.get("id"),
            "type": ep_type,
            "default": p.get("default", False) if isinstance(p, dict) else False,
            "tip": p.get("tip") if isinstance(p, dict) else None,
        }
        for p in providers
        if (p if isinstance(p, str) else p.get("id"))
    ]


@router.get("/servers")
async def servers(
    response: Response,
    id: int = Query(..., description="AniList anime ID"),
    episode: int = Query(default=1, alias="ep", ge=1),
    ep: int | None = None,
    site: str | None = None,
    refresh: str | None = None,
):
    set_cache_headers(response, CACHE_POLICIES.episodes)
    episode = ep if ep is not None else episode
    requested_site = normalize_source_name(site)
    do_refresh = _to_bool(refresh)
    source_order = get_source_order(requested_site)

    attempts: list[str] = []
    all_sub: list[dict] = []
    all_dub: list[dict] = []
    primary_site: str | None = None

    # Fetch AniList title if animex is in scope
    media_data: dict | None = None
    if "animex" in source_order:
        try:
            resp = await fetch_anilist(
                "query ($id: Int) { Media(id: $id, type: ANIME) { id title { english romaji native } } }",
                {"id": id},
                ttl_ms=CACHE_POLICIES.episodes.ttl_ms,
            )
            media_data = resp.get("Media")
        except Exception as exc:
            attempts.append(f"anilist-title: {exc}")

    # Parallel provider fetch
    tasks = []
    for source in source_order:
        if source == "anikage":
            tasks.append(_task_anikage(id, episode, do_refresh, attempts))
        elif source == "animex" and media_data:
            tasks.append(_task_animex(id, media_data.get("title") or {}, episode, do_refresh, attempts))
        elif source == "anidap":
            tasks.append(_task_anidap(id, episode, do_refresh, attempts))
        else:
            tasks.append(asyncio.coroutine(lambda: None)())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if not isinstance(result, dict):
            continue
        sub = result.get("sub") or []
        dub = result.get("dub") or []
        if sub or dub:
            s_site = result["site"]
            if not primary_site:
                primary_site = s_site
            # De-dupe
            existing_sub_ids = {p["id"] for p in all_sub}
            existing_dub_ids = {p["id"] for p in all_dub}
            all_sub += [p for p in _map_providers(sub, s_site, "sub") if p["id"] not in existing_sub_ids]
            all_dub += [p for p in _map_providers(dub, s_site, "dub") if p["id"] not in existing_dub_ids]

    if not all_sub and not all_dub:
        return {
            "success": False,
            "error": "No providers available",
            "details": attempts,
        }

    return {
        "success": True,
        "data": {
            "id": id,
            "episode": episode,
            "sourceSite": primary_site,
            "hasSub": bool(all_sub),
            "hasDub": bool(all_dub),
            "providers": {"sub": all_sub, "dub": all_dub},
        },
    }


async def _task_anikage(anime_id: int, episode: int, refresh: bool, attempts: list) -> dict | None:
    try:
        raw = await fetch_anikage_episodes(anime_id, refresh)
        eps = normalize_episode_list(raw)
        ep = next((e for e in eps if e["number"] == episode), None)
        return {"site": "anikage", "sub": ep.get("subProviders") or [] if ep else [], "dub": ep.get("dubProviders") or [] if ep else []}
    except Exception as exc:
        attempts.append(f"anikage: {exc}")
        return None


async def _task_animex(anime_id: int, title_data: dict, episode: int, refresh: bool, attempts: list) -> dict | None:
    try:
        raw = await fetch_animex_servers(anime_id, title_data, episode, refresh)
        s = normalize_server_lists(raw)
        return {"site": "animex", "sub": s["subProviders"], "dub": s["dubProviders"]}
    except Exception as exc:
        attempts.append(f"animex: {exc}")
        return None


async def _task_anidap(anime_id: int, episode: int, refresh: bool, attempts: list) -> dict | None:
    try:
        raw = await fetch_anidap_servers(anime_id, episode, refresh)
        return {"site": "anidap", "sub": raw.get("subProviders") or [], "dub": raw.get("dubProviders") or []}
    except Exception as exc:
        attempts.append(f"anidap: {exc}")
        return None
