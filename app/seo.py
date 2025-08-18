# app/seo.py
# --------------------------------------------------------------------------------------
# SEO Analyzer Script
# --------------------------------------------------------------------------------------

from __future__ import annotations
import asyncio
import re
import time
from typing import Dict, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests_html import HTMLSession


# -----------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------

def get_domain(url: str) -> str:
    return urlparse(url).netloc


def fetch_html(url: str) -> str:
    """Fetch static HTML from the URL."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        return f"ERROR: {e}"


def fetch_rendered_html(url: str, timeout: int = 20) -> str:
    """Fetch rendered HTML using requests_html (JS execution)."""
    try:
        session = HTMLSession()
        r = session.get(url, timeout=timeout)
        r.html.render(timeout=timeout)
        return r.html.html
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
        "title": soup.title.string.strip() if soup.title else None,
        "description": (soup.find("meta", attrs={"name": "description"}) or {}).get("content"),
        "canonical": (soup.find("link", rel="canonical") or {}).get("href"),
        "viewport": (soup.find("meta", attrs={"name": "viewport"}) or {}).get("content"),
        "hreflang": [tag.get("href") for tag in soup.find_all("link", attrs={"rel": "alternate", "hreflang": True})],
        "favicon": (soup.find("link", rel="icon") or {}).get("href"),
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

    # --- 7. Images ---
    images = soup.find_all("img")
    results["images"] = {
        "total": len(images),
        "with_alt": len([img for img in images if img.get("alt")]),
        "without_alt": len([img for img in images if not img.get("alt")]),
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


# -----------------------------------------------------------
# Entry Point
# -----------------------------------------------------------

if __name__ == "__main__":
    test_url = "https://example.com"
    report = analyze_seo(test_url)
    import json
    print(json.dumps(report, indent=2, ensure_ascii=False))
