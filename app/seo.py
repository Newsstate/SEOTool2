# app/seo.py
from __future__ import annotations

import os
import re
import json
import math
import tldextract
from typing import Any, Dict, List, Tuple, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, SoupStrainer

# -----------------------------
# Playwright (JS-rendered pass)
# -----------------------------
try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout  # type: ignore
    _PLAYWRIGHT_AVAILABLE = True
except Exception:
    async_playwright = None  # type: ignore
    PWTimeout = Exception     # type: ignore
    _PLAYWRIGHT_AVAILABLE = False

UA_DEFAULT = os.getenv(
    "RENDER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)
RENDER_NAV_WAIT = os.getenv("RENDER_NAV_WAIT", "domcontentloaded")  # or "networkidle"
RENDER_EXTRA_WAIT_MS = int(os.getenv("RENDER_EXTRA_WAIT_MS", "600"))
RENDER_MAX_TIME_MS = int(os.getenv("RENDER_MAX_TIME_MS", "15000"))
RENDER_LOCALE = os.getenv("RENDER_LOCALE", "en-US")
RENDER_TZ = os.getenv("RENDER_TIMEZONE", "Asia/Kolkata")
VIEW_W = int(os.getenv("RENDER_VIEWPORT_WIDTH", "1366"))
VIEW_H = int(os.getenv("RENDER_VIEWPORT_HEIGHT", "768"))
BLOCK_MEDIA = os.getenv("RENDER_BLOCK_MEDIA", "1") not in ("0", "false", "False")


def _norm_url(url: str) -> str:
    if not url:
        return url
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    elif not (u.startswith("http://") or u.startswith("https://")):
        u = "https://" + u
    parts = urlsplit(u)
    netloc = parts.netloc.lower()
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


async def render_page(url: str) -> Dict[str, Any]:
    """
    Try to render the page with Playwright. Never raises; returns a dict.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return {
            "html": None,
            "ok": False,
            "status": None,
            "engine": "playwright-chromium",
            "error": "Playwright not installed/available in this environment.",
        }

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                user_agent=UA_DEFAULT,
                locale=RENDER_LOCALE,
                timezone_id=RENDER_TZ,
                viewport={"width": VIEW_W, "height": VIEW_H},
                device_scale_factor=1.0,
                java_script_enabled=True,
                ignore_https_errors=True,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "DNT": "1",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            # reduce detection
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            if BLOCK_MEDIA:
                async def route_filter(route):
                    r = route.request
                    if r.resource_type in ("image", "media", "font"):
                        return await route.abort()
                    return await route.continue_()
                await context.route("**/*", route_filter)

            page = await context.new_page()
            page.set_default_navigation_timeout(RENDER_MAX_TIME_MS)
            resp = await page.goto(url, wait_until=RENDER_NAV_WAIT, timeout=RENDER_MAX_TIME_MS)
            status = resp.status if resp else None
            if RENDER_EXTRA_WAIT_MS > 0:
                await page.wait_for_timeout(RENDER_EXTRA_WAIT_MS)
            html = await page.content()
            await context.close()
            await browser.close()
            return {"html": html, "ok": True, "status": status, "engine": "playwright-chromium", "error": None}
    except PWTimeout as e:
        return {"html": None, "ok": False, "status": None, "engine": "playwright-chromium", "error": f"Render timeout: {e}"}
    except Exception as e:
        return {"html": None, "ok": False, "status": None, "engine": "playwright-chromium", "error": f"Render failed: {e}"}


# -----------------------------
# Static fetch & helpers
# -----------------------------

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
HEADERS = {
    "User-Agent": UA_DEFAULT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

async def fetch(url: str) -> Tuple[Optional[httpx.Response], Optional[bytes], int]:
    status = 0
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=DEFAULT_TIMEOUT, headers=HEADERS) as client:
            resp = await client.get(url)
            status = resp.status_code
            body = resp.content or b""
            return resp, body, status
    except Exception:
        return None, None, status


def extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    m = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "og:title"})
    if m and m.get("content"):
        return m["content"].strip()
    return ""


def extract_meta_description(soup: BeautifulSoup) -> str:
    m = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if m and m.get("content"):
        return m["content"].strip()
    return ""


def extract_canonical(soup: BeautifulSoup, base_url: str) -> str:
    link = soup.find("link", rel=lambda v: v and "canonical" in v.lower())
    href = (link.get("href") or "").strip() if link else ""
    if href.startswith("//"):
        parts = urlsplit(base_url)
        href = f"{parts.scheme}:{href}"
    return href


def extract_robots_meta(soup: BeautifulSoup) -> str:
    m = soup.find("meta", attrs={"name": re.compile(r"robots", re.I)})
    return (m.get("content") or "").strip() if m else ""


def extract_viewport_meta(soup: BeautifulSoup) -> str:
    m = soup.find("meta", attrs={"name": re.compile(r"viewport", re.I)})
    return (m.get("content") or "").strip() if m else ""


def extract_open_graph(soup: BeautifulSoup) -> Dict[str, str]:
    og: Dict[str, str] = {}
    for m in soup.select("meta[property^=og:]"):
        prop = m.get("property") or ""
        cont = m.get("content") or ""
        if prop:
            og[prop] = cont
    return og


def extract_twitter_card(soup: BeautifulSoup) -> Dict[str, str]:
    tw: Dict[str, str] = {}
    for m in soup.select("meta[name^=twitter:]"):
        name = m.get("name") or ""
        cont = m.get("content") or ""
        if name:
            tw[name] = cont
    return tw


def extract_hreflang(soup: BeautifulSoup) -> List[Dict[str, str]]:
    out = []
    for link in soup.find_all("link", rel="alternate"):
        hreflang = (link.get("hreflang") or "").strip()
        href = (link.get("href") or "").strip()
        if hreflang and href:
            out.append({"hreflang": hreflang, "href": href})
    return out


def extract_headings(soup: BeautifulSoup) -> Dict[str, List[str]]:
    d: Dict[str, List[str]] = {f"h{i}": [] for i in range(1, 7)}
    for i in range(1, 7):
        for h in soup.find_all(f"h{i}"):
            txt = h.get_text(separator=" ", strip=True)
            if txt:
                d[f"h{i}"].append(txt)
    return d


def extract_images_missing_alt(soup: BeautifulSoup) -> List[Dict[str, str]]:
    out = []
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if not alt:
            src = (img.get("src") or "").strip()
            if src:
                out.append({"src": src})
    return out


def extract_links(soup: BeautifulSoup, base_url: str) -> Tuple[List[str], List[str], List[str]]:
    internals: List[str] = []
    externals: List[str] = []
    nofollow: List[str] = []
    if not base_url:
        return internals, externals, nofollow

    base_host = urlsplit(base_url).netloc.lower()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        rel = a.get("rel") or []
        try:
            # normalize
            if href.startswith("//"):
                href = f"{urlsplit(base_url).scheme}:{href}"
            elif href.startswith("/"):
                p = urlsplit(base_url)
                href = f"{p.scheme}://{p.netloc}{href}"
            elif not href.startswith("http://") and not href.startswith("https://"):
                # skip non-http schemes
                continue
        except Exception:
            continue

        host = urlsplit(href).netloc.lower()
        if host == base_host:
            internals.append(href)
        else:
            externals.append(href)
        if isinstance(rel, list) and any(r.lower() == "nofollow" for r in rel):
            nofollow.append(href)

    # trim duplicates
    def _dedupe(arr: List[str]) -> List[str]:
        seen = set()
        out = []
        for u in arr:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    return _dedupe(internals), _dedupe(externals), _dedupe(nofollow)


def extract_json_ld(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for tag in soup.find_all("script", type=lambda v: v and "ld+json" in v):
        try:
            txt = tag.string or tag.get_text() or ""
            data = json.loads(txt)
            # unpack @graph if needed
            if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
                for it in data["@graph"]:
                    if isinstance(it, dict):
                        blocks.append(it)
            else:
                if isinstance(data, list):
                    for it in data:
                        if isinstance(it, dict):
                            blocks.append(it)
                elif isinstance(data, dict):
                    blocks.append(data)
        except Exception:
            continue
    return blocks


def extract_microdata_count(soup: BeautifulSoup) -> int:
    return sum(1 for _ in soup.select("[itemscope]"))


def extract_rdfa_count(soup: BeautifulSoup) -> int:
    # crude heuristic
    return sum(1 for _ in soup.select("[vocab], [typeof], [property]"))


def keyword_density(text: str, top_n: int = 10) -> List[Dict[str, Any]]:
    words = re.findall(r"[A-Za-z]{3,}", text.lower())
    freq: Dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    total = sum(freq.values()) or 1
    items = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    out = []
    for w, c in items:
        out.append({"word": w, "count": c, "percent": round(100.0 * c / total, 2)})
    return out


def is_amp_page(soup: BeautifulSoup) -> Tuple[bool, str]:
    # check html ⚡ or amp attribute
    html = soup.find("html")
    attr_amp = False
    if html:
        for key in ("amp", "⚡"):
            if key in html.attrs:
                attr_amp = True
                break
    amp_link = ""
    link = soup.find("link", rel=lambda v: v and "amphtml" in v)
    if link and link.get("href"):
        amp_link = link["href"].strip()
    return attr_amp or bool(amp_link), amp_link


# ---------------------------------
# Main public entry: analyze(url)
# ---------------------------------

async def analyze(url: str, do_rendered_check: bool = True) -> Dict[str, Any]:
    """
    Returns a dictionary used by templates. Always safe (no exceptions bubble up).
    """
    url = _norm_url(url)
    result: Dict[str, Any] = {
        "url": url,
        "status_code": 0,
        "load_time_ms": 0,
        "content_length": 0,
        "title": "",
        "description": "",
        "canonical": "",
        "robots_meta": "",
        "has_open_graph": False,
        "has_twitter_card": False,
        "open_graph": {},
        "twitter_card": {},
        "headings": {f"h{i}": [] for i in range(1, 7)},
        "h1": [],
        "h2": [],
        "h3": [],
        "h4": [],
        "h5": [],
        "h6": [],
        "internal_links": [],
        "external_links": [],
        "nofollow_links": [],
        "images_missing_alt": [],
        "hreflang": [],
        "json_ld": [],
        "microdata": [],
        "rdfa": [],
        "sd_types": {"types": []},
        "is_amp": False,
        "amp_url": "",
        "checks": {},              # filled below (safe to be empty)
        "performance": {},         # filled below
        "pagespeed": {"enabled": False, "message": "PageSpeed API not configured"},
        "link_checks": None,       # optional; keep None to avoid heavy work
        "crawl_checks": None,      # optional; set later if you add robots/sitemaps fetch
        "rendered_diff": {"engine": None, "matrix": []},  # always present
    }

    # --------------------
    # Static fetch/parse
    # --------------------
    try:
        import time
        t0 = time.perf_counter()
        resp, body, status = await fetch(url)
        t1 = time.perf_counter()

        result["status_code"] = int(status or 0)
        result["load_time_ms"] = int((t1 - t0) * 1000)
        result["content_length"] = int(len(body or b"0"))

        if not body:
            # Nothing else to do; rendered_diff remains (possibly error on render step)
            if do_rendered_check:
                r = await render_page(url)
                result["rendered_diff"]["engine"] = r.get("engine")
                if not r["ok"]:
                    result["rendered_diff"]["error"] = r.get("error") or f"Status {r.get('status')}"
            return result

        # Parse static HTML
        soup = BeautifulSoup(body, "html.parser")

        # Basic meta & structure
        title = extract_title(soup)
        desc = extract_meta_description(soup)
        canonical = extract_canonical(soup, url)
        robots_meta = extract_robots_meta(soup)
        viewport_meta = extract_viewport_meta(soup)

        og = extract_open_graph(soup)
        tw = extract_twitter_card(soup)
        headings = extract_headings(soup)
        h1 = headings.get("h1", [])
        h2 = headings.get("h2", [])

        internal, external, nofollow = extract_links(soup, url)
        imgs_missing_alt = extract_images_missing_alt(soup)
        hreflang = extract_hreflang(soup)

        json_ld_blocks = extract_json_ld(soup)
        micro_count = extract_microdata_count(soup)
        rdfa_count = extract_rdfa_count(soup)

        text_content = soup.get_text(separator=" ", strip=True)
        kd_top = keyword_density(text_content, top_n=10)

        amp_flag, amp_href = is_amp_page(soup)

        # Fill result
        result.update({
            "title": title,
            "description": desc,
            "canonical": canonical,
            "robots_meta": robots_meta,
            "open_graph": og,
            "twitter_card": tw,
            "has_open_graph": bool(og),
            "has_twitter_card": bool(tw),
            "headings": headings,
            "h1": h1,
            "h2": h2,
            "h3": headings.get("h3", []),
            "h4": headings.get("h4", []),
            "h5": headings.get("h5", []),
            "h6": headings.get("h6", []),
            "internal_links": internal,
            "external_links": external,
            "nofollow_links": nofollow,
            "images_missing_alt": imgs_missing_alt,
            "hreflang": hreflang,
            "json_ld": json_ld_blocks,
            "microdata": [{"count": micro_count}] if micro_count else [],
            "rdfa": [{"count": rdfa_count}] if rdfa_count else [],
            "keyword_density_top": kd_top,
            "is_amp": bool(amp_flag),
            "amp_url": amp_href,
        })

        # Checks (lightweight; expand as you like)
        checks: Dict[str, Any] = {}

        # title length
        tlen = len(title or "")
        checks["title_length"] = {"chars": tlen, "ok": 10 <= tlen <= 60}

        # meta description length
        dlen = len(desc or "")
        checks["meta_description_length"] = {"chars": dlen, "ok": 50 <= dlen <= 160}

        # viewport meta presence
        checks["viewport_meta"] = {
            "present": bool(viewport_meta),
            "ok": bool(viewport_meta),
            "value": viewport_meta or "",
        }

        # canonical present
        checks["canonical"] = {
            "present": bool(canonical),
            "ok": bool(canonical),
        }

        # h1 count
        checks["h1_count"] = {"value": len(h1), "ok": len(h1) == 1}

        # alt coverage (simple)
        total_imgs = len(soup.find_all("img"))
        alt_missing = len(imgs_missing_alt)
        alt_percent = 0 if total_imgs == 0 else int(round(100.0 * (total_imgs - alt_missing) / total_imgs))
        checks["alt_coverage"] = {
            "ok": total_imgs == 0 or alt_missing == 0,
            "percent": alt_percent,
            "total_imgs": total_imgs,
        }

        # robots meta index/follow
        rmeta = (robots_meta or "").lower()
        robots_index = "noindex" not in rmeta
        robots_follow = "nofollow" not in rmeta
        checks["robots_meta_index"] = {"value": "index" if robots_index else "noindex", "ok": robots_index}
        checks["robots_meta_follow"] = {"value": "follow" if robots_follow else "nofollow", "ok": robots_follow}

        # basic indexable (very coarse)
        indexable = (result["status_code"] == 200) and robots_index
        checks["indexable"] = {"value": "Yes" if indexable else "No", "ok": indexable}

        # charset/lang (basic)
        meta_charset = soup.find("meta", attrs={"charset": True})
        http_equiv = soup.find("meta", attrs={"http-equiv": re.compile("content-type", re.I)})
        charset_ok = bool(meta_charset or http_equiv)
        html_lang = (soup.find("html") or {}).attrs.get("lang", "")
        checks["charset"] = {"ok": charset_ok}
        checks["lang"] = {"ok": bool(html_lang)}

        # compression (we can't see server compression here; show placeholder)
        checks["compression"] = {"ok": True, "value": "—"}

        # X-Robots-Tag (from headers if present)
        xrt = ""
        try:
            if resp is not None:
                xrt = resp.headers.get("x-robots-tag", "")  # type: ignore
        except Exception:
            pass
        checks["x_robots_tag"] = {"ok": True if xrt == "" else ("noindex" not in xrt.lower())}

        result["checks"] = checks

        # performance snapshot
        is_https = urlsplit(result["url"]).scheme == "https"
        perf = {
            "load_time_ms": result["load_time_ms"],
            "page_size_bytes": result["content_length"],
            "http_version": getattr(resp, "http_version", "HTTP/1.1") if resp else "HTTP/1.1",
            "redirects": 0,  # httpx doesn't expose history; keep 0 or compute manually if you implement it
            "final_url": str(resp.url) if resp else result["url"],  # type: ignore
            "https": {
                "is_https": bool(is_https),
                "ssl_checked": False,
                "ssl_ok": None,
            },
            # optional Lighthouse PageSpeed scores if you add an API call later:
            "mobile_score": None,
            "desktop_score": None,
        }
        result["performance"] = perf

    except Exception as e:
        # In case static phase itself blew up, surface as minimal result
        result.setdefault("errors", []).append(f"Static fetch failed: {e}")

    # ---------------------------------------
    # Rendered vs Static: optional JS pass
    # ---------------------------------------
    rendered_diff: Dict[str, Any] = result.get("rendered_diff") or {"engine": None, "matrix": []}
    if do_rendered_check:
        r = await render_page(result["url"])
        rendered_diff["engine"] = r.get("engine")
        if r.get("ok") and r.get("html"):
            try:
                rsoup = BeautifulSoup(r["html"], "html.parser")

                # Collect rendered equivalents
                r_title = extract_title(rsoup)
                r_desc = extract_meta_description(rsoup)
                r_canonical = extract_canonical(rsoup, result["url"])
                r_robots = extract_robots_meta(rsoup)
                r_viewport = extract_viewport_meta(rsoup)
                r_headings = extract_headings(rsoup)
                r_h1 = r_headings.get("h1", [])
                r_internal, r_external, _ = extract_links(rsoup, result["url"])
                r_og = extract_open_graph(rsoup)
                r_tw = extract_twitter_card(rsoup)
                r_jsonld = extract_json_ld(rsoup)
                r_micro = extract_microdata_count(rsoup)
                r_rdfa = extract_rdfa_count(rsoup)

                def row(label: str, before: Any, after: Any) -> Dict[str, Any]:
                    return {"label": label, "before": before if before not in ("", None) else "—",
                            "after": after if after not in ("", None) else "—",
                            "changed": (before or "") != (after or "")}

                matrix: List[Dict[str, Any]] = []
                matrix.append(row("Title", result.get("title"), r_title))
                matrix.append(row("Meta Description", result.get("description"), r_desc))
                matrix.append(row("Canonical", result.get("canonical"), r_canonical))
                matrix.append(row("Robots Meta", result.get("robots_meta"), r_robots))
                matrix.append(row("H1 Count", len(result.get("h1") or []), len(r_h1)))
                first_h1_before = (result.get("h1") or [None])[0]
                first_h1_after = (r_h1 or [None])[0]
                matrix.append(row("First H1", first_h1_before, first_h1_after))
                matrix.append(row("Open Graph present", "Yes" if result.get("has_open_graph") else "No", "Yes" if r_og else "No"))
                matrix.append(row("Twitter Card present", "Yes" if result.get("has_twitter_card") else "No", "Yes" if r_tw else "No"))
                matrix.append(row("JSON-LD blocks", len(result.get("json_ld") or []), len(r_jsonld)))
                matrix.append(row("Microdata items", (result["microdata"][0]["count"] if result.get("microdata") else 0), r_micro))
                matrix.append(row("RDFa items", (result["rdfa"][0]["count"] if result.get("rdfa") else 0), r_rdfa))
                matrix.append(row("Internal links (count)", len(result.get("internal_links") or []), len(r_internal)))
                matrix.append(row("External links (count)", len(result.get("external_links") or []), len(r_external)))
                matrix.append(row("Viewport meta present", "Yes" if result["checks"].get("viewport_meta", {}).get("present") else "No", "Yes" if r_viewport else "No"))

                rendered_diff["matrix"] = matrix
            except Exception as diff_e:
                rendered_diff.setdefault("matrix", [])
                rendered_diff["error"] = f"Render diff failed: {diff_e}"
        else:
            rendered_diff.setdefault("matrix", [])
            rendered_diff["error"] = r.get("error") or f"Status {r.get('status')}"

    else:
        # Explicitly note skip so the UI can show a helpful link/message if desired
        rendered_diff.setdefault("matrix", [])
        rendered_diff.setdefault("error", "Rendered DOM check skipped by quick mode.")

    result["rendered_diff"] = rendered_diff

    # Summarize sd types
    try:
        types: List[str] = []
        for block in result.get("json_ld") or []:
            t = block.get("@type")
            if isinstance(t, list):
                for x in t:
                    if isinstance(x, str):
                        types.append(x)
            elif isinstance(t, str):
                types.append(t)
        result["sd_types"] = {"types": sorted(set(types))}
    except Exception:
        result["sd_types"] = {"types": []}

    return result
