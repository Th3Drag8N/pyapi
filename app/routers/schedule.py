from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from app.cache.policies import CACHE_POLICIES, set_cache_headers
from app.clients.anilist import fetch_anilist

router = APIRouter()

# IST offset (+05:30)
_IST = timezone(timedelta(hours=5, minutes=30))


def _format_schedule(ts: int | None) -> tuple[str | None, str | None]:
    if not ts:
        return None, None
    dt = datetime.fromtimestamp(ts, tz=_IST)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")


def _build_date_range(date: str | None) -> tuple[int, int]:
    if date:
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', date)
        if not m:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        start_dt = datetime(y, mo, d, 0, 0, 0, tzinfo=_IST)
        end_dt   = datetime(y, mo, d, 23, 59, 59, tzinfo=_IST)
        return int(start_dt.timestamp()), int(end_dt.timestamp())

    now = int(datetime.now(timezone.utc).timestamp())
    return now, now + 10 * 24 * 60 * 60


_QUERY_MD = """
query ($start: Int, $end: Int) {
  Page(page: 1, perPage: 40) {
    airingSchedules(airingAt_greater: $start, airingAt_less: $end, sort: TIME) {
      airingAt episode
      media {
        id idMal title { english romaji } coverImage { large } genres averageScore
      }
    }
  }
}
"""

_QUERY_FULL = """
query ($start: Int, $end: Int, $page: Int) {
  Page(page: $page, perPage: 100) {
    pageInfo { hasNextPage total currentPage lastPage }
    airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: [TIME]) {
      airingAt episode
      media {
        id title { english romaji native } coverImage { large }
      }
    }
  }
}
"""


@router.get("/schedule.md")
async def schedule_md(response: Response):
    """Compact upcoming schedule (next 7 days, single page)."""
    set_cache_headers(response, CACHE_POLICIES.schedule)
    from datetime import datetime, timezone
    now = int(datetime.now(timezone.utc).timestamp())
    end = now + 7 * 24 * 60 * 60

    try:
        data = await fetch_anilist(
            _QUERY_MD, {"start": now, "end": end},
            ttl_ms=CACHE_POLICIES.schedule.ttl_ms,
        )
        return {"success": True, "data": data["Page"]["airingSchedules"]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/schedule")
async def schedule(
    response: Response,
    page: int = Query(default=1, ge=1),
    date: Optional[str] = None,
):
    set_cache_headers(response, CACHE_POLICIES.schedule)
    start, end = _build_date_range(date)

    try:
        data = await fetch_anilist(
            _QUERY_FULL,
            {"start": start, "end": end, "page": page},
            ttl_ms=CACHE_POLICIES.schedule.ttl_ms,
        )
        items = data["Page"]["airingSchedules"]
        results = []
        for item in items:
            media = item.get("media") or {}
            title = item.get("media", {}).get("title", {})
            en = title.get("english") or title.get("romaji") or title.get("native") or None
            jp = title.get("native") or title.get("romaji") or title.get("english") or None
            release_date, schedule_time = _format_schedule(item.get("airingAt"))
            results.append({
                "id": media.get("id"),
                "data_id": f"{media.get('id')}&ep={item['episode']}",
                "title": en,
                "japanese_title": jp,
                "poster": (media.get("coverImage") or {}).get("large"),
                "releaseDate": release_date,
                "time": schedule_time,
                "episode_no": item["episode"],
            })
        return {"success": True, "results": results}
    except HTTPException:
        raise
    except Exception as exc:
        return {"success": False, "error": str(exc)}
