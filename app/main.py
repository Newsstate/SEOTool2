from __future__ import annotations
import sys
import asyncio
from time import time
from typing import Dict, Tuple, Any
from pathlib import Path
import os
import requests   # ✅ needed for /compare_rendered_static

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from .seo import analyze as analyze_url, fetch_rendered_html
from .db import init_db, save_analysis


app = FastAPI(title="SEO Analyzer")

# --- Windows asyncio policy fix (safe no-op elsewhere) ---
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# Resolve templates dir relative to this file: app/templates
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Optional: allow override via env var if you ever need it
env_templates = os.getenv("TEMPLATES_DIR")
if env_templates:
    candidate = Path(env_templates).resolve()
    if candidate.exists():
        TEMPLATES_DIR = candidate

# Create the Jinja environment
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ------------------------------------------------------------------------------
# AMP vs Non-AMP comparison: cache + helpers
# ------------------------------------------------------------------------------

COMPARE_CACHE: Dict[str, Tuple[float, dict]] = {}
COMPARE_TTL = 15 * 60  # 15 minutes


def _val(d: Any, *path: str, default=None):
    cur = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            cur = None
        if cur is None:
            return default
    return cur if cur not in (None, "") else default


def _yesno(b: Any) -> str:
    return "Yes" if bool(b) else "No"


async def build_amp_compare_payload(url: str, request: Request | None):
    base = await analyze_url(url, do_rendered_check=False)
    amp_url = base.get("amp_url")
    if not amp_url:
        return {
            "request": request,
            "url": url,
            "amp_url": None,
            "rows": [],
            "error": "No AMP version found via <link rel='amphtml'>.",
        }

    amp = await analyze_url(amp_url, do_rendered_check=False)

    rows = [
        {
            "label": "Title",
            "non_amp": _val(base, "title", default="—"),
            "amp": _val(amp, "title", default="—"),
            "changed": _val(base, "title") != _val(amp, "title"),
        },
        {
            "label": "Meta Description",
            "non_amp": _val(base, "description", default="—"),
            "amp": _val(amp, "description", default="—"),
            "changed": _val(base, "description") != _val(amp, "description"),
        },
        {
            "label": "Canonical",
            "non_amp": _val(base, "canonical", default="—"),
            "amp": _val(amp, "canonical", default="—"),
            "changed": _val(base, "canonical") != _val(amp, "canonical"),
        },
        {
            "label": "Robots Meta",
            "non_amp": _val(base, "robots_meta", default="—"),
            "amp": _val(amp, "robots_meta", default="—"),
            "changed": _val(base, "robots_meta") != _val(amp, "robots_meta"),
        },
        {
            "label": "H1 Count",
            "non_amp": len(_val(base, "headings", "h1", default=[]) or []),
            "amp": len(_val(amp, "headings", "h1", default=[]) or []),
            "changed": len(_val(base, "headings", "h1", default=[]) or [])
            != len(_val(amp, "headings", "h1", default=[]) or []),
        },
        {
            "label": "First H1",
            "non_amp": (_val(base, "headings", "h1", default=[None]) or [None])[0] or "—",
            "amp": (_val(amp, "headings", "h1", default=[None]) or [None])[0] or "—",
            "changed": (_val(base, "headings", "h1", default=[None]) or [None])[0]
            != (_val(amp, "headings", "h1", default=[None]) or [None])[0],
        },
        {
            "label": "Open Graph present",
            "non_amp": _yesno(base.get("has_open_graph")),
            "amp": _yesno(amp.get("has_open_graph")),
            "changed": bool(base.get("has_open_graph")) != bool(amp.get("has_open_graph")),
        },
        {
            "label": "Twitter Card present",
            "non_amp": _yesno(base.get("has_twitter_card")),
            "amp": _yesno(amp.get("has_twitter_card")),
            "changed": bool(base.get("has_twitter_card")) != bool(amp.get("has_twitter_card")),
        },
        {
            "label": "JSON-LD blocks",
            "non_amp": len(base.get("json_ld") or []),
            "amp": len(amp.get("json_ld") or []),
            "changed": len(base.get("json_ld") or []) != len(amp.get("json_ld") or []),
        },
        {
            "label": "Microdata items",
            "non_amp": len(base.get("microdata") or []),
            "amp": len(amp.get("microdata") or []),
            "changed": len(base.get("microdata") or []) != len(amp.get("microdata") or []),
        },
        {
            "label": "RDFa items",
            "non_amp": len(base.get("rdfa") or []),
            "amp": len(amp.get("rdfa") or []),
            "changed": len(base.get("rdfa") or []) != len(amp.get("rdfa") or []),
        },
        {
            "label": "Internal links (count)",
            "non_amp": len(base.get("internal_links") or []),
            "amp": len(amp.get("internal_links") or []),
            "changed": len(base.get("internal_links") or []) != len(amp.get("internal_links") or []),
        },
        {
            "label": "External links (count)",
            "non_amp": len(base.get("external_links") or []),
            "amp": len(amp.get("external_links") or []),
            "changed": len(base.get("external_links") or []) != len(amp.get("external_links") or []),
        },
        {
            "label": "Viewport meta present",
            "non_amp": _yesno(_val(base, "checks", "viewport_meta", "present", default=False)),
            "amp": _yesno(_val(amp, "checks", "viewport_meta", "present", default=False)),
            "changed": bool(_val(base, "checks", "viewport_meta", "present", default=False))
            != bool(_val(amp, "checks", "viewport_meta", "present", default=False)),
        },
    ]

    return {
        "request": request,
        "url": url,
        "amp_url": amp_url,
        "rows": rows,
        "error": None,
    }


