# app/main.py
from __future__ import annotations
import sys
import asyncio
from time import time
from typing import Dict, Tuple, Any
import os
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from .seo import analyze as analyze_url
from .db import init_db, save_analysis

# --- Windows asyncio policy fix ---
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# -------------------------------------------------------------------
# Templates setup
# -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
env_templates = os.getenv("TEMPLATES_DIR")
if env_templates and Path(env_templates).exists():
    TEMPLATES_DIR = Path(env_templates).resolve()

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# -------------------------------------------------------------------
# AMP vs Non-AMP comparison cache
# -------------------------------------------------------------------
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
        # ... keep rest of rows as in your code ...
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

# -------------------------------------------------------------------
# FastAPI app
# -------------------------------------------------------------------
app = FastAPI(title="SEO & Performance Analyzer", version="1.0.0")

@app.on_event("startup")
async def on_startup():
    init_db()

# -------------------------------------------------------------------
# Pages
# -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/analyze", response_class=HTMLResponse)
async def analyze_form(request: Request, url: str = Form(...)):
    try:
        result = await analyze_url(url, do_rendered_check=True)

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

# -------------------------------------------------------------------
# API
# -------------------------------------------------------------------
class AnalyzeQuery(BaseModel):
    url: HttpUrl

@app.get("/api/analyze", response_class=JSONResponse)
async def api_analyze(url: HttpUrl):
    try:
        result = await analyze_url(str(url), do_rendered_check=True)

        save_analysis(
            url=str(url),
            result=result,
            status_code=int(result.get("status_code") or 0),
            load_time_ms=int(result.get("load_time_ms") or 0),
            content_length=int(result.get("content_length") or 0),
            is_amp=bool(result.get("is_amp")),
        )

        if result.get("amp_url"):
            asyncio.create_task(_warm_compare_async(result["url"]))

        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# -------------------------------------------------------------------
# AMP vs Non-AMP comparison
# -------------------------------------------------------------------
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

# -------------------------------------------------------------------
# Health
# -------------------------------------------------------------------
@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "SEO Analyzer running"}
