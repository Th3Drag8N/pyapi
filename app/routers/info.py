from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist
from app.providers.anikage import fetch_anikage_episodes, normalize_episode_list
from app.providers.animex import (
    fetch_animex_episodes,
    normalize_episode_list as animex_ep_list,
)
from app.utils.source_config import get_source_order

router = APIRouter()
logger = logging.getLogger("th3anime.routers.info")

_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id idMal
    title { romaji english native }
    description status episodes
    nextAiringEpisode { episode airingAt timeUntilAiring }
    season seasonYear bannerImage
    coverImage { extraLarge large color }
    genres averageScore
    trailer { id site }
    characters(sort: [ROLE, RELEVANCE, ID], perPage: 12) {
      edges { node { id name { full } image { large } } }
    }
    recommendations(perPage: 12) {
      nodes {
        mediaRecommendation {
          id idMal title { english romaji } coverImage { large } type
        }
      }
    }
    relations {
      edges {
        relationType(version: 2)
        node {
          id title { english romaji native }
          coverImage { large extraLarge } format type status episodes
        }
      }
    }
  }
}
"""


def _ep_flags(episodes: list[dict]) -> dict:
    return {
        "hasSub": any(ep.get("hasSub") for ep in episodes),
        "hasDub": any(ep.get("hasDub") for ep in episodes),
        "sub": sum(1 for ep in episodes if ep.get("hasSub")),
        "dub": sum(1 for ep in episodes if ep.get("hasDub")),
    }


@router.get("/info")
async def info(
    response: Response,
    id: int = Query(..., description="AniList anime ID"),
):
    set_cache_headers(response, CACHE_POLICIES.info)

    try:
        resp = await fetch_anilist(
            _QUERY, {"id": id}, ttl_ms=CACHE_POLICIES.info.ttl_ms
        )
        data = resp.get("Media")
        if not data:
            return {"success": True, "data": None}

        title_data = data.get("title") or {}
        source_order = get_source_order()

        # Build provider tasks based on source order
        tasks = []
        for source in source_order:
            if source == "anikage":
                tasks.append(asyncio.create_task(_fetch_anikage_eps(id)))
            elif source == "animex":
                tasks.append(asyncio.create_task(_fetch_animex_eps(id, title_data)))
            # anidap doesn't provide episode sub/dub flags in bulk

        provider_results = await asyncio.gather(*tasks, return_exceptions=True)

        ep_map: dict[int, dict] = {}
        for result in provider_results:
            if isinstance(result, Exception):
                continue
            for ep in result:
                num = ep.get("number")
                if num is None:
                    continue
                if num not in ep_map:
                    ep_map[num] = dict(ep)
                else:
                    ep_map[num]["hasSub"] = ep_map[num].get("hasSub") or ep.get("hasSub")
                    ep_map[num]["hasDub"] = ep_map[num].get("hasDub") or ep.get("hasDub")

        unified = list(ep_map.values())
        streaming = _ep_flags(unified)

        if data.get("averageScore"):
            data["averageScore"] = f"{data['averageScore'] / 10:.1f}"

        return {
            "success": True,
            "data": {**data, "streaming": streaming},
        }
    except Exception as exc:
        logger.error("[info] error: %s", exc)
        return {"success": False, "error": str(exc)}


async def _fetch_anikage_eps(anime_id: int) -> list[dict]:
    try:
        raw = await fetch_anikage_episodes(anime_id)
        return normalize_episode_list(raw)
    except Exception:
        return []


async def _fetch_animex_eps(anime_id: int, title_data: dict) -> list[dict]:
    try:
        raw = await fetch_animex_episodes(anime_id, title_data)
        return animex_ep_list(raw)
    except Exception:
        return []
