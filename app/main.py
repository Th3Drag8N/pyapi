"""
Th3Anime API — Python/FastAPI rewrite
Improvements over the JS version:
  - Typed request/response models (Pydantic)
  - Structured logging (structured JSON in prod)
  - Proper async HTTP with httpx (connection pooling, retries, timeouts)
  - Two-tier cache: in-process OrderedDict LRU + Upstash Redis REST
  - In-flight deduplication via asyncio.Event
  - Rate-limit back-off for AniList & Jikan baked into clients
  - Clean router separation — one file per endpoint group
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.cache.backend import close_http_client, init_http_client
from app.routers import airing, collection, episodes, health, home, info, schedule, search, servers, stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("th3anime")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_http_client()
    logger.info("HTTP client pool initialised")
    yield
    await close_http_client()
    logger.info("HTTP client pool closed")


app = FastAPI(
    title="Th3Anime API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)

# ── Routers ──────────────────────────────────────────────────────────────────
for router in (airing, collection, episodes, health, home, info, schedule, search, servers, stream):
    app.include_router(router.router, prefix="/api")


# ── Root ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Th3-Anime API</title>
  <style>
    :root{--bg:#0a0a0c;--card:#121217;--accent:#5ef1a1;--text:#fff;--muted:#888891}
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--bg);color:var(--text);
         font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",Roboto,sans-serif;
         height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden}
    .container{text-align:center;padding:2rem;position:relative}
    .card{background:var(--card);padding:3rem 4rem;border-radius:24px;
          border:1px solid rgba(255,255,255,.05);box-shadow:0 20px 40px rgba(0,0,0,.4)}
    h1{font-size:2rem;font-weight:800;letter-spacing:-.02em;margin-bottom:1rem;
       background:linear-gradient(135deg,#fff 0%,#888 100%);
       -webkit-background-clip:text;-webkit-text-fill-color:transparent}
    .status-wrapper{display:flex;align-items:center;justify-content:center;
                    gap:12px;color:var(--muted);font-weight:500;font-size:1.1rem}
    .dot{width:10px;height:10px;background:var(--accent);border-radius:50%;
         position:relative;box-shadow:0 0 10px var(--accent)}
    .dot::after{content:'';position:absolute;inset:0;background:var(--accent);
                border-radius:50%;animation:pulse 2s infinite}
    @keyframes pulse{0%{transform:scale(1);opacity:.8}100%{transform:scale(3);opacity:0}}
    .glow{position:absolute;width:300px;height:300px;
          background:radial-gradient(circle,rgba(94,241,161,.05) 0%,transparent 70%);
          top:50%;left:50%;transform:translate(-50%,-50%);pointer-events:none}
  </style>
</head>
<body>
  <div class="glow"></div>
  <div class="container">
    <div class="card">
      <h1>Th3-Anime API</h1>
      <div class="status-wrapper">
        <div class="dot"></div>
        <span>Operational and Running</span>
      </div>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)
