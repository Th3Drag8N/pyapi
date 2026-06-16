"""
Stream header utilities.
- Strips internal infra headers (Vercel, Cloudflare, etc.)
- Builds correct Referer + Origin per provider
"""

from __future__ import annotations

from urllib.parse import urlparse

# Headers that must never reach the client
_STRIP = frozenset({
    "x-vercel-id", "x-invocation-id", "x-vercel-cache", "x-vercel-deployment-url",
    "x-vercel-forwarded-for", "x-vercel-ip-city", "x-vercel-ip-continent",
    "x-vercel-ip-country", "x-vercel-ip-latitude", "x-vercel-ip-longitude",
    "x-vercel-ip-timezone", "x-real-ip", "x-forwarded-for", "x-forwarded-host",
    "x-forwarded-proto", "via", "cf-ray", "cf-cache-status", "nel", "report-to",
    "server-timing",
})

# Per-provider Referer/Origin lookup
_PROVIDER_HEADERS: dict[str, dict] = {
    "yuki":   {"Referer": "https://megaplay.buzz/", "Origin": "https://megaplay.buzz"},
    "vee":    {"Referer": "https://megaplay.buzz/", "Origin": "https://megaplay.buzz"},
    "miku":   {"Referer": "https://megaplay.buzz/", "Origin": "https://megaplay.buzz"},
    "kiwi":   {"Referer": "https://megaplay.buzz/", "Origin": "https://megaplay.buzz"},
    "mimi":   {"Referer": "https://megaplay.buzz/", "Origin": "https://megaplay.buzz"},
    "mochi":  {"Referer": "https://animex.one/", "Origin": "https://animex.one"},
    "oppai":  {"Referer": "https://animex.one/", "Origin": "https://animex.one"},
    "beep":   {"Referer": "https://animex.one/", "Origin": "https://animex.one"},
    "uwu":    {"Referer": "https://animex.one/", "Origin": "https://animex.one"},
    "anidap": {"Referer": "https://anidap.se/", "Origin": "https://otakuhg.site"},
}

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def sanitize_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _STRIP}


def build_stream_headers(
    api_headers: dict,
    provider_id: str = "",
    stream_url: str = "",
) -> dict:
    clean = sanitize_headers(api_headers)
    table = _PROVIDER_HEADERS.get(provider_id, {})

    # Derive from stream URL as last resort
    derived_referer = ""
    derived_origin = ""
    if stream_url:
        try:
            parsed = urlparse(stream_url)
            derived_origin = f"{parsed.scheme}://{parsed.netloc}"
            derived_referer = f"{derived_origin}/"
        except Exception:
            pass

    referer = (
        clean.get("Referer") or clean.get("referer")
        or table.get("Referer")
        or derived_referer
    )
    origin = (
        clean.get("Origin") or clean.get("origin")
        or table.get("Origin")
        or derived_origin
        or (urlparse(referer).scheme + "://" + urlparse(referer).netloc if referer else "")
    )

    result = {"User-Agent": clean.get("User-Agent") or _DEFAULT_UA, **clean}
    if referer:
        result["Referer"] = referer
    if origin:
        result["Origin"] = origin

    return result
