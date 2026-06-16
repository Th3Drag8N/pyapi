from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist

router = APIRouter()

_QUERY = """
query ($page: Int, $sort: [MediaSort], $status: MediaStatus) {
  Page(page: $page, perPage: 24) {
    pageInfo { total currentPage lastPage hasNextPage }
    media(type: ANIME, sort: $sort, status: $status) {
      id idMal title { english romaji }
      coverImage { large } bannerImage genres averageScore episodes status
    }
  }
}
"""


@router.get("/collection")
async def collection(
    response: Response,
    sort: str = "TRENDING_DESC",
    page: int = Query(default=1, ge=1),
    status: str | None = None,
):
    set_cache_headers(response, CACHE_POLICIES.daily)

    variables: dict = {
        "page": page,
        "sort": [s.strip().upper() for s in sort.split(",") if s.strip()],
    }
    if status:
        variables["status"] = status.strip().upper()

    try:
        data = await fetch_anilist(_QUERY, variables, ttl_ms=CACHE_POLICIES.daily.ttl_ms)
        media = [
            {**m, "averageScore": f"{m['averageScore'] / 10:.1f}" if m.get("averageScore") else None}
            for m in data["Page"]["media"]
        ]
        return {"success": True, "data": {**data["Page"], "media": media}}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
