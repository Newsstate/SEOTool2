# app/seo.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
import asyncio
import re
import time
import httpx
from playwright.async_api import async_playwright

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

async def fetch_html_async(url: str) -> str:
    """Fetch page HTML using Playwright headless Chromium."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, timeout=30000)
        html = await page.content()
        await browser.close()
        return html

def fetch_html(url: str) -> str:
    """Sync wrapper for Render: runs async inside its own loop."""
    return asyncio.run(fetch_html_async(url))

# ----------------------------------------------------------------------------
# SEO Checks
# ----------------------------------------------------------------------------

def analyze_seo(url: str) -> Dict[str, Any]:
    start = time.time()
    try:
        html = fetch_html(url)
    except Exception as e:
        return {"error": f"Failed to fetch page: {e}"}

    results: Dict[str, Any] = {}
    results["url"] = url
    results["load_time"] = round(time.time() - start, 2)

    # --- Title ---
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    results["title"] = m.group(1).strip() if m else None

    # --- Meta Description ---
    m = re.search(
        r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
        html, re.I
    )
    results["description"] = m.group(1).strip() if m else None

    # --- Canonical ---
    m = re.search(
        r'<link\s+rel=["\']canonical["\']\s+href=["\'](.*?)["\']',
        html, re.I
    )
    results["canonical"] = m.group(1).strip() if m else None

    # --- Headings ---
    headings = {}
    for level in range(1, 7):
        tags = re.findall(fr"<h{level}[^>]*>(.*?)</h{level}>", html, re.I | re.S)
        headings[f"h{level}"] = [re.sub("<.*?>", "", t).strip() for t in tags]
    results["headings"] = headings

    # --- Links ---
    links = re.findall(r"<a[^>]+href=['\"](.*?)['\"]", html, re.I)
    domain = urlparse(url).netloc
    results["internal_links"] = [l for l in links if domain in l or l.startswith("/")]
    results["external_links"] = [l for l in links if domain not in l and not l.startswith("/")]

    return results
