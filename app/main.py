# app/main.py
from __future__ import annotations

import os
import asyncio
import time
from typing import Dict, Any

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

# Import our SEO analyzer
from app import seo

# Setup FastAPI
app = FastAPI()
templates = Jinja2Templates(directory="app/templates")


class SEORequest(BaseModel):
    url: HttpUrl


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/analyze", response_class=JSONResponse)
async def analyze_url(url: str = Form(...)) -> Dict[str, Any]:
    """
    Analyze a given URL for SEO, performance, and content metrics.
    """

    start_time = time.time()
    results: Dict[str, Any] = {}

    try:
        # Run all SEO checks from seo.py
        results = await seo.analyze_website(url)

        # Execution time
        results["execution_time"] = round(time.time() - start_time, 2)

    except Exception as e:
        return {"error": str(e)}

    return results


@app.post("/api/analyze", response_class=JSONResponse)
async def analyze_api(request: SEORequest) -> Dict[str, Any]:
    """
    API endpoint for SEO analysis.
    """
    start_time = time.time()
    results: Dict[str, Any] = {}

    try:
        results = await seo.analyze_website(str(request.url))
        results["execution_time"] = round(time.time() - start_time, 2)

    except Exception as e:
        return {"error": str(e)}

    return results
