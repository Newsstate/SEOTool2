# app/seo.py
# --------------------------------------------------------------------------------------
# SEO Analyzer Script
# --------------------------------------------------------------------------------------

from __future__ import annotations
import asyncio, re, json, time, requests
from typing import Dict, Any
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ----------------------------
# Helpers
# ----------------------------
def fetch_static_html(url: str) -> str:
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        return f"<error>{e}</error>"


async def fetch_rendered_html(url: str) -> str:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url, timeout=20000)
            content = await page.content()
            await browser.close()
            return content
    except Exception as e:
        return f"<error>{e}</error>"


# ----------------------------
# SEO Checks
# ----------------------------
def check_meta(soup: BeautifulSoup) -> Dict[str, Any]:
    return {
        "title": soup.title.string.strip() if soup.title else None,
        "description": (soup.find("meta", attrs={"name": "description"}) or {}).get("content"),
        "canonical": (soup.find("link", attrs={"rel": "canonical"}) or {}).get("href"),
        "viewport": (soup.find("meta", attrs={"name": "viewport"}) or {}).get("content"),
        "favicon": (soup.find("link", rel="icon") or {}).get("href"),
        "hreflang": [tag.get("href") for tag in soup.find_all("link", rel="alternate") if tag.get("hreflang")],
    }


def check_headings(soup: BeautifulSoup) -> Dict[str, Any]:
    return {f"H{i}": [h.get_text(strip=True) for h in soup.find_all(f"h{i}")] for i in range(1, 7)}


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


# ----------------------------
# CLI for testing
# ----------------------------
if __name__ == "__main__":
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    res = asyncio.run(analyze_url(test_url))
    print(json.dumps(res, indent=2, ensure_ascii=False))
