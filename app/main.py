# app/main.py
from __future__ import annotations

import os, time
from typing import Dict

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.seo import analyze_seo

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/analyze", response_class=JSONResponse)
async def analyze(url: str = Query(..., description="Target URL to analyze")):
    results: Dict = analyze_seo(url)
    return results
