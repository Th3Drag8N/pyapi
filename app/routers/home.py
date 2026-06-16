from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist

router = APIRouter()
logger = logging.getLogger("th3anime.routers.home")

_SPOTLIGHT_Q = """query {
  spotlights: Page(page: 1, perPage: 10) {
    media(type: ANIME, sort: TRENDING_DESC, status: RELEASING) {
      id idMal title { english romaji } bannerImage
      coverImage { extraLarge } description averageScore status
    }
  }
}"""

_MAIN_Q = """query {
  trending: Page(page: 1, perPage: 15) {
    media(type: ANIME, sort: TRENDING_DESC) {
      id idMal title { english romaji } coverImage { large } averageScore status
    }
  }
  popular: Page(page: 1, perPage: 15) {
    media(type: ANIME, sort: POPULARITY_DESC) {
      id idMal title { english romaji } coverImage { large } averageScore episodes status
    }
  }
  topUpcoming: Page(page: 1, perPage: 15) {
    media(type: ANIME, sort: POPULARITY_DESC, status: NOT_YET_RELEASED) {
      id idMal title { english romaji } coverImage { large } averageScore status
    }
  }
  topAiring: Page(page: 1, perPage: 15) {
    media(type: ANIME, sort: POPULARITY_DESC, status: RELEASING) {
      id idMal title { english romaji } coverImage { large } averageScore episodes status
    }
  }
  allTimeFavorites: Page(page: 1, perPage: 15) {
    media(type: ANIME, sort: FAVOURITES_DESC) {
      id idMal title { english romaji } coverImage { large } averageScore episodes status
    }
  }
}"""

_LATEST_Q = """query {
  latestEpisodes: Page(page: 1, perPage: 12) {
    airingSchedules(notYetAired: false, sort: [TIME_DESC]) {
      episode airingAt
      media {
        id idMal title { english romaji } coverImage { large } averageScore status episodes
      }
    }
  }
}"""

_EMPTY_MAIN = {
    "trending": {"media": []}, "popular": {"media": []},
    "topUpcoming": {"media": []}, "topAiring": {"media": []},
    "allTimeFavorites": {"media": []},
}


def _norm(media_list: list) -> list:
    return [
        {**m, "averageScore": f"{m['averageScore'] / 10:.1f}" if m.get("averageScore") else None}
        for m in media_list
    ]


@router.get("/home")
async def home(response: Response):
    set_cache_headers(response, CACHE_POLICIES.home)
    ttl = CACHE_POLICIES.home.ttl_ms

    spotlight_task = asyncio.create_task(fetch_anilist(_SPOTLIGHT_Q, {}, ttl_ms=ttl))
    main_task      = asyncio.create_task(fetch_anilist(_MAIN_Q, {}, ttl_ms=ttl))
    latest_task    = asyncio.create_task(fetch_anilist(_LATEST_Q, {}, ttl_ms=ttl))

    results = await asyncio.gather(spotlight_task, main_task, latest_task, return_exceptions=True)
    spotlight_result, main_result, latest_result = results

    if isinstance(spotlight_result, Exception):
        logger.error("[home] spotlight failed: %s", spotlight_result)
        spotlight_data = {"spotlights": {"media": []}}
    else:
        spotlight_data = spotlight_result

    if isinstance(main_result, Exception):
        logger.error("[home] main failed: %s", main_result)
        main_data = _EMPTY_MAIN
    else:
        main_data = main_result

    if isinstance(latest_result, Exception):
        logger.error("[home] latest failed: %s", latest_result)
        latest_data = {"latestEpisodes": {"airingSchedules": []}}
    else:
        latest_data = latest_result

    # De-duped latest episodes
    latest_episodes = []
    seen: set[int] = set()
    for item in (latest_data.get("latestEpisodes") or {}).get("airingSchedules") or []:
        media = item.get("media") or {}
        mid = media.get("id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        latest_episodes.append({
            "id": media.get("id"),
            "idMal": media.get("idMal"),
            "title": media.get("title"),
            "coverImage": media.get("coverImage"),
            "averageScore": f"{media['averageScore'] / 10:.1f}" if media.get("averageScore") else None,
            "episodes": media.get("episodes"),
            "status": media.get("status"),
        })

    return {
        "success": True,
        "data": {
            "spotlights": _norm((spotlight_data.get("spotlights") or {}).get("media") or []),
            "trending":   _norm((main_data.get("trending") or {}).get("media") or []),
            "popular":    _norm((main_data.get("popular") or {}).get("media") or []),
            "topUpcoming":_norm((main_data.get("topUpcoming") or {}).get("media") or []),
            "topAiring":  _norm((main_data.get("topAiring") or {}).get("media") or []),
            "allTimeFavorites": _norm((main_data.get("allTimeFavorites") or {}).get("media") or []),
            "latestEpisodes": latest_episodes,
        },
    }
