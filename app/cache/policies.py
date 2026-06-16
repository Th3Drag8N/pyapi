"""
Cache-Control policies mirrored from the JS version.
Each policy is also applied as HTTP Cache-Control headers for CDN-level caching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Response


@dataclass(frozen=True)
class CachePolicy:
    s_max_age: int          # seconds — CDN / shared cache TTL
    stale_while_revalidate: int  # seconds
    ttl_ms: int             # milliseconds — in-process + Redis TTL (derived)

    def __post_init__(self):
        # Allow frozen dataclass to set derived field
        object.__setattr__(self, "ttl_ms", self.s_max_age * 1000)


class CACHE_POLICIES:
    info     = CachePolicy(s_max_age=30 * 24 * 60 * 60, stale_while_revalidate=7 * 24 * 60 * 60, ttl_ms=0)
    stream   = CachePolicy(s_max_age=30 * 60,            stale_while_revalidate=5 * 60,            ttl_ms=0)
    episodes = CachePolicy(s_max_age=15 * 60,            stale_while_revalidate=3 * 60,            ttl_ms=0)
    home     = CachePolicy(s_max_age=20 * 60,            stale_while_revalidate=5 * 60,            ttl_ms=0)
    schedule = CachePolicy(s_max_age=2 * 24 * 60 * 60,  stale_while_revalidate=2 * 60 * 60,       ttl_ms=0)
    daily    = CachePolicy(s_max_age=24 * 60 * 60,       stale_while_revalidate=60 * 60,           ttl_ms=0)
    search   = CachePolicy(s_max_age=10 * 60,            stale_while_revalidate=60,                ttl_ms=0)


def set_cache_headers(response: Response, policy: CachePolicy) -> None:
    response.headers["Cache-Control"] = (
        f"public, s-maxage={policy.s_max_age}, "
        f"stale-while-revalidate={policy.stale_while_revalidate}"
    )
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
