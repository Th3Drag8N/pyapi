from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist

router = APIRouter()

_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id status episodes
    nextAiringEpisode { airingAt timeUntilAiring episode }
  }
}
"""


@router.get("/airing")
async def airing(
    response: Response,
    id: int = Query(..., description="AniList anime ID"),
):
    set_cache_headers(response, CACHE_POLICIES.daily)
    try:
        data = await fetch_anilist(
            _QUERY, {"id": id}, ttl_ms=CACHE_POLICIES.daily.ttl_ms
        )
        return {"success": True, "data": data["Media"]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
