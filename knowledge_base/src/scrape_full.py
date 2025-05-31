"""
scrape_full.py

Full-site crawler for *madewithnestle.ca*.

• Recursively visits every internal page starting from BASE_URL.
• Saves:
    ─ raw HTML          →  scraped_data_async/<path>.html
    ─ cleaned text      →  .../<path>_text.json
    ─ extracted tables  →  .../<path>_tables.json
    ─ images (binary)   →  .../images/<hash>.<ext>
• Records every visited URL to scraped_data_async/visited_urls.txt
  (one per line, path-only duplicates removed).

The crawler runs concurrently (MAX_CONCURRENCY pages in flight)
to speed up the initial ~600 URL scrape.

Intended for one-off “full” runs.  
See `scrape_incremental.py` for later updates.
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
USER_AGENT     = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/114.0.0.0 Safari/537.36"
)
VALID_IMG_EXT  = (".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp")
MAX_CONCURRENCY = 6          # simultaneous Playwright pages

# ── Bootstrap folders ────────────────────────────────────────────────────
os.makedirs(os.path.join(OUTPUT_DIR, "images"), exist_ok=True)
# start fresh: overwrite visited file
open(VISITED_FILE, "w", encoding="utf-8").close()


def normalize(url: str) -> str:
    """Strip query / fragment → scheme+netloc+path."""
    pr = urlparse(url)
    return urlunparse((pr.scheme, pr.netloc, pr.path, "", "", ""))


def is_internal(link: str) -> bool:
    return urlparse(link).netloc == urlparse(BASE_URL).netloc


async def save_image(src: str, session: requests.Session):
    """Download image if passes extension filter; name by MD5(src)."""
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


async def process_page(
    url: str,
    page,
    to_crawl: set[str],
    visited: set[str],
    visited_lock: asyncio.Lock,
    session: requests.Session,
):
    """Scrape one URL; enqueue new links."""
    # Skip external or already-seen URLs
    if not is_internal(url) or url in visited:
        return

    # Heuristic skip for recipe param filters
    if "recipe_tags_filter" in url or "/search?" in url:
        return

    print(f"[→] {url}")
    try:
        await page.goto(url, wait_until="networkidle", timeout=120_000)
    except Exception as e:
        print(f"[!] Fail {url}: {e}")
        return

    # Accept cookies (cover multiple selectors)
    for sel in (
        "button#consent-accept",
        "button.cookie-btn",
        "button:has-text('Accept All')",
    ):
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                break
        except Exception:
            pass

    # Expand dynamic accordions / load-more
    expand_selectors = [
        "button.dropdown-toggle",
        "button[data-action='expand']",
        "button.accordion-toggle",
    ]
    for sel in expand_selectors:
        for toggle in await page.query_selector_all(sel):
            try:
                await toggle.click()
            except Exception:
                pass
    # recipe list “Load more”
    if "/recipes" in url:
        while True:
            lm = await page.query_selector(
                "button.load-more, button[data-action='load-more']"
            )
            if not lm:
                break
            print("    ↳ Load more …")
            await lm.click()
            await page.wait_for_timeout(2_000)

    # Extract and save
    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")

    path_stub = urlparse(url).path.strip("/") or "index"
    safe_name = path_stub.replace("/", "_")

    with open(f"{OUTPUT_DIR}/{safe_name}.html", "w", encoding="utf-8") as f:
        f.write(html)

    texts = [
        t.get_text(strip=True)
        for t in soup.find_all(["p", "h1", "h2", "h3", "li", "span"])
        if t.get_text(strip=True)
    ]
    with open(f"{OUTPUT_DIR}/{safe_name}_text.json", "w", encoding="utf-8") as f:
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
    with open(f"{OUTPUT_DIR}/{safe_name}_tables.json", "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, indent=2)

    # Images
    imgs = [urljoin(BASE_URL, img["src"]) for img in soup.find_all("img", src=True)]
    tasks = [save_image(src, session) for src in imgs]
    await asyncio.gather(*tasks)

    # Enqueue links
    hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    for link in hrefs:
        clean = normalize(link)
        if is_internal(clean) and clean not in visited:
            to_crawl.add(clean)

    # Mark visited (thread-safe append)
    async with visited_lock:
        visited.add(url)
        with open(VISITED_FILE, "a", encoding="utf-8") as fv:
            fv.write(url + "\n")
    print(f"[✓] {url}")


async def crawl():
    visited: set[str] = set()
    to_crawl: set[str] = {BASE_URL}
    visited_lock = asyncio.Lock()
    session = requests.Session()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def worker():
            async with browser.new_context(user_agent=USER_AGENT) as ctx:
                page = await ctx.new_page()
                while True:
                    try:
                        url = to_crawl.pop()
                    except KeyError:
                        break
                    async with sem:
                        await process_page(
                            url, page, to_crawl, visited, visited_lock, session
                        )

        workers = [asyncio.create_task(worker()) for _ in range(MAX_CONCURRENCY)]
        await asyncio.gather(*workers)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(crawl())
