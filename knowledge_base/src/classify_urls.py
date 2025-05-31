"""
classify_urls.py

Categorize Nestlé website pages into predefined categories (Recipe, Product, Video, etc.).
"""

import os, re, json, time
from pathlib import Path
from collections import Counter
from typing import Dict, List, Tuple

import openai
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# ─────────────────────────── config ────────────────────────────
DATA_DIR    = Path("scraped_data_async")
TXT_PATH    = DATA_DIR / "visited_urls.txt"
OUT_PATH    = "classified_urls.jsonl"
LLM_MODEL   = "gpt-4o"
BATCH_SIZE  = 15
SNIPPET_LEN = 400
CATEGORIES  = {
    "Recipe","Product","Video","Blog","Article","Support",
    "Sustainability","Promo","About","Search","Document","Other"
}

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# ─────────────────────── regex quick rules ─────────────────────
RULES = [
    (r"^https?://[^/]+/(?:coffee|nesfruta|real-dairy|del-monte|confectionery-frozen-desserts)(?:/|$)", "Product"),
    (r"^https?://[^/]+/recipes?$", "Recipe"),
    (r"/(?:complementary-brands|prepared-canada)(?:/|$)", "Product"),
    (r"/(?:holiday-favourites|easter-chocolates-and-treats|diwali|french-christmas)(?:/|$)", "Promo"),
    (r"/recipes?/|/recipe/",               "Recipe"),
    (r"/video/",                           "Video"),
    (r"/blog/|/articles?(/|$)",            "Blog"),
    (r"/news/|/stories?/",                 "Article"),
    (r"/help/|/support|/faq|/contact",     "Support"),
    (r"/(about|corporate|careers|who-we-are)", "About"),
    (r"/sustainability|/responsible|/sustainable",    "Sustainability"),
    (r"/promotions?|/contest|/world-",     "Promo"),
    (r"/search(\?|/)|/sitemap|/signup",    "Search"),
    (r"\.pdf$",                            "Document"),
    (
        r"/(?:brand|confectionery|boost|nescaf|nescafe|kit-kat|aero|turtles|"
        r"drumstick|parlour|quality-street|after-eight|coffee-crisp|"
        r"nesquik|smarties|coffee-mate|haagen-dazs|natures-bounty|"
        r"milo|rolo|crunch)(?:/|$)",
        "Product"
    ),
]
COMPILED = [(re.compile(p, re.I), lab) for p, lab in RULES]

def classify_by_regex(url: str) -> str:
    for pat, lab in COMPILED:
        if pat.search(url):
            return lab
    return ""

# ─────────────────── helpers: mapping & snippet ─────────────────
def build_url2file() -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    for hpath in DATA_DIR.glob("*.html"):
        stub = hpath.stem
        tpath = DATA_DIR / f"{stub}_text.json"
        if not tpath.is_file():
            continue
        soup = BeautifulSoup(hpath.read_text(errors="ignore"), "html.parser")
        url = None
        can = soup.find("link", rel="canonical")
        if can and can.has_attr("href"):
            url = can["href"].strip()
        else:
            og = soup.find("meta", property="og:url")
            if og and og.has_attr("content"):
                url = og["content"].strip()
        if not url:
            path = stub.replace("_", "/")
            url = "https://www.madewithnestle.ca/" + ("" if path=="index" else path)
        mapping[url] = tpath
    return mapping

def load_page_snippet(jpath: Path, limit: int = SNIPPET_LEN) -> str:
    if not jpath.is_file():
        return ""
    raw = json.loads(jpath.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "text" in raw:
        lines = raw["text"]
    else:
        lines = raw if isinstance(raw, list) else []
    if not lines:
        return ""
    title = lines[0]
    body  = " ".join(lines[1:])[:limit]
    return f"Title: {title}\nText: {body}"

# ─────────────────────── LLM helper ────────────────────────────
SYSTEM_MSG = (
    "You are classifying Nestlé website pages. "
    "Given the page snippet, answer with exactly ONE of: "
    "Recipe, Product, Video, Blog, Article, Support, Sustainability, "
    "Promo, About, Search, Document, Other."
)
def call_llm(batch: List[Tuple[str,str]]) -> Dict[str,str]:
    msgs = [{"role":"system", "content": SYSTEM_MSG}]
    for u,s in batch:
        msgs.append({"role":"user", "content": f"{u}\n---\n{s}"})
    resp = openai.chat.completions.create(
        model       = LLM_MODEL,
        messages    = msgs,
        temperature = 0,
        max_tokens  = 5 * len(batch)
    )
    lines = resp.choices[0].message.content.strip().splitlines()
    out: Dict[str,str] = {}
    for (u,_), line in zip(batch, lines):
        cat = line.strip().title()
        out[u] = cat if cat in CATEGORIES else "Other"
    return out

# ─────────────────────────── main ──────────────────────────────
def main():
    urls     = [ln.strip() for ln in open(TXT_PATH, encoding="utf-8") if ln.strip()]
    url2file = build_url2file()

    missing = [u for u in urls if u not in url2file]
    print(f"{len(missing)} URLs have no _text.json (will send to LLM with empty snippet)")

    cats, need_llm = {}, []
    for u in urls:
        c = classify_by_regex(u)
        if c:
            cats[u] = c
        else:
            need_llm.append(u)
    print(f"Regex classified {len(cats)} URLs, {len(need_llm)} need LLM…")

    batch: List[Tuple[str,str]] = []
    for u in need_llm:
        snippet = load_page_snippet(url2file.get(u, Path()))
        batch.append((u, snippet))
        if len(batch) >= BATCH_SIZE:
            cats.update(call_llm(batch))
            batch.clear()
    if batch:
        cats.update(call_llm(batch))

    # save classified_urls.jsonl
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for u in urls:
            f.write(json.dumps({"url":u, "category":cats.get(u,"Other")}, ensure_ascii=False) + "\n")

    # inject metadata back into *_text.json
    for url, jpath in url2file.items():
        cat = cats.get(url, "Other")
        raw = json.loads(jpath.read_text(encoding="utf-8"))
        lines = raw["text"] if isinstance(raw, dict) and "text" in raw else raw
        annotated = {"metadata": {"url": url, "category": cat}, "text": lines}
        jpath.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")

    cnt = Counter(cats.values())
    print("\n✅ Classification finished →", OUT_PATH)
    for c,n in cnt.most_common():
        print(f"  {c:<15}: {n}")
    print("✅ Metadata injected into original *_text.json files")

if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"\nElapsed: {time.time()-t0:.1f}s")

