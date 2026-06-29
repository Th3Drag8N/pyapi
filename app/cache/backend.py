"""
Two-tier cache backend:
  1. In-process OrderedDict LRU (fast, zero latency)
  2. Upstash Redis REST (shared across processes/instances, fire-and-forget writes)

Also exposes the shared HTTP client used everywhere in the app.
Uses curl_cffi for Cloudflare-protected providers (impersonates real Chrome TLS),
and a plain httpx client for non-Cloudflare endpoints (AniList, Jikan, Upstash).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections import OrderedDict
from typing import Any, Callable, Coroutine, Optional

import httpx

logger = logging.getLogger("th3anime.cache")

# ── Shared HTTP clients ───────────────────────────────────────────────────────
# Strategy (in priority order):
#  1. CF Worker proxy  — if WORKER_PROXY_URL is set, route all provider requests
#     through the Cloudflare Worker. CF → CF never gets blocked.
#  2. curl_cffi        — impersonates Chrome TLS fingerprint; beats most CF checks
#     from fresh datacenter IPs but can still get IP-banned over time.
#  3. httpx fallback   — plain HTTP, used when neither above is available.
#
# _http_client is always a plain httpx client used for non-provider endpoints
# (AniList, Jikan, Upstash, AniSkip) which don't need Cloudflare bypass.
_cf_client: Any = None
_http_client: Optional[httpx.AsyncClient] = None

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


class _WorkerProxySession:
    """
    Wraps httpx to route requests through a Cloudflare Worker /proxy endpoint.
    The Worker fetches the target URL from inside CF's own network, bypassing
    any IP-based blocks that affect Render/Vercel datacenter IPs permanently.

    Usage: set WORKER_PROXY_URL=https://your-worker.workers.dev in env vars.
    """

    def __init__(self, proxy_base: str, client: httpx.AsyncClient) -> None:
        self._proxy_base = proxy_base.rstrip("/")
        self._client = client

    async def get(self, url: str, headers: dict | None = None, **_kwargs) -> Any:
        import json as _json
        proxy_url = f"{self._proxy_base}/proxy?url={url}"
        req_headers: dict = {}
        if headers:
            req_headers["x-proxy-headers"] = _json.dumps(headers)
        return await self._client.get(proxy_url, headers=req_headers)


async def init_http_client() -> None:
    global _http_client, _cf_client

    # Plain httpx — for AniList, Jikan, Upstash, AniSkip
    _http_client = httpx.AsyncClient(
        headers=_DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=30),
        http2=True,
    )

    # Priority 1: CF Worker proxy
    proxy_url = os.getenv("WORKER_PROXY_URL", "").strip()
    if proxy_url:
        _cf_client = _WorkerProxySession(proxy_url, _http_client)
        logger.info("CF Worker proxy enabled — provider requests routed via %s", proxy_url)
        return

    # Priority 2: curl_cffi Chrome impersonation
    try:
        from curl_cffi.requests import AsyncSession
        _cf_client = AsyncSession(impersonate="chrome124")
        logger.info("curl_cffi session initialised (Chrome TLS impersonation enabled)")
    except ImportError:
        logger.warning(
            "curl_cffi not installed and WORKER_PROXY_URL not set — "
            "falling back to plain httpx. Provider requests may fail on "
            "datacenter IPs (Render, Vercel). Set WORKER_PROXY_URL to your "
            "Cloudflare Worker URL for a permanent fix."
        )
        _cf_client = None


async def close_http_client() -> None:
    global _http_client, _cf_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
    if _cf_client is not None and hasattr(_cf_client, "close"):
        try:
            result = _cf_client.close()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            pass
        _cf_client = None


def get_client() -> httpx.AsyncClient:
    """Return the plain httpx client (for AniList, Jikan, Upstash, etc.)."""
    if _http_client is None:
        raise RuntimeError("HTTP client not initialised — call init_http_client() first")
    return _http_client


def get_cf_client() -> Any:
    """
    Return the best available client for Cloudflare-protected providers.
    Priority: CF Worker proxy → curl_cffi → plain httpx.
    """
    if _cf_client is not None:
        return _cf_client
    return get_client()



# ── In-process LRU cache ─────────────────────────────────────────────────────
MAX_LOCAL_CACHE_SIZE = 1000
# Serve stale data up to 24 h after expiry if upstream fails
STALE_FALLBACK_TTL_MS = 24 * 60 * 60 * 1000

_local_cache: OrderedDict[str, dict] = OrderedDict()
_inflight: dict[str, asyncio.Event] = {}
_inflight_results: dict[str, Any] = {}
_inflight_errors: dict[str, Exception] = {}


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _set_local(key: str, value: Any, ttl_ms: int) -> None:
    if len(_local_cache) >= MAX_LOCAL_CACHE_SIZE and key not in _local_cache:
        # Evict expired first
        now = _now_ms()
        expired = [k for k, v in _local_cache.items() if v["expires_at"] <= now]
        for k in expired:
            _local_cache.pop(k, None)
        # Still full → evict oldest (LRU: first inserted)
        if len(_local_cache) >= MAX_LOCAL_CACHE_SIZE:
            _local_cache.popitem(last=False)

    _local_cache[key] = {"value": value, "expires_at": _now_ms() + ttl_ms}
    _local_cache.move_to_end(key)  # mark as recently used


def _get_local(key: str) -> Optional[Any]:
    entry = _local_cache.get(key)
    if entry and entry["expires_at"] > _now_ms():
        _local_cache.move_to_end(key)
        return entry["value"]
    return None


def _get_stale(key: str) -> Optional[Any]:
    """Return stale value if still within fallback window."""
    entry = _local_cache.get(key)
    if entry and entry["expires_at"] > _now_ms() - STALE_FALLBACK_TTL_MS:
        return entry["value"]
    return None


# ── Upstash Redis REST helpers ───────────────────────────────────────────────
def _upstash_config() -> Optional[dict]:
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
    if not url or not token:
        return None
    return {"url": url, "token": token}


def _upstash_headers(cfg: dict) -> dict:
    return {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }


def _cache_key(key: str) -> str:
    return f"th3anime:{key}"


async def _read_upstash(key: str) -> Optional[Any]:
    cfg = _upstash_config()
    if not cfg:
        return None
    try:
        client = get_client()
        resp = await asyncio.wait_for(
            client.get(
                f"{cfg['url']}/get/{_cache_key(key)}",
                headers=_upstash_headers(cfg),
            ),
            timeout=2.0,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        result = payload.get("result")
        if result is None:
            return None
        return json.loads(result)
    except Exception:
        return None


def _write_upstash_bg(key: str, value: Any, ttl_ms: int) -> None:
    """Fire-and-forget cache write — never blocks the response."""
    cfg = _upstash_config()
    if not cfg:
        return

    async def _write():
        try:
            ttl_sec = max(1, math.ceil(ttl_ms / 1000))
            client = get_client()
            await asyncio.wait_for(
                client.post(
                    f"{cfg['url']}/pipeline",
                    headers=_upstash_headers(cfg),
                    json=[["SETEX", _cache_key(key), ttl_sec, json.dumps(value)]],
                ),
                timeout=4.0,
            )
        except Exception:
            pass  # cache write failure is non-fatal

    asyncio.ensure_future(_write())


# ── Public API ────────────────────────────────────────────────────────────────
async def get_cached_value(
    key: str,
    ttl_ms: int,
    loader: Callable[[], Coroutine[Any, Any, Any]],
    force_refresh: bool = False,
) -> Any:
    """
    Fetch a value with two-tier cache + in-flight deduplication.

    Priority (cache hits, when force_refresh=False):
      1. In-process LRU
      2. Upstash Redis
      3. In-flight dedup (only ONE upstream call per key)
    On upstream failure: serve stale data within 24 h window before raising.
    """
    if not force_refresh:
        # 1. Local cache
        cached = _get_local(key)
        if cached is not None:
            return cached

        # 2. Redis
        external = await _read_upstash(key)
        if external is not None:
            _set_local(key, external, ttl_ms)
            return external

        # 3. In-flight dedup
        if key in _inflight:
            event = _inflight[key]
            await event.wait()
            if key in _inflight_errors:
                raise _inflight_errors[key]
            return _inflight_results[key]

    # We are the "leader" request
    event = asyncio.Event()
    _inflight[key] = event

    try:
        value = await loader()
        _set_local(key, value, ttl_ms)
        _write_upstash_bg(key, value, ttl_ms)
        _inflight_results[key] = value
        return value
    except Exception as exc:
        stale = _get_stale(key)
        if stale is not None:
            logger.warning("Loader failed for '%s', serving stale data: %s", key, exc)
            _inflight_results[key] = stale
            return stale
        _inflight_errors[key] = exc
        raise
    finally:
        event.set()
        _inflight.pop(key, None)
        # Clean up result/error maps after a short delay so waiters can read them
        async def _cleanup():
            await asyncio.sleep(0.1)
            _inflight_results.pop(key, None)
            _inflight_errors.pop(key, None)
        asyncio.ensure_future(_cleanup())
