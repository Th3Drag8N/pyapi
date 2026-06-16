import time

from fastapi import APIRouter, Response

router = APIRouter()

_start_time = time.time()


@router.get("/health")
async def health(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time, 2),
    }
