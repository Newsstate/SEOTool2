# app/seo.py
# --------------------------------------------------------------------------------------
# DEV-ONLY WARNING:
# SSL verification is intentionally disabled (verify=False) for local testing.
# Do NOT use verify=False in production or on untrusted networks.
# --------------------------------------------------------------------------------------

from __future__ import annotations

from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
import asyncio
import collections
import json
import os
import re
import time
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv()

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------

UserAgent = os.getenv(
    "CRAWL_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
STOPWORDS = {
    # minimal english stopwords for keyword density
    "the","and","for","are","but","not","you","your","with","have","this","that","was",
    "from","they","his","her","she","him","has","had","were","will","what","when","where",
    "who","why","how","can","all","any","each","few","more","most","other","some","such",
    "no","nor","too","very","of","to","in","on","by","is","as","at","it","or","be","we",
    "an","a","our","us","if","out","up","so","do","did","does","their","its","than","then"
}

# If you want to hardcode, set PSI_API_KEY = "YOUR_KEY". Otherwise use env var.
PSI_API_KEY = os.getenv("PAGESPEED_API_KEY", "").strip() or None

# ======================================================================================
# HTTP helpers
# ======================================================================================

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        headers={
            "User-Agent": UserAgent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
        },
        verify=False,   # (keep as-is if you need it in dev; consider True in prod)
        trust_env=True,
    )


async def fetch(url: str, timeout: float = 25.0) -> Tuple[int, bytes, Dict[str, str], Dict[str, Any]]:
    """
    Fetch raw HTML. Returns: (load_ms, body, headers+status, netinfo)
    """
    async with _client() as client:
        start = time.perf_counter()
        resp = await client.get(
            url,
            timeout=timeout,
        )
        end = time.perf_counter()
        body = resp.content or b""
        headers = {k.lower(): v for k, v in resp.headers.items()}
        netinfo = {
            "http_version": getattr(resp, "http_version", "HTTP/1.1"),
            "final_url": str(resp.url),
            "redirects": len(resp.history),
            "status_code": resp.status_code,
        }
    return int((end - start) * 1000), body, headers, netinfo

def _text(node) -> Optional[str]:
    try:
        return (node.get_text(separator=" ", strip=True) or "").strip()
    except Exception:
        return None

def _norm_urls(urls: List[str]) -> List[str]:
    seen = set()
    out = []
    for u in urls:
        if not u: 
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def _safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return None

# ======================================================================================
# Structured data extraction & validation
# ======================================================================================

def extract_structured_data_full(body: bytes, base_url: str) -> Dict[str, Any]:
    """
    Extract JSON-LD, Microdata (lite), RDFa (lite) as lists.
    """
    soup = BeautifulSoup(body, "lxml")
    json_ld: List[Any] = []
    for tag in soup.find_all("script", type=lambda v: v and "ld+json" in v.lower()):
        txt = tag.string or tag.get_text() or ""
        data = _safe_json_loads(txt)
        if data is None:
            continue
        if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
            json_ld.extend([x for x in data["@graph"] if isinstance(x, dict)])
        elif isinstance(data, list):
            json_ld.extend([x for x in data if isinstance(x, dict)])
        elif isinstance(data, dict):
            json_ld.append(data)

    # Microdata & RDFa (counts)
    microdata = soup.select("[itemscope]")
    rdfa = soup.select("[vocab], [typeof], [property]")

    return {
        "json_ld": json_ld,
        "microdata": [{"count": len(microdata)}] if microdata else [],
        "rdfa": [{"count": len(rdfa)}] if rdfa else [],
    }

