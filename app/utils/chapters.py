"""
WebVTT chapter parser — extracts intro/outro skip data from a chapters track.
Non-critical: returns safe defaults on any failure.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from app.cache.backend import get_client

_INTRO_RE = re.compile(r'\b(op|opening|intro|op\s*\d+)\b', re.IGNORECASE)
_OUTRO_RE = re.compile(r'\b(ed|ending|outro|ed\s*\d+|credit|credits)\b', re.IGNORECASE)


def _vtt_time_to_seconds(ts: str) -> float:
    parts = ts.strip().split(":")
    seconds = 0.0
    if len(parts) == 3:
        seconds += float(parts[0]) * 3600
        seconds += float(parts[1]) * 60
        seconds += float(parts[2])
    elif len(parts) == 2:
        seconds += float(parts[0]) * 60
        seconds += float(parts[1])
    return round(seconds, 3)


def parse_vtt_chapters(text: str) -> list[dict]:
    cues = []
    for block in re.split(r'\n{2,}', text):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        timing = next((l for l in lines if "-->" in l), None)
        if not timing:
            continue
        parts = timing.split("-->")
        if len(parts) < 2:
            continue
        label_line = next(
            (l for l in lines if "-->" not in l and not re.fullmatch(r'\d+', l)),
            None,
        )
        if not label_line:
            continue
        cues.append({
            "start": _vtt_time_to_seconds(parts[0]),
            "end": _vtt_time_to_seconds(parts[1]),
            "label": label_line.strip(),
        })
    return cues


def extract_skip_data(cues: list[dict]) -> dict:
    intro = None
    outro = None
    for cue in cues:
        label = cue.get("label", "")
        if not intro and _INTRO_RE.search(label):
            intro = {"start": cue["start"], "end": cue["end"]}
        if not outro and _OUTRO_RE.search(label):
            outro = {"start": cue["start"], "end": cue["end"]}
        if intro and outro:
            break
    return {"intro": intro, "outro": outro}


async def fetch_chapter_skip_data(chapters_url: str, headers: dict) -> dict:
    client: httpx.AsyncClient = get_client()
    try:
        resp = await asyncio.wait_for(client.get(chapters_url, headers=headers), timeout=8.0)
        if resp.status_code != 200:
            return {"intro": None, "outro": None}
        text = resp.text
        if not text.startswith("WEBVTT"):
            return {"intro": None, "outro": None}
        cues = parse_vtt_chapters(text)
        return extract_skip_data(cues)
    except Exception:
        return {"intro": None, "outro": None}
