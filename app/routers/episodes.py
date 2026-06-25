from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist
from app.clients.jikan import fetch_jikan_episode_data
from app.providers.anikage import (
    fetch_anikage_episodes,
    normalize_episode_list,
    summarize_providers,
)
from app.providers.animex import (
    fetch_animex_episodes,
    normalize_episode_list as animex_normalize,
)

router = APIRouter()
logger = logging.getLogger("th3anime.routers.episodes")

_IST = timezone(timedelta(hours=5, minutes=30))

_ANILIST_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id idMal title { english romaji native }
    status episodes
    nextAiringEpisode { episode airingAt }
  }
}
"""


def _format_episode_schedule(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=_IST).strftime("%Y-%m-%d %H:%M:%S")


def _is_aired(ep_num: int, media_data: dict | None) -> bool:
    if not media_data:
        return True
    if media_data.get("status") == "RELEASING" and media_data.get("nextAiringEpisode"):
        return ep_num < media_data["nextAiringEpisode"]["episode"]
    return True


@router.get("/episodes")
async def episodes(
    response: Response,
    id: int = Query(..., description="AniList anime ID"),
):
    if not id:
        return {"success": False, "error": "id is required"}

    try:
        resp = await fetch_anilist(
            _ANILIST_QUERY, {"id": id}, ttl_ms=CACHE_POLICIES.episodes.ttl_ms
        )
        media_data = resp.get("Media") or {}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    anime_title   = (
        media_data.get("title", {}).get("english")
        or media_data.get("title", {}).get("romaji")
        or media_data.get("title", {}).get("native")
        or None
    )
    japanese_title = (
        media_data.get("title", {}).get("native")
        or media_data.get("title", {}).get("romaji")
        or media_data.get("title", {}).get("english")
        or None
    )
    is_airing = media_data.get("status") == "RELEASING"
    mal_id    = media_data.get("idMal")

    # Parallel fetch: anikage + animex + jikan
    anikage_task = asyncio.create_task(_safe_anikage(id))
    animex_task  = asyncio.create_task(_safe_animex(id, media_data.get("title") or {}))
    jikan_task   = asyncio.create_task(_safe_jikan(mal_id))

    anikage_eps, animex_eps, jikan_record = await asyncio.gather(
        anikage_task, animex_task, jikan_task
    )

    anikage_ep_map = {ep["number"]: ep for ep in anikage_eps}
    animex_ep_map  = {ep["number"]: ep for ep in animex_eps}
    anikage_summary = summarize_providers(anikage_eps)

    # Determine final episode count
    provider_count = 0
    if anikage_eps:
        provider_count = max(ep["number"] for ep in anikage_eps)
    elif animex_eps:
        provider_count = max(ep["number"] for ep in animex_eps)

    anilist_aired = None
    if is_airing and media_data.get("nextAiringEpisode"):
        anilist_aired = media_data["nextAiringEpisode"]["episode"] - 1
    elif media_data.get("episodes"):
        anilist_aired = media_data["episodes"]

    final_count = provider_count
    if is_airing and anilist_aired is not None:
        final_count = min(provider_count, anilist_aired)
        if final_count == 0 and anilist_aired > 0:
            final_count = anilist_aired
    elif not is_airing and final_count == 0 and media_data.get("episodes"):
        final_count = media_data["episodes"]

    ep_list = []
    for i in range(final_count):
        ep_num = i + 1
        anikage_ep = anikage_ep_map.get(ep_num)
        animex_ep  = animex_ep_map.get(ep_num)
        jikan_ep   = jikan_record.get(str(ep_num))
        aired      = _is_aired(ep_num, media_data)

        # hasSub / hasDub
        if anikage_ep:
            has_sub = anikage_ep["hasSub"]
            has_dub = anikage_ep["hasDub"]
        elif animex_ep:
            has_sub = animex_ep["hasSub"]
            has_dub = animex_ep["hasDub"]
        elif aired:
            has_sub = anikage_summary["hasSub"]
            has_dub = anikage_summary["hasDub"]
        else:
            has_sub = has_dub = False

        if not aired:
            has_sub = has_dub = False

        # isFiller / isRecap
        is_filler = is_recap = False
        if aired:
            if jikan_ep:
                is_filler = jikan_ep["isFiller"]
                is_recap  = jikan_ep["isRecap"]
            elif anikage_ep:
                is_filler = anikage_ep.get("isFiller", False)
            elif animex_ep:
                is_filler = animex_ep.get("isFiller", False)

        # Title / description / image
        title = anime_title
        description = None
        image = None
        jp_ep_title = japanese_title

        if anikage_ep and anikage_ep.get("title") and anikage_ep["title"] != anime_title:
            title = anikage_ep["title"]
        elif animex_ep and animex_ep.get("title") and animex_ep["title"] != anime_title:
            title = animex_ep["title"]
        elif jikan_ep and jikan_ep.get("title"):
            title = jikan_ep["title"]

        if anikage_ep and anikage_ep.get("description"):
            description = anikage_ep["description"]
        elif animex_ep and animex_ep.get("description"):
            description = animex_ep["description"]

        if anikage_ep and anikage_ep.get("img"):
            image = anikage_ep["img"]
        elif animex_ep and animex_ep.get("img"):
            image = animex_ep["img"]

        jp_ep_title = (
            (None if anikage_ep and anikage_ep.get("title") else None)
            or (None if animex_ep and animex_ep.get("title") else None)
            or (jikan_ep.get("titleJapanese") if jikan_ep else None)
            or japanese_title
        )

        ep_list.append({
            "episode_no": ep_num,
            "id": f"{media_data['id']}&ep={ep_num}",
            "data_id": media_data["id"],
            "title": title,
            "japanese_title": jp_ep_title,
            "description": description,
            "image": image,
            "hasSub": has_sub,
            "hasDub": has_dub,
            "isFiller": is_filler,
            "isRecap": is_recap,
            "isReleased": aired,
        })

    if is_airing:
        response.headers["Cache-Control"] = "public, s-maxage=900, stale-while-revalidate=300"
    else:
        set_cache_headers(response, CACHE_POLICIES.episodes)

    next_ep = (media_data.get("nextAiringEpisode") or {}).get("episode")
    next_ep_schedule = _format_episode_schedule(
        (media_data.get("nextAiringEpisode") or {}).get("airingAt")
    )

    return {
        "success": True,
        "results": [
            {
                "totalEpisodes": final_count,
                "releasedEpisodes": sum(1 for e in ep_list if e["isReleased"]),
                "episodes": ep_list,
                "nextEpisode": next_ep,
                "nextEpisodeSchedule": next_ep_schedule,
                "status": media_data.get("status"),
            }
        ],
    }


async def _safe_anikage(anime_id: int) -> list[dict]:
    try:
        raw = await fetch_anikage_episodes(anime_id)
        return normalize_episode_list(raw)
    except Exception as exc:
        logger.warning("[episodes] anikage failed: %s", exc)
        return []


async def _safe_animex(anime_id: int, title_data: dict) -> list[dict]:
    try:
        raw = await fetch_animex_episodes(anime_id, title_data)
        return animex_normalize(raw)
    except Exception as exc:
        logger.warning("[episodes] animex failed: %s", exc)
        return []


async def _safe_jikan(mal_id: int | None) -> dict:
    if not mal_id:
        return {}
    try:
        return await fetch_jikan_episode_data(mal_id)
    except Exception as exc:
        logger.warning("[episodes] jikan failed: %s", exc)
        return {}
