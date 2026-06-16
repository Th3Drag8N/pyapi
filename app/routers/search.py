from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist

router = APIRouter()

_QUERY = """
query ($search: String, $genre: String, $year: Int, $season: MediaSeason, $format: MediaFormat, $page: Int) {
  Page(page: $page, perPage: 20) {
    pageInfo { total currentPage lastPage hasNextPage }
    media(
      search: $search, genre: $genre, seasonYear: $year,
      season: $season, format: $format, type: ANIME, sort: [POPULARITY_DESC]
    ) {
      id idMal title { english romaji native }
      coverImage { large } status episodes averageScore
    }
  }
}
"""


@router.get("/search")
async def search(
    response: Response,
    q: str | None = None,
    genre: str | None = None,
    year: int | None = None,
    season: str | None = None,
    format: str | None = None,
    page: int = Query(default=1, ge=1),
):
    set_cache_headers(response, CACHE_POLICIES.daily)

    variables: dict = {"page": page}
    if q:
        variables["search"] = q
    if genre:
        variables["genre"] = genre
    if year:
        variables["year"] = year
    if season:
        variables["season"] = season.upper()
    if format:
        variables["format"] = format.upper().replace(" ", "_")

    try:
        data = await fetch_anilist(_QUERY, variables, ttl_ms=CACHE_POLICIES.daily.ttl_ms)
        media = [
            {**m, "averageScore": f"{m['averageScore'] / 10:.1f}" if m.get("averageScore") else None}
            for m in data["Page"]["media"]
        ]
        return {"success": True, "data": {**data["Page"], "media": media}}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