def _jsonld_items(jsonld_any: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in jsonld_any or []:
        if isinstance(it, dict):
            out.append(it)
    return out

# Minimal map of types → required fields
_SD_REQUIRED = {
    "Article": ["headline"],
    "BlogPosting": ["headline"],
    "NewsArticle": ["headline"],
    "Product": ["name"],
    "Event": ["name", "startDate"],
    "Organization": ["name"],
    "LocalBusiness": ["name", "address"],
    "FAQPage": ["mainEntity"],
    "HowTo": ["name", "step"],
}

def _sd_required_fields_for(t: str) -> List[str]:
    return _SD_REQUIRED.get(t, [])

def validate_jsonld(jsonld_any: List[Any]) -> Dict[str, Any]:
    items = _jsonld_items(jsonld_any)
    report = []
    for it in items:
        typ = it.get("@type")
        typ_val = (typ[0] if isinstance(typ, list) and typ else (typ or "Unknown"))
        req = _sd_required_fields_for(str(typ_val))
        missing = [f for f in req if f not in it or (isinstance(it.get(f), str) and not it.get(f).strip())]
        report.append({"type": typ_val, "missing": missing, "ok": len(missing) == 0 if req else True})
    summary = {
        "total_items": len(items),
        "ok_count": sum(1 for r in report if r.get("ok")),
        "has_errors": any(r for r in report if not r.get("ok")),
    }
    return {"summary": summary, "items": report}

def _localname(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    if "#" in t:
        t = t.rsplit("#", 1)[-1]
    if "/" in t:
        t = t.rstrip("/").rsplit("/", 1)[-1]
    t = t.strip()
    return t or None

def structured_types_present(jsonld: List[Any], microdata: List[Any], rdfa: List[Any]) -> Dict[str, Any]:
    types: set[str] = set()

    # JSON-LD types
    for item in _jsonld_items(jsonld):
        t = item.get("@type")
        if isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    types.add(_localname(x) or x)
        elif isinstance(t, str):
            types.add(_localname(t) or t)

    # Microdata/RDFa presence already carried as counts above
    return {"types": sorted(types)}

# ======================================================================================
# Keyword density
# ======================================================================================

def _extract_text_for_density(soup: BeautifulSoup) -> str:
    # remove script/style/noscript
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)

def keyword_density(text: str, top_n: int = 10) -> List[Dict[str, Any]]:
    words = re.findall(r"[A-Za-z]{3,}", text.lower())
    freq: Dict[str, int] = {}
    for w in words:
        if w in STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    total = sum(freq.values()) or 1
    items = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    out = []
    for w, c in items:
        out.append({"word": w, "count": c, "percent": round(100.0 * c / total, 2)})
    return out

# ======================================================================================
# Phase 1: Static parse
# ======================================================================================

def parse_html(url: str, body: bytes, headers: Dict[str, str], load_ms: int) -> Dict[str, Any]:
    soup = BeautifulSoup(body, "lxml")
    head = soup.head or soup

    # --- Meta basics
    title = _text(head.title) if head and head.title else None
    desc = None
    robots = None
    for meta in head.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if name in ("description", "og:description"):
            desc = desc or meta.get("content")
        if name == "robots":
            robots = meta.get("content")

    # Canonical
    link_canon = head.find("link", rel=lambda v: v and "canonical" in v.lower())
    canon = urljoin(url, link_canon["href"]) if (link_canon and link_canon.get("href")) else None

    # AMP
    amp_link = head.find("link", rel=lambda v: v and "amphtml" in v.lower())
    amp_url = urljoin(url, amp_link["href"]) if (amp_link and amp_link.get("href")) else None
    is_amp = bool(amp_link) or ("amp-boilerplate" in str(body[:5000]).lower())

    # Headings
    h1 = [_text(h) for h in soup.find_all("h1")]
    h2 = [_text(h) for h in soup.find_all("h2")]
    h3 = [_text(h) for h in soup.find_all("h3")]
    h4 = [_text(h) for h in soup.find_all("h4")]
    h5 = [_text(h) for h in soup.find_all("h5")]
    h6 = [_text(h) for h in soup.find_all("h6")]

    # Links
    a_links = [a.get("href") for a in soup.find_all("a")]
    internal_links, external_links, nofollow_links = [], [], []
    parsed = urlparse(url)
    base_host = parsed.netloc.lower()
    for href in a_links:
        if not href:
            continue
        absu = urljoin(url, href)
        host = urlparse(absu).netloc.lower()
        (internal_links if host == base_host else external_links).append(absu)
    for a in soup.find_all("a"):
        rel = a.get("rel") or []
        if any((r or "").lower() == "nofollow" for r in rel):
            href = a.get("href")
            if href:
                nofollow_links.append(urljoin(url, href))

    internal_links = _norm_urls(internal_links)[:300]
    external_links = _norm_urls(external_links)[:300]
    nofollow_links = _norm_urls(nofollow_links)[:300]

    # Images: missing alts
    imgs = soup.find_all("img")
    missing_alts = []
    with_alt = 0
    for im in imgs:
        alt = (im.get("alt") or "").strip()
        if alt:
            with_alt += 1
            continue
        src = urljoin(url, im.get("src") or "")
        missing_alts.append({"src": src})

    # Structured data
    sd_all = extract_structured_data_full(body, url)
    jsonld = sd_all.get("json_ld") or []
    microdata_any = sd_all.get("microdata") or []
    rdfa_any = sd_all.get("rdfa") or []
    sd_validation = validate_jsonld(jsonld)
    sd_types = structured_types_present(jsonld, microdata_any, rdfa_any)

    # hreflang
    hreflang_rows = []
    for ln in head.find_all("link", rel=lambda v: v and "alternate" in v.lower()):
        href = ln.get("href")
        hreflang = (ln.get("hreflang") or "").strip().lower()
        if href and hreflang:
            hreflang_rows.append({"hreflang": hreflang, "href": urljoin(url, href)})

    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt" if parsed.scheme and parsed.netloc else None

    # --- Quick checks
    checks: Dict[str, Any] = {}

    # Title & meta description length
    title_len = len(title or "")
    desc_len = len((desc or "").strip() or "")
    checks["title_length"] = {"chars": title_len, "ok": 30 <= title_len <= 65}
    checks["meta_description_length"] = {"chars": desc_len, "ok": 70 <= desc_len <= 160}

    # Heading sanity
    checks["h1_count"] = {"count": len(h1), "ok": len(h1) == 1}

    # Viewport
    viewport = head.find("meta", attrs={"name": "viewport"})
    checks["viewport_meta"] = {"present": viewport is not None, "ok": viewport is not None, "value": "present" if viewport else "missing"}

    # Canonical sanity
    checks["canonical"] = {
        "present": canon is not None,
        "absolute": canon.startswith("http") if canon else False,
        "self_ref": canon == url if canon else False,
        "ok": bool(canon and canon.startswith("http")),
        "value": canon or "",
    }

    # IMG alt coverage
    total_imgs = len(imgs)
    checks["alt_coverage"] = {
        "with_alt": with_alt,
        "total_imgs": total_imgs,
        "percent": round((with_alt / total_imgs * 100), 1) if total_imgs else 100.0,
        "ok": (with_alt / total_imgs >= 0.8) if total_imgs else True,
    }

    # Lang & charset
    html_tag = soup.find("html")
    lang_ok = bool(html_tag and html_tag.get("lang"))
    meta_charset = head.find("meta", attrs={"charset": True})
    http_equiv = head.find("meta", attrs={"http-equiv": re.compile("content-type", re.I)})
    charset_ok = bool(meta_charset or http_equiv)
    checks["lang"] = {"ok": lang_ok}
    checks["charset"] = {"ok": charset_ok}

    # Robots meta quick parse
    def _parse_robots_meta(v: Optional[str]) -> Dict[str, Any]:
        v = (v or "").lower()
        return {
            "index": "noindex" not in v,
            "follow": "nofollow" not in v,
            "raw": v,
        }
    rob = _parse_robots_meta(robots)
    checks["robots_meta_index"] = {"value": "index" if rob["index"] else "noindex", "ok": rob["index"]}
    checks["robots_meta_follow"] = {"value": "follow" if rob["follow"] else "nofollow", "ok": rob["follow"]}

    # X-Robots-Tag (header) minimal parse
    def _parse_x_robots(h: Dict[str, str]) -> Dict[str, Any]:
        val = (h.get("x-robots-tag") or "").lower()
        return {"raw": val, "ok": "noindex" not in val}
    checks["x_robots_tag"] = _parse_x_robots(headers)

     # ▼ INSERT HERE — Compression check from response headers
    enc = (headers.get("content-encoding") or "").lower()
    _enc_names = {"br": "Brotli", "gzip": "gzip", "deflate": "deflate", "zstd": "zstd"}
    pretty = None
    for k, v in _enc_names.items():
        if k in enc:
            pretty = v
            break
    checks["compression"] = {
        "ok": bool(pretty),        # True if compression detected
        "value": pretty or "none", # e.g. "Brotli", "gzip", "none"
    }
    # Indexable coarse flag
    indexable = (headers.get("status") or "") != "404" and rob["index"]
    checks["indexable"] = {"value": "Yes" if indexable else "No", "ok": indexable}

    text_for_kd = _extract_text_for_density(soup)
    kd_top = keyword_density(text_for_kd, 10)

    return {
        "url": url,
        "status_code": int(headers.get("status") or 200),
        "load_time_ms": int(load_ms),
        "content_length": int(len(body)),
        "title": title or "",
        "description": desc or "",
        "canonical": canon or "",
        "robots_meta": robots or "",
        "open_graph": {},  # (we only expose presence here)
        "twitter_card": {},
        "has_open_graph": bool(soup.find("meta", property=re.compile(r"^og:", re.I))),
        "has_twitter_card": bool(soup.find("meta", attrs={"name": re.compile(r"^twitter:", re.I)})),
        "headings": {"h1": [h for h in h1 if h], "h2": [h for h in h2 if h], "h3": [h for h in h3 if h], "h4": [h for h in h4 if h], "h5": [h for h in h5 if h], "h6": [h for h in h6 if h]},
        "h1": [h for h in h1 if h],
        "h2": [h for h in h2 if h],
        "h3": [h for h in h3 if h],
        "h4": [h for h in h4 if h],
        "h5": [h for h in h5 if h],
        "h6": [h for h in h6 if h],
        "internal_links": internal_links,
        "external_links": external_links,
        "nofollow_links": nofollow_links,
        "images_missing_alt": missing_alts,
        "hreflang": hreflang_rows,
        "json_ld": jsonld,
        "microdata": microdata_any,
        "rdfa": rdfa_any,
        "sd_types": sd_types,
        "json_ld_validation": sd_validation,
        "is_amp": bool(is_amp),
        "amp_url": amp_url or "",
        "checks": checks,
        "performance": {},   # filled later in analyze()
        "pagespeed": {"enabled": False},
        "link_checks": None,    # filled later
        "crawl_checks": None,   # filled later
        "rendered_diff": {"rendered": False},
        "robots_url": robots_url,
        "keyword_density_top": kd_top,
    }

# ======================================================================================
# Phase 2: link sample checks + robots/sitemaps
# ======================================================================================

async def _check_one(session: httpx.AsyncClient, u: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {"url": u}
    try:
        r = await session.get(u, timeout=10.0, follow_redirects=True)
        item["status"] = r.status_code
        item["final_url"] = str(r.url)
        item["redirects"] = len(r.history)
    except Exception as e:
        item["error"] = str(e)
    return item

async def link_audit(data: Dict[str, Any]) -> Dict[str, Any]:
    internal = (data.get("internal_links") or [])[:30]
    external = (data.get("external_links") or [])[:15]
    out: Dict[str, Any] = {"internal": [], "external": []}
    async with _client() as s:
        res_int = await asyncio.gather(*(_check_one(s, u) for u in internal))
        res_ext = await asyncio.gather(*(_check_one(s, u) for u in external))
    out["internal"] = res_int
    out["external"] = res_ext
    return out

# robots.txt + sitemap discovery/peek
import urllib.robotparser as robotparser

def _discover_sitemaps_from_robots(robots_txt: str) -> List[str]:
    sitemaps: List[str] = []
    for line in robots_txt.splitlines():
        if line.strip().lower().startswith("sitemap:"):
            sm = line.split(":", 1)[1].strip()
            if sm:
                sitemaps.append(sm)
    # de-dup & keep order
    seen = set()
    uniq: List[str] = []
    for sm in sitemaps:
        if sm not in seen:
            uniq.append(sm)
            seen.add(sm)
    return uniq

async def robots_sitemap_audit(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    - Fetch robots.txt (if present)
    - Determine if the scanned URL is blocked for our UA
    - Discover sitemaps **only** from robots.txt (dynamic)
    """
    res: Dict[str, Any] = {"robots_txt": None, "sitemaps": [], "blocked_by_robots": None}
    robots_url = data.get("robots_url")
    target_url = data.get("url") or ""
    async with _client() as s:
        robots_txt = ""
        if robots_url:
            try:
                r = await s.get(robots_url, timeout=8.0)
                robots_txt = r.text if r.status_code == 200 else ""
                res["robots_txt"] = {"url": robots_url, "status": r.status_code, "length": len(r.content)}
            except Exception as e:
                res["robots_txt"] = {"url": robots_url, "error": str(e)}

        # blocked?
        if robots_txt:
            rp = robotparser.RobotFileParser()
            rp.parse(robots_txt.splitlines())
            try:
                allowed = rp.can_fetch(UserAgent, target_url)
                res["blocked_by_robots"] = not bool(allowed)
            except Exception:
                res["blocked_by_robots"] = None

        # discover sitemaps and peek with HEAD (skip sitemap_index.xml full fetch)
        if robots_txt:
            sitemaps = _discover_sitemaps_from_robots(robots_txt)
            for sm in sitemaps:
                if sm.lower().endswith("sitemap_index.xml"):
                    res["sitemaps"].append({"url": sm, "note": "listed in robots; skipped index fetch"})
                    continue
                try:
                    r = await s.head(sm, timeout=8.0, follow_redirects=True)
                    res["sitemaps"].append({"url": sm, "status": r.status_code})
                except Exception as e:
                    res["sitemaps"].append({"url": sm, "error": str(e)})
    return res

# ---- Optional: summarize sitemaps (kept but **skips** sitemap_index.xml fetch) --------

async def summarize_sitemaps(sitemap_urls: List[str]) -> Dict[str, Any]:
    """
    Fetch up to 2 sitemaps (skip *sitemap_index.xml*), count <url> tags (sample).
    """
    out = {"checked": [], "total_url_count_sampled": 0, "is_index": False}
    async with _client() as s:
        checked = 0
        for sm in (sitemap_urls or []):
            if checked >= 2:
                break
            try:
                if sm.lower().endswith("sitemap_index.xml"):
                    out["is_index"] = True
                    out["checked"].append({"url": sm, "note": "index file (not fetched)"})
                    continue
                r = await s.get(sm, timeout=10.0)
                entry = {"url": sm, "status": r.status_code}
                if r.status_code == 200 and (r.headers.get("content-type", "").lower().startswith("application/xml") or r.text.strip().startswith("<")):
                    txt = r.text
                    url_count = txt.lower().count("<url>")
                    entry["url_tags"] = url_count
                    out["total_url_count_sampled"] += url_count
                out["checked"].append(entry)
            except Exception as e:
                out["checked"].append({"url": sm, "error": str(e)})
            checked += 1
    return out

# ======================================================================================
# Phase 3: Rendered DOM (Windows-safe: Playwright sync API in a thread)
# ======================================================================================

from contextlib import asynccontextmanager

RENDER_TIMEOUT_MS = int(os.getenv("RENDER_TIMEOUT_MS", "20000"))  # 20s default
RENDER_WAIT_STATE = os.getenv("RENDER_WAIT_STATE", "networkidle") # or "load"
RENDER_JS_ENABLED = os.getenv("RENDER_JS_ENABLED", "1") == "1"
RENDER_DEBUG = os.getenv("RENDER_DEBUG", "0") == "1"  # set 1 to log errors

PLAYWRIGHT_ARGS = [
    "--disable-gpu",
    "--no-sandbox",  # harmless on Windows; useful in CI/containers
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
    "--disable-features=SitePerProcess",
    "--disable-blink-features=AutomationControlled",
]

PLAYWRIGHT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)

# prefer system browsers to avoid big downloads
PREFERRED_CHANNEL = os.getenv("PW_BROWSER_CHANNEL", "").strip().lower()  # "msedge" or "chrome"
CHROME_PATH = os.getenv("CHROME_PATH", "").strip()  # e.g., r"C:\Program Files\Google\Chrome\Application\chrome.exe"

async def _ensure_playwright():
    try:
        from playwright.async_api import async_playwright  # noqa
        return True
    except Exception:
        return False

@asynccontextmanager
async def _browser_context():
    """
    Try Edge/Chrome channel first, then CHROME_PATH, then bundled Chromium.
    """
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = None
        used = "chromium"
        try:
            # Try preferred channel if provided
            if PREFERRED_CHANNEL in ("chrome", "msedge"):
                used = PREFERRED_CHANNEL
                browser = await p.chromium.launch(channel=PREFERRED_CHANNEL, headless=True, args=PLAYWRIGHT_ARGS)
            elif CHROME_PATH:
                used = "chrome-path"
                browser = await p.chromium.launch(executable_path=CHROME_PATH, headless=True, args=PLAYWRIGHT_ARGS)
            else:
                browser = await p.chromium.launch(headless=True, args=PLAYWRIGHT_ARGS)
        except Exception:
            browser = await p.chromium.launch(headless=True, args=PLAYWRIGHT_ARGS)
            used = "chromium"

        ctx = await browser.new_context(
            user_agent=PLAYWRIGHT_UA,
            java_script_enabled=RENDER_JS_ENABLED,
            locale="en-US",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1366, "height": 768},
            ignore_https_errors=True,
        )
        # reduce detection
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        try:
            yield ctx
        finally:
            await ctx.close()
            await browser.close()

def _render_sync(url: str, user_agent: str, timeout_ms: int) -> dict:
    """
    Returns dict: {"html": Optional[str], "error": Optional[str], "used": str}
    - tries Edge/Chrome channels first, then CHROME_PATH, then bundled Chromium
    - tries wait_until='networkidle' first, then falls back to 'load'
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return {"html": None, "error": f"playwright import failed: {e}", "used": "none"}

    used = []
    try:
        with sync_playwright() as p:
            browser = None

            def try_channel(ch: str):
                nonlocal used
                try:
                    b = p.chromium.launch(channel=ch, headless=True, args=PLAYWRIGHT_ARGS)
                    used.append(ch)
                    return b
                except Exception:
                    return None

            # Try msedge/chrome channel first
            if PREFERRED_CHANNEL in ("msedge", "chrome"):
                browser = try_channel(PREFERRED_CHANNEL)

            # Try executable path if provided
            if not browser and CHROME_PATH:
                try:
                    browser = p.chromium.launch(executable_path=CHROME_PATH, headless=True, args=PLAYWRIGHT_ARGS)
                    used.append("chrome-path")
                except Exception:
                    browser = None

            # Fallback to bundled Chromium
            if not browser:
                browser = p.chromium.launch(headless=True, args=PLAYWRIGHT_ARGS)
                used.append("chromium")

            ctx = browser.new_context(
                user_agent=PLAYWRIGHT_UA,
                java_script_enabled=RENDER_JS_ENABLED,
                locale="en-US",
                timezone_id="Asia/Kolkata",
                viewport={"width": 1366, "height": 768},
                ignore_https_errors=True,
            )
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page = ctx.new_page()
            page.set_default_navigation_timeout(timeout_ms or 20000)

            # first attempt: networkidle
            try:
                page.goto(url, wait_until=RENDER_WAIT_STATE, timeout=timeout_ms or 20000)
            except Exception as e:
                # fallback to 'load'
                try:
                    page.goto(url, wait_until="load", timeout=timeout_ms or 20000)
                except Exception as e2:
                    ctx.close(); browser.close()
                    return {"html": None, "error": f"{e} / fallback: {e2}", "used": ",".join(used)}

            # small extra wait for hydration
            try:
                page.wait_for_timeout(600)
            except Exception:
                pass

            html = page.content()
            ctx.close()
            browser.close()
            return {"html": html, "error": None, "used": ",".join(used)}
    except Exception as e:
        return {"html": None, "error": str(e), "used": ",".join(used) if used else "none"}

async def fetch_rendered(url: str, timeout_ms: int = 30000) -> dict:
    """
    Async wrapper. Returns {"html", "error", "used"}.
    """
    return await asyncio.to_thread(_render_sync, url, UserAgent, timeout_ms)

def _summarize_for_compare(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    head = soup.head or soup

    # meta
    title = _text(head.title) if head and head.title else None
    desc = None
    robots = None
    for meta in head.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if name in ("description", "og:description"):
            desc = desc or meta.get("content")
        if name == "robots":
            robots = meta.get("content")

    # canonical
    canon = None
    link_canon = head.find("link", rel=lambda v: v and "canonical" in v.lower())
    if link_canon and link_canon.get("href"):
        canon = urljoin(url, link_canon["href"])

    # headings
    h1 = [_text(h) for h in soup.find_all("h1")]

    # social quick flags
    og_ok = bool(head.find("meta", property=re.compile(r"^og:", re.I)))
    tw_ok = bool(head.find("meta", attrs={"name": re.compile(r"^twitter:", re.I)}))

    # structured counts
    jsonld_count = len(soup.find_all("script", type=lambda v: v and "ld+json" in v.lower()))
    microdata_count = len(soup.select("[itemscope]"))
    rdfa_count = len(soup.select("[vocab], [typeof], [property]"))

    # internal/external (approx)
    internal = 0
    external = 0
    base_host = urlparse(url).netloc.lower()
    for a in soup.find_all("a", href=True):
        absu = urljoin(url, a["href"])
        host = urlparse(absu).netloc.lower()
        if host == base_host:
            internal += 1
        else:
            external += 1

    # viewport
    viewport_present = head.find("meta", attrs={"name": "viewport"}) is not None

    return {
        "title": title,
        "description": desc,
        "canonical": canon,
        "robots_meta": robots,
        "h1_count": len(h1),
        "h1_first": h1[0] if h1 else None,
        "has_open_graph": og_ok,
        "has_twitter_card": tw_ok,
        "json_ld_count": jsonld_count,
        "microdata_count": microdata_count,
        "rdfa_count": rdfa_count,
        "internal_links_count": internal,
        "external_links_count": external,
        "viewport_present": viewport_present,
    }

def rendered_compare_matrix(original: Dict[str, Any], rendered_html: Optional[str]) -> Dict[str, Any]:
    if not rendered_html:
        return {"rendered": False}

    before = {
        "title": original.get("title"),
        "description": original.get("description"),
        "canonical": original.get("canonical"),
        "robots_meta": original.get("robots_meta"),
        "h1_count": len((original.get("headings") or {}).get("h1", []) or []),
        "h1_first": ((original.get("headings") or {}).get("h1") or [None])[0],
        "has_open_graph": bool(original.get("has_open_graph")),
        "has_twitter_card": bool(original.get("has_twitter_card")),
        "json_ld_count": len(original.get("json_ld") or []),
        "microdata_count": len(original.get("microdata") or []),
        "rdfa_count": len(original.get("rdfa") or []),
        "internal_links_count": len(original.get("internal_links") or []),
        "external_links_count": len(original.get("external_links") or []),
        "viewport_present": bool(original.get("checks", {}).get("viewport_meta", {}).get("present", False)),
    }
    after = _summarize_for_compare(original.get("url", ""), rendered_html)

    def row(label: str, key: str):
        b = before.get(key)
        a = after.get(key)
        return {
            "label": label,
            "before": b if b not in (None, "") else "—",
            "after": a if a not in (None, "") else "—",
            "changed": (b or "") != (a or "")
        }

    matrix = [
        row("Title", "title"),
        row("Meta Description", "description"),
        row("Canonical", "canonical"),
        row("Robots Meta", "robots_meta"),
        row("H1 Count", "h1_count"),
        row("First H1", "h1_first"),
        row("Open Graph present", "has_open_graph"),
        row("Twitter Card present", "has_twitter_card"),
        row("JSON-LD blocks", "json_ld_count"),
        row("Microdata items", "microdata_count"),
        row("RDFa items", "rdfa_count"),
        row("Internal Links (count)", "internal_links_count"),
        row("External Links (count)", "external_links_count"),
        row("Viewport Meta Present", "viewport_present"),
    ]

    quick = {
        "rendered": True,
        "title_changed": before["title"] != after["title"],
        "description_changed": before["description"] != after["description"],
        "h1_count_changed": before["h1_count"] != after["h1_count"],
        "render_excerpt": rendered_html[:2000],
    }
    return {**quick, "matrix": matrix, "before": before, "after": after}

# ======================================================================================
# PageSpeed Insights (optional)
# ======================================================================================

async def fetch_pagespeed(url: str, api_key: Optional[str]) -> Dict[str, Any]:
    if not api_key:
        return {"enabled": False, "error": "No API key"}
    base = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    async with _client() as s:
        out: Dict[str, Any] = {"enabled": True}
        for strat in ("mobile", "desktop"):
            try:
                r = await s.get(base, params={"url": url, "strategy": strat, "key": api_key}, timeout=35.0)
                if r.status_code != 200:
                    out[strat] = {"error": f"HTTP {r.status_code}"}
                    continue
                data = r.json()
                cat = (data.get("lighthouseResult", {}).get("categories", {}).get("performance") or {})
                score = cat.get("score", None)
                audits = data.get("lighthouseResult", {}).get("audits", {})
                def pick_value(audit_key: str, field: str = "displayValue"):
                    a = audits.get(audit_key) or {}
                    return a.get(field)
                out[strat] = {
                    "score": int(score * 100) if isinstance(score, (int, float)) else None,
                    "metrics": {
                        "first-contentful-paint": pick_value("first-contentful-paint"),
                        "speed-index": pick_value("speed-index"),
                        "largest-contentful-paint": pick_value("largest-contentful-paint"),
                        "total-blocking-time": pick_value("total-blocking-time"),
                        "cumulative-layout-shift": pick_value("cumulative-layout-shift"),
                        "server-response-time": pick_value("server-response-time"),
                    }
                }
            except Exception as e:
                out[strat] = {"error": str(e)}
        return out

# =============================================
# Public entrypoint
# =============================================

async def analyze(url: str, *, do_rendered_check: bool = False) -> Dict[str, Any]:
    """
    Main analyzer used by the app.
    - Phase 1: static parse & checks
    - Phase 2: link sampling, robots/sitemap reachability (+ block check)
    - Phase 3 (optional): rendered DOM comparison via Playwright (sync API in a thread)
    - Performance block + (optional) PageSpeed Insights
    """
    # Phase 1
    load_ms, body, headers, netinfo = await fetch(url)
    data = parse_html(url, body, headers, load_ms)

    # Performance snapshot
    final_url = netinfo.get("final_url") or url
    is_https = urlparse(final_url).scheme.lower() == "https"
    data["performance"] = {
        "load_time_ms": load_ms,
        "page_size_bytes": int(headers.get("content-length") or len(body)),
        "http_version": netinfo.get("http_version"),
        "redirects": netinfo.get("redirects"),
        "final_url": final_url,
        "https": {
            "is_https": is_https,
            "ssl_checked": False,  # can't verify due to verify=False
            "ssl_ok": None,
        },
    }

    # Phase 2: link & robots/sitemap checks
    data["link_checks"] = await link_audit(data)
    data["crawl_checks"] = await robots_sitemap_audit(data)

    # Optionally summarize sitemaps discovered from robots
    sm_urls = [s.get("url") for s in (data.get("crawl_checks", {}).get("sitemaps") or []) if s.get("url")]
    if sm_urls:
        data["sitemap_summary"] = await summarize_sitemaps(sm_urls)
    else:
        data["sitemap_summary"] = {"checked": [], "total_url_count_sampled": 0, "is_index": False}

    # Phase 3: Rendered compare
    if do_rendered_check:
        rres = await fetch_rendered(final_url)
        html2 = rres.get("html")
        used = rres.get("used")
        err = rres.get("error")
        if html2:
            data["rendered_diff"] = rendered_compare_matrix(data, html2)
            data["rendered_diff"]["engine"] = used
        else:
            msg = "Playwright render skipped/failed"
            if err:
                msg = f"{msg}: {err} | engine={used}"
            data["rendered_diff"] = {"rendered": False, "error": msg}
            if RENDER_DEBUG:
                # log to console to help diagnose
                print("[RENDER_DEBUG]", msg)

    # PageSpeed Insights (optional)
    if PSI_API_KEY:
        data["pagespeed"] = await fetch_pagespeed(final_url, PSI_API_KEY)
        # Also surface scores at top-level performance block (handy for UI)
        try:
            data["performance"]["mobile_score"] = data["pagespeed"].get("mobile", {}).get("score")
            data["performance"]["desktop_score"] = data["pagespeed"].get("desktop", {}).get("score")
        except Exception:
            pass
    else:
        data["pagespeed"] = {"enabled": False}

    return data
