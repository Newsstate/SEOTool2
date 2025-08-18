from __future__ import annotations
import asyncio
import re
import time
import asyncio, re, json, time, requests
from typing import Dict, Any
from urllib.parse import urljoin, urlparse

import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from requests_html import HTMLSession


# -----------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------

def get_domain(url: str) -> str:
    return urlparse(url).netloc
from playwright.async_api import async_playwright


def fetch_html(url: str) -> str:
    """Fetch static HTML from the URL."""
# ----------------------------
# Helpers
# ----------------------------
def fetch_static_html(url: str) -> str:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        return f"ERROR: {e}"
        return f"<error>{e}</error>"


def fetch_rendered_html(url: str, timeout: int = 20) -> str:
    """Fetch rendered HTML using requests_html (JS execution)."""
async def fetch_rendered_html(url: str) -> str:
    try:
        session = HTMLSession()
        r = session.get(url, timeout=timeout)
        r.html.render(timeout=timeout)
        return r.html.html
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url, timeout=20000)
            content = await page.content()
            await browser.close()
            return content
    except Exception as e:
        return f"ERROR: {e}"


def get_performance_metrics(url: str) -> Dict[str, Any]:
    """Naive performance metrics: load time, size, requests."""
    metrics = {"load_time": None, "size_kb": None, "requests": None}

    try:
        start = time.time()
        resp = requests.get(url, timeout=15)
        load_time = time.time() - start
        metrics["load_time"] = round(load_time, 2)
        metrics["size_kb"] = round(len(resp.content) / 1024, 2)
        metrics["requests"] = len(resp.history) + 1
    except Exception as e:
        metrics["error"] = str(e)

    return metrics


# -----------------------------------------------------------
# Main SEO Checks
# -----------------------------------------------------------