def _cache_put(url: str, payload: dict):
    COMPARE_CACHE[url] = (time(), payload)


def _cache_get(url: str) -> dict | None:
    hit = COMPARE_CACHE.get(url)
    if not hit:
        return None
    ts, payload = hit
    if time() - ts > COMPARE_TTL:
        return None
    return payload


async def _warm_compare_async(url: str):
    try:
        payload = await build_amp_compare_payload(url, request=None)
        _cache_put(url, payload)
    except Exception:
        pass


# ------------------------------------------------------------------------------
# Startup
# ------------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    init_db()


# ------------------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/analyze", response_class=HTMLResponse)
async def analyze_form(request: Request, url: str = Form(...)):
    try:
        result = await analyze_url(url, do_rendered_check=True)

        # Add rendered HTML
        rendered_html = await fetch_rendered_html(url)
        result["rendered_html"] = rendered_html

        # Persist
        save_analysis(
            url=url,
            result=result,
            status_code=int(result.get("status_code") or 0),
            load_time_ms=int(result.get("load_time_ms") or 0),
            content_length=int(result.get("content_length") or 0),
            is_amp=bool(result.get("is_amp")),
        )

        if result.get("amp_url"):
            asyncio.create_task(_warm_compare_async(result["url"]))

        return templates.TemplateResponse("index.html", {"request": request, "result": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/compare_rendered_static")
async def compare_rendered_static(url: str):
    static_html = requests.get(url).text
    rendered_html = await fetch_rendered_html(url)
    return {"static": static_html, "rendered": rendered_html}


# ------------------------------------------------------------------------------
# AMP vs Non-AMP comparison page
# ------------------------------------------------------------------------------

@app.get("/amp-compare", response_class=HTMLResponse)
async def amp_compare(request: Request, url: str):
    cached = _cache_get(url)
    if cached:
        payload = dict(cached)
        payload["request"] = request
        return templates.TemplateResponse("amp_compare.html", payload)

    payload = await build_amp_compare_payload(url, request)
    _cache_put(url, dict(payload, request=None))
    return templates.TemplateResponse("amp_compare.html", payload)
