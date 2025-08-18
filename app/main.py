# app/main.py
from __future__ import annotations

import os, asyncio
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from seo import analyze_url  # import your analyzer function

# === CONFIG ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# FastAPI app
app = FastAPI(title="SEO & Performance Analyzer", version="1.0.0")


# Home route - render index.html
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# API route - run SEO analysis
@app.get("/analyze")
async def analyze(url: str) -> Dict:
    """
    Example:
        /analyze?url=https://example.com
    Returns all SEO & performance scan results as JSON.
    """
    try:
        result = await analyze_url(url)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# Health check
@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "SEO Analyzer running"}
