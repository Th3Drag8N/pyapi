# Th3Anime API — Python Rewrite

FastAPI rewrite of `th3anime-api-main` with improved stability, type safety, and async performance.

## Requirements

- Python 3.11+
- pip

## Setup

```bash
pip install -r requirements.txt
```

Copy or edit `.env` with your Upstash credentials (optional — in-memory cache works without Redis):

```
UPSTASH_REDIS_REST_URL=https://...
UPSTASH_REDIS_REST_TOKEN=...
PORT=3030
```

## Run

```bash
python run.py
```

Or with hot-reload for development:

```bash
uvicorn app.main:app --reload --port 3030
```

API docs available at `http://localhost:3030/docs`

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Status page |
| GET | `/api/health` | Health check |
| GET | `/api/home` | Homepage data (spotlights, trending, popular, etc.) |
| GET | `/api/info?id=` | Full anime metadata + streaming availability |
| GET | `/api/episodes?id=` | Episode list with sub/dub/filler flags |
| GET | `/api/servers?id=&ep=` | Available stream servers for an episode |
| GET | `/api/stream?id=&ep=` | Stream sources, subtitles, intro/outro timestamps |
| GET | `/api/search?q=` | Search anime by title, genre, year, season, format |
| GET | `/api/collection?sort=` | Anime list by sort/status |
| GET | `/api/schedule` | Airing schedule (paginated, optional `?date=YYYY-MM-DD`) |
| GET | `/api/schedule.md` | Next 7 days compact schedule |
| GET | `/api/airing?id=` | Next airing episode info for a specific anime |

### Stream params
- `id` — AniList ID
- `ep` / `episode` — episode number (default 1)
- `type` — `sub` or `dub` (default `sub`)
- `site` — force a provider: `anikage`, `animex`, or `anidap`
- `host` — force a specific server host
- `refresh` — `true` to bypass cache

---

## Improvements over the JS version

| Area | JS | Python |
|------|----|--------|
| HTTP client | Native fetch (no pool) | `httpx.AsyncClient` with connection pooling + HTTP/2 |
| Cache LRU | Manual Map eviction | `OrderedDict` proper LRU, O(1) operations |
| In-flight dedup | Promise-based | `asyncio.Event` + result/error maps |
| Rate limiting | Module-level var | Per-client async lock |
| Type safety | None | Full Python type hints throughout |
| Error messages | Generic | Structured with source attribution |
| Logging | `console.warn/error` | Structured `logging` module (JSON-ready) |
| Docs | None | Auto-generated OpenAPI at `/docs` |

## Architecture

```
app/
  main.py              ← FastAPI app, lifespan, CORS
  cache/
    backend.py         ← Two-tier cache: in-process LRU + Upstash Redis REST
    policies.py        ← Cache-Control TTL definitions
  clients/
    anilist.py         ← AniList GraphQL with rate-limit back-off
    jikan.py           ← Jikan v4 (filler flags) with rate-limit spacing
  providers/
    anikage.py         ← XOR+base64url auth, episode/source fetch
    animex.py          ← Slug scraping + REST API
    anidap.py          ← Slug scraping + M3U8 playlist parsing
  utils/
    m3u8.py            ← HLS master playlist quality expansion
    aniskip.py         ← AniSkip intro/outro timestamps
    chapters.py        ← WebVTT chapter → skip data
    headers.py         ← Stream header sanitisation + per-provider lookup
    source_config.py   ← Provider order/selection
  routers/
    home.py            ← /api/home
    info.py            ← /api/info
    episodes.py        ← /api/episodes
    servers.py         ← /api/servers
    stream.py          ← /api/stream
    search.py          ← /api/search
    collection.py      ← /api/collection
    schedule.py        ← /api/schedule + /api/schedule.md
    airing.py          ← /api/airing
    health.py          ← /api/health
```