def analyze_seo(url: str) -> Dict[str, Any]:
    results: Dict[str, Any] = {"url": url}
        return f"<error>{e}</error>"

    # --- 1. Fetch HTML ---
    static_html = fetch_html(url)
    rendered_html = fetch_rendered_html(url)

    if "ERROR" in static_html:
        results["error"] = static_html
        return results

    soup = BeautifulSoup(static_html, "html.parser")
    rendered_soup = None
    if rendered_html and "ERROR" not in rendered_html:
        rendered_soup = BeautifulSoup(rendered_html, "html.parser")

    # --- 2. Page Overview ---
    results["overview"] = {
        "domain": get_domain(url),
        "https": url.startswith("https"),
        "status": "OK" if "ERROR" not in static_html else "ERROR",
    }

    # --- 3. Performance ---
    results["performance"] = get_performance_metrics(url)

    # --- 4. Meta Tags ---
    results["meta"] = {
# ----------------------------
# SEO Checks
# ----------------------------
def check_meta(soup: BeautifulSoup) -> Dict[str, Any]:
    return {
        "title": soup.title.string.strip() if soup.title else None,
        "description": (soup.find("meta", attrs={"name": "description"}) or {}).get("content"),
        "canonical": (soup.find("link", rel="canonical") or {}).get("href"),
        "canonical": (soup.find("link", attrs={"rel": "canonical"}) or {}).get("href"),
        "viewport": (soup.find("meta", attrs={"name": "viewport"}) or {}).get("content"),
        "hreflang": [tag.get("href") for tag in soup.find_all("link", attrs={"rel": "alternate", "hreflang": True})],
        "favicon": (soup.find("link", rel="icon") or {}).get("href"),
        "hreflang": [tag.get("href") for tag in soup.find_all("link", rel="alternate") if tag.get("hreflang")],
    }

    # --- 5. Headings ---
    results["headings"] = {f"h{i}": [h.get_text(strip=True) for h in soup.find_all(f"h{i}")]
                           for i in range(1, 7)}

    # --- 6. Links ---
    links = soup.find_all("a", href=True)
    results["links"] = {
        "total": len(links),
        "internal": [a["href"] for a in links if urlparse(a["href"]).netloc in ("", get_domain(url))],
        "external": [a["href"] for a in links if urlparse(a["href"]).netloc not in ("", get_domain(url))],
    }
def check_headings(soup: BeautifulSoup) -> Dict[str, Any]:
    return {f"H{i}": [h.get_text(strip=True) for h in soup.find_all(f"h{i}")] for i in range(1, 7)}

    # --- 7. Images ---
    images = soup.find_all("img")
    results["images"] = {
        "total": len(images),
        "with_alt": len([img for img in images if img.get("alt")]),
        "without_alt": len([img for img in images if not img.get("alt")]),

def check_links(soup: BeautifulSoup, domain: str) -> Dict[str, Any]:
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    internal = [l for l in links if domain in l] if domain else []
    external = [l for l in links if domain not in l] if domain else links
    return {"internal": internal[:20], "external": external[:20], "total": len(links)}


def check_content(soup: BeautifulSoup) -> Dict[str, Any]:
    text = soup.get_text(" ", strip=True)
    words = text.split()
    return {
        "word_count": len(words),
        "text_snippet": " ".join(words[:50]) + "..." if len(words) > 50 else " ".join(words),
    }

    # --- 8. Schema / Structured Data ---
    scripts = soup.find_all("script", type="application/ld+json")
    schemas = []
    for s in scripts:
        try:
            schemas.append(s.get_text(strip=True))
        except Exception:
            continue
    results["schema"] = {"count": len(schemas), "raw": schemas}

    # --- 9. Rendered vs Static (JS comparison) ---
    if rendered_soup:
        static_text = soup.get_text(" ", strip=True)
        rendered_text = rendered_soup.get_text(" ", strip=True)
        diff_ratio = (len(rendered_text) - len(static_text)) / (len(static_text) + 1)
        results["rendered_vs_static"] = {
            "static_length": len(static_text),
            "rendered_length": len(rendered_text),
            "difference_ratio": round(diff_ratio, 2),
        }
    else:
        results["rendered_vs_static"] = {"error": "Could not render JS"}

    return results
def check_performance(url: str) -> Dict[str, Any]:
    # Placeholder for PageSpeed or Lighthouse integration
    return {"status": "ok", "note": "PageSpeed API results injected via main.py"}


# ----------------------------
# MAIN SEO ANALYSIS
# ----------------------------
async def analyze_url(url: str) -> Dict[str, Any]:
    results: Dict[str, Any] = {"url": url, "timestamp": time.time()}

    # Static HTML
    static_html = fetch_static_html(url)
    static_soup = BeautifulSoup(static_html, "html.parser")
    results["meta"] = check_meta(static_soup)
    results["headings"] = check_headings(static_soup)
    results["links"] = check_links(static_soup, urlparse(url).netloc)
    results["content"] = check_content(static_soup)
    results["performance"] = check_performance(url)

    # Rendered HTML
    rendered_html = await fetch_rendered_html(url)
    rendered_soup = BeautifulSoup(rendered_html, "html.parser")

    # Compare static vs rendered
    results["render_comparison"] = {
        "static_h1_count": len(static_soup.find_all("h1")),
        "rendered_h1_count": len(rendered_soup.find_all("h1")),
        "static_text_len": len(static_soup.get_text(" ", strip=True)),
        "rendered_text_len": len(rendered_soup.get_text(" ", strip=True)),
    }

    return results

# -----------------------------------------------------------
# Entry Point
# -----------------------------------------------------------

# ----------------------------
# CLI for testing
# ----------------------------
if __name__ == "__main__":
    test_url = "https://example.com"
    report = analyze_seo(test_url)
    import json
    print(json.dumps(report, indent=2, ensure_ascii=False))
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    res = asyncio.run(analyze_url(test_url))
    print(json.dumps(res, indent=2, ensure_ascii=False))
