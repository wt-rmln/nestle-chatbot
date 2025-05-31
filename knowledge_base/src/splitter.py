"""
splitter.py

Batch-slice all *_text.json files into embedding-friendly chunks. Preserves URL, title, and category; fills in missing fields; uses sentence-based and simple splitting with overlap; processes in parallel.
"""

import json, glob, os, gzip, pathlib, concurrent.futures, logging, sys
from typing import List, Dict
from dataclasses import dataclass
from text_splitter import SentenceTextSplitter, SimpleTextSplitter
from page import Page, SplitPage 
import base64


# ───────────────────────────── logging ──────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("splitter")

# ──────────────────────────── config ────────────────────────────────
HERE           = pathlib.Path(__file__).parent
DATA_DIR       = HERE / "scraped_data_async"
OUT_PATH       = HERE / "slices.jsonl.gz"
MAX_TOKENS     = 450
MAX_OBJECT_LEN = 1200
MAX_IMAGES = 3 
NUM_WORKERS    = os.cpu_count() or 4
USE_GZIP       = OUT_PATH.suffix == ".gz"

# ─────────────────── import splitters ───────────────────────────────
sentence_splitter = SentenceTextSplitter(max_tokens_per_section=MAX_TOKENS)
simple_splitter   = SimpleTextSplitter(max_object_length=MAX_OBJECT_LEN)

# ─────────────────── Load & Split Pages ────────────────────────────────────
def load_pages(fp: pathlib.Path) -> List[Page]:
    """Load one *_text.json and convert to Page list."""
    try:
        doc = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Failed to read {fp}: {e}")
        return []

    meta = doc.get("metadata", {})
    meta.setdefault("url",      str(fp))
    meta.setdefault("title",    meta["url"].rsplit("/", 1)[-1])
    meta.setdefault("category", meta.get("category", "Uncategorized"))

    blocks = doc.get("text", [])
    if isinstance(blocks, str):
        blocks = [blocks]

    pages: List[Page] = []
    offset = 0
    for i, txt in enumerate(blocks):
        pages.append(Page(page_num=i, offset=offset, text=txt, meta=meta))
        offset += len(txt)
    return pages

def choose_splitter(total_chars: int):
    """
    Simple heuristic: use SentenceTextSplitter for long text,
    otherwise use SimpleTextSplitter.
    """
    return sentence_splitter if total_chars > MAX_OBJECT_LEN else simple_splitter

def make_id(stem: str, idx: int) -> str:
    b64 = base64.urlsafe_b64encode(stem.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{b64}__{idx:03}"

def slice_one(fp: pathlib.Path) -> List[Dict]:
    pages = load_pages(fp)
    if not pages:
        return []

    total_len = sum(len(p.text) for p in pages)
    splitter  = choose_splitter(total_len)

    out: List[Dict] = []
    for idx, sp in enumerate(splitter.split_pages(pages)):
        meta = pages[sp.page_num].meta
        meta_imgs = (meta.get("images") or [])[:MAX_IMAGES]

        out.append({
            "id":           make_id(fp.stem, idx),
            "url":          meta["url"],
            "title":        meta["title"],
            "category":     meta["category"],
            "images":       [img["url"] for img in meta_imgs],
            "image_titles": [img.get("alt", "") for img in meta_imgs],
            "content":      sp.text.strip(),
        })
    return out

# ──────────────────────────── Main Workflow ─────────────────────────────────
def main():
    files = list(DATA_DIR.glob("**/*_text.json"))
    if not files:
        log.error(f"No *_text.json found under {DATA_DIR}")
        return
    log.info(f"Found {len(files)} files → slicing with {NUM_WORKERS} workers…")

    all_slices: List[Dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as ex:
        futures = {ex.submit(slice_one, fp): fp for fp in files}
        for f in concurrent.futures.as_completed(futures):
            fp = futures[f]
            try:
                slices = f.result()
                all_slices.extend(slices)
                log.debug(f"{fp.name}: {len(slices)} slices")
            except Exception as e:
                log.error(f"Error slicing {fp}: {e}")

    log.info(f"Total slices: {len(all_slices)} → writing to {OUT_PATH.name}")
    opener = gzip.open if USE_GZIP else open
    with opener(OUT_PATH, "wt", encoding="utf-8") as fh:
        for obj in all_slices:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    log.info("All done.")

if __name__ == "__main__":
    main()
