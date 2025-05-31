"""
scrape_incremental.py

Incremental crawler for *madewithnestle.ca*.

Purpose:
    • Re-visit URLs found in scraped_data_async/visited_urls.txt
    • For each URL compare ETag / Last-Modified headers
      to decide whether the page has changed.
    • Only pages that differ are re-scraped (HTML / text / tables / images)
      and **overwrite** previous files.
    • New URLs discovered during recrawl are appended to visited_urls.txt
      and scraped immediately (naïve breadth-first).

This script lets you keep the knowledge base fresh without
touching hundreds of unchanged pages.
"""
import asyncio
import os
import json
import hashlib
import requests
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ── Configuration ─────────────────────────────────────────────────────────
BASE_URL       = "https://www.madewithnestle.ca"
OUTPUT_DIR     = "scraped_data_async"
VISITED_FILE   = os.path.join(OUTPUT_DIR, "visited_urls.txt")
CACHE_FILE     = "page_cache.json"            # stores {url: {"etag":..,"last":..}}
USER_AGENT     = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/114.0.0.0 Safari/537.36"
)
VALID_IMG_EXT   = (".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp")
MAX_CONCURRENCY = 5

# ── Load previous run info ────────────────────────────────────────────────
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        page_cache: dict[str, dict] = json.load(f)
else:
    page_cache = {}

with open(VISITED_FILE, "r", encoding="utf-8") as f:
    initial_urls = {line.strip() for line in f if line.strip()}

# ── Helpers ───────────────────────────────────────────────────────────────
def normalize(url: str) -> str:
    pr = urlparse(url)
    return urlunparse((pr.scheme, pr.netloc, pr.path, "", "", ""))


def needs_refresh(url: str, session: requests.Session) -> bool:
    """Return True if HEAD result shows new ETag/Last-Modified."""
    headers = {}
    cache = page_cache.get(url, {})
    if "etag" in cache:
        headers["If-None-Match"] = cache["etag"]
    if "last" in cache:
        headers["If-Modified-Since"] = cache["last"]
    try:
        r = session.head(url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code == 304:
            return False
        # store new headers
        page_cache[url] = {
            "etag": r.headers.get("ETag"),
            "last": r.headers.get("Last-Modified"),
        }
        return True
    except Exception:
        # on error → play safe and refresh
        return True


async def save_image(src: str, session: requests.Session):
    ext = os.path.splitext(urlparse(src).path)[1].lower()
    if ext not in VALID_IMG_EXT:
        return
    try:
        resp = session.get(src, timeout=15)
        resp.raise_for_status()
        h = hashlib.md5(src.encode()).hexdigest()
        path = os.path.join(OUTPUT_DIR, "images", f"{h}{ext}")
        with open(path, "wb") as f:
            f.write(resp.content)
    except Exception:
        pass


async def scrape_one(
    url: str,
    page,
    to_crawl: set[str],
    session: requests.Session,
):
    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    stub = urlparse(url).path.strip("/") or "index"
    safe = stub.replace("/", "_")

    # overwrite previous files
    with open(f"{OUTPUT_DIR}/{safe}.html", "w", encoding="utf-8") as f:
        f.write(html)

    texts = [
        t.get_text(strip=True)
        for t in soup.find_all(["p", "h1", "h2", "h3", "li", "span"])
        if t.get_text(strip=True)
    ]
    with open(f"{OUTPUT_DIR}/{safe}_text.json", "w", encoding="utf-8") as f:
        json.dump(texts, f, ensure_ascii=False, indent=2)

    tables = []
    for tbl in soup.find_all("table"):
        hdrs = [th.get_text(strip=True) for th in tbl.find_all("th")]
        rows = [
            [td.get_text(strip=True) for td in tr.find_all("td")]
            for tr in tbl.find_all("tr")
            if tr.find_all("td")
        ]
        if rows:
            tables.append({"headers": hdrs, "rows": rows})
    with open(f"{OUTPUT_DIR}/{safe}_tables.json", "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, indent=2)

    imgs = [urljoin(BASE_URL, img["src"]) for img in soup.find_all("img", src=True)]
    await asyncio.gather(*[save_image(src, session) for src in imgs])

    # discover new links
    hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    for link in hrefs:
        clean = normalize(link)
        if urlparse(clean).netloc == urlparse(BASE_URL).netloc:
            to_crawl.add(clean)


async def worker(
    ctx,
    to_crawl: set[str],
    visited: set[str],
    session: requests.Session,
    sem: asyncio.Semaphore,
):
    page = await ctx.new_page()
    while True:
        try:
            url = to_crawl.pop()
        except KeyError:
            break
        if url in visited:
            continue
        visited.add(url)

        if not needs_refresh(url, session):
            continue  # unchanged

        print(f"[↻] {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=120_000)
            await scrape_one(url, page, to_crawl, session)
        except Exception as e:
            print(f"[!] {url}: {e}")
    await page.close()


async def incremental_crawl():
    visited: set[str] = set()
    to_crawl: set[str] = set(initial_urls)
    session = requests.Session()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        tasks = [
            asyncio.create_task(worker(ctx, to_crawl, visited, session, sem))
            for _ in range(MAX_CONCURRENCY)
        ]
        await asyncio.gather(*tasks)
        await ctx.close()
        await browser.close()

    # persist updated cache + any newly found URLs
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(page_cache, f, ensure_ascii=False, indent=2)
    with open(VISITED_FILE, "a", encoding="utf-8") as f:
        for url in visited - initial_urls:
            f.write(url + "\n")
    print("✓ Incremental crawl done.")


if __name__ == "__main__":
    asyncio.run(incremental_crawl())
