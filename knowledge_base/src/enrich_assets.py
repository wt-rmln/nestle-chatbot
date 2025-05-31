"""
enrich_assets.py

Extract title / remove navigation bar / flatten tables / count images
"""

import re, json, html
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse   import urljoin

DATA_DIR = Path("scraped_data_async")
TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# ────────────── 1) Text Cleaning ────────────────────────────────────
STOP_LINES = {
    "Share This",
    "FacebookTwitterPinterestEmailYum",
    "BrandChocolate & TreatsAeroCOFFEE CRISPDrumstick bitesKit KatSmartiesTurtlesAfter EightBig TurkCrunchEaster chocolates and treatsMackintosh ToffeeMirageQuality StreetRoloCoffeeCoffee MateNESCAFÉIce Cream & Frozen TreatsConfectionery Frozen DessertsHäagen-DazsDrumstickDel MonteiÖGOParlourReal DairyInfant NutritionNidoNestlé MaternaMeal Times Made EasyMAGGINutrition +BOOST KidsBoostNature's BountyPet FoodsPurinaSpring & Sparkling WaterEssentiaMaison PerrierPerrierSan PellegrinoQuick-Mix DrinksGoodHostMiloNESTEANesfrutaCarnation Hot ChocolateNesquik",
    "All recipesA World of FlavoursA World of FlavoursAll recipesOriginal SMARTIES CookiesTurtles mason jar s’more parfaitGreen Monster SmoothieAERO Truffle TartsSee AllQuick and easyBOOST Fluffy Protein Pancake + Waffle MixPerfect AffogatoIced Java Coconut SwirlSee AllImpress your guestsRaspberry Brown Butter Crêpes à la ModeSnickerdoodle Cups with Vanilla Ice CreamSea Salt Chocolate Brownie Ice Cream SandwichesCardamom Donuts With Vanilla Bean Ice Cream",
    "Sustainability",
    "BrandChocolate & TreatsAeroCOFFEE CRISPKit KatSmartiesTurtlesAfter EightBig TurkCrunchDrumstick bitesEaster chocolates and treatsMackintosh ToffeeMirageQuality StreetRoloCoffeeCoffee MateNESCAFÉIce Cream & Frozen TreatsConfectionery Frozen DessertsHäagen-DazsDrumstickIÖGODel MonteParlourReal DairyInfant NutritionNidoNestlé MaternaMeal Times Made EasyMAGGINutrition +BOOST KidsBoostNature's BountyPet FoodsPurinaSpring & Sparkling WaterMaison PerrierPerrierSan PellegrinoQuick-Mix DrinksGoodHostMiloNESTEANesfrutaCarnation Hot ChocolateNesquik",
    "Spring & Sparkling WaterPerrierSan PellegrinoMaison Perrier",
    "Nestle around the globe",
    "Nestle fun facts",
    "Global recipes",
    "Holidays",
    "FAQ",
    "Tubs",
    "Classics",
    "Minis",
    "Featured Cones",
    "Plant-Based",
    "Our Story",
    "Drumstick bites",
    "Kit Kat",
    "Smarties",
    "Turtles",
    "After Eight",
    "Big Turk",
    "Crunch",
    "Easter chocolates and treats",
    "Mackintosh Toffee",
    "Mirage",
    "Quality Street",
    "Rolo",
    "CoffeeCoffee MateNESCAFÉ",
    "Ice Cream & Frozen TreatsConfectionery Frozen DessertsHäagen-DazsDrumstickDel MonteiÖGOParlourReal Dairy",
    "Infant NutritionNidoNestlé Materna",
    "Meal Times Made EasyMAGGI",
    "Nutrition +BOOST KidsBoostNature's Bounty",
    "Pet FoodsPurina",
    "Spring & Sparkling WaterEssentiaMaison PerrierPerrierSan Pellegrino",
    "Quick-Mix DrinksGoodHostMiloNESTEANesfrutaCarnation Hot ChocolateNesquik",
    "Product",
    "Classic Drumstick",
    "About NestleNestlé NewsNestlé® SMARTIES® featured on Food FactoryOur Peanut Free PromiseNestlé Canada’s Nesquik proudly sponsors the First-Ever National Make Happy Tummies CampaignSmart Strategies to Lose Weight, One Meal at a TimeSee AllWho We AreAbout UsCorporate InformationContact UsCareers",
    "About NestléNestle NewsAbout UsCorporate InformationCareers",
    "Chocolate & TreatsAfter EightBig TurkAeroCOFFEE CRISPMackintosh ToffeeEaster chocolates and treatsCrunchDrumstick bitesMirageQuality StreetRolo",
    "Ice Cream & Frozen TreatsConfectionery Frozen DessertsDel MonteDrumstickHäagen-DazsiÖGOParlourReal Dairy",
    "Infant NutritionCerelacGerberNestlé MaternaNido",
    "Nutrition +BOOST kidsBoostNature's Bounty",
    "Spring & Sparkling WaterEssentiaPerrierSan PellegrinoMaison Perrier",
    "Quick-Mix DrinksGoodHostNESTEAMiloNesfruta",
    "CoffeeNESCAFÉCoffee Mate LiquidCoffee Mate",
    "Support",
    "Sign up",
    "Search",
    "Products",
    "facts",
    "Home",
    "Facebook",
    "Instagram",
    "YouTube",
    "TikTok",
    "Contact Us",
    "FAQs",
    "Blogs",
    "Sign Up",
}

def clean_lines(lines: list[str], page_title: str) -> list[str]:
    """
    · If the title is found (ignoring case/punctuation), remove all lines before it.
    · Filter out adjacent duplicates, very short lines, and blacklisted lines.
    """
    def norm(s): return re.sub(r"[^A-Za-z0-9]+", "", s).lower()

    title_norm = norm(page_title)
    cut_idx = 0
    for i, ln in enumerate(lines):
        if norm(ln).startswith(title_norm) and len(ln) > 2:
            cut_idx = i
            break
    lines = lines[cut_idx:]

    cleaned, prev = [], ""
    for ln in lines:
        ln = ln.strip()
        if ln in STOP_LINES:
            continue
        if len(ln) <= 2:
            continue
        if ln == prev:
            continue
        if any(ln in old for old in cleaned):
            continue

        cleaned.append(ln)
        prev = ln
    return cleaned

# ────────────── 2) Title Extraction ───────────────────────────────────
def extract_title(html_path: Path, fallback_lines: list[str]) -> str:
    raw = html_path.read_text(errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")

    if soup.title and soup.title.string:
        return soup.title.string.split("|")[0].strip()

    og = soup.find("meta", {"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()

    h1 = (soup.find("main") or soup).find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)

    m = TITLE_TAG_RE.search(raw)
    if m:
        return html.unescape(m.group(1)).split("|")[0].strip()

    for ln in fallback_lines:
        if 3 < len(ln) < 120:
            return ln
    return ""

# ────────────── 3) Table Flattening & Image Counting ─────────────────
def flatten_tables(stub: str) -> tuple[list[str], int]:
    f = DATA_DIR / f"{stub}_tables.json"
    if not f.is_file():
        return [], 0
    tables = json.loads(f.read_text())
    out = []
    for tbl in tables:
        hdrs = tbl.get("headers") or []
        for row in tbl.get("rows", []):
            cells = row
            if hdrs and len(cells) == len(hdrs):
                out.append("[table] " + " | ".join(f"{h}:{c}" for h, c in zip(hdrs, cells)))
            else:
                out.append("[table] " + " | ".join(cells))
    return out, len(tables)

def count_images(html_path: Path) -> int:
    soup = BeautifulSoup(html_path.read_text(errors="ignore"), "html.parser")

    main = soup.find("main") or soup
    imgs = main.find_all("img")

    meaningful = set()
    for img in imgs:
        src = (img.get("src") or "").strip()

        if not src or src.startswith("data:"):
            continue

        if any(word in src.lower() for word in ["icon", "sprite", "logo", "placeholder"]):
            continue

        w = int(img.get("width") or 0)
        h = int(img.get("height") or 0)
        if 0 < w <= 20 and 0 < h <= 20:
            continue

        meaningful.add(src)
    return len(meaningful)

# ────────────── 4) Image Collection Helpers ───────────────────────────

ROLE_RULES = [
    ("package",   [r"carton", r"pack(ag)?e", r"hero", r"\bmain\b", r"\b(stub)\b"]),
    ("nutrition", [r"nutri",  r"panel", r"fact"]),
    ("recipe",    [r"recipe", r"step",  r"instruct", r"method"]),
]

def detect_role(fname: str, alt: str, stub: str) -> str:
    fname = fname.lower()
    alt   = alt.lower()
    if stub in fname or any(re.search(p, fname) for p in ROLE_RULES[0][1]):
        return "package"
    if any(re.search(p, fname) for p in ROLE_RULES[1][1]) or "nutrition" in alt:
        return "nutrition"
    if any(re.search(p, fname) for p in ROLE_RULES[2][1]) or "recipe" in alt:
        return "recipe"
    return "secondary" 

def to_int(val) -> int:
    if val is None:
        return 0
    m = re.match(r"\d+(\.\d+)?", str(val))
    if not m:
        return 0
    return int(float(m.group(0)))

def collect_images(html_path: Path, page_url: str | None, stub: str) -> list[dict]:
    soup  = BeautifulSoup(html_path.read_text(errors="ignore"), "html.parser")
    main  = soup.find("main") or soup
    imgs  = main.find_all("img")

    primary, others = [], []
    seen = set()
    for img in imgs:
        src = (img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue

        abs_url = urljoin(page_url or "", src)
        base    = abs_url.split("?")[0].lower()
        if base in seen:
            continue
        seen.add(base)

        if any(k in base for k in ["sprite", "logo", "placeholder", "cookie", "icon", "pixel"]):
            continue
        w = to_int(img.get("width"))
        h = to_int(img.get("height"))
        if 0 < w <= 50 and 0 < h <= 50:
            continue

        alt = img.get("alt") or ""
        role = detect_role(base, alt, stub)

        item = {"url": abs_url, "role": role, "alt": alt}
        if role == "package":
            primary.append(item)
        else:
            others.append(item)

    result = primary + others
    return result[:5]


# ────────────── Process Single File ───────────────────────────────────
def process_one(jpath: Path):
    stub = jpath.stem[:-5]
    html_path = DATA_DIR / f"{stub}.html"
    if not html_path.is_file():
        print(f"[skip] {stub}")
        return

    raw = json.loads(jpath.read_text())
    if isinstance(raw, dict):
        meta, lines_raw = raw.get("metadata", {}), raw["text"]
    else:
        meta, lines_raw = {}, raw

    title = extract_title(html_path, lines_raw)
    lines_clean = clean_lines(lines_raw, title)

    tbl_lines, n_tbl = flatten_tables(stub)
    lines_clean.extend(tbl_lines)

    try:
        foot_idx = next(i for i, ln in enumerate(lines_clean)
                        if ln.strip().lower() == "site map")
        lines_clean = lines_clean[:foot_idx]
    except StopIteration:
        pass

    images   = collect_images(html_path, meta.get("url"), stub)
    n_img    = len(images)

    n_tokens = sum(len(l.split()) for l in lines_clean)

    meta.update({
        "title":     title,
        "n_tokens":  n_tokens,
        "n_tables":  n_tbl,
        "n_images":  n_img,
        "images":    images,
    })

    jpath.write_text(json.dumps({"metadata": meta, "text": lines_clean},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[✓] {stub:<45} tok={meta['n_tokens']:<5} tbl={n_tbl} img={n_img}")

# ────────────── 5) main ───────────────────────────────────────
def main():
    for j in sorted(DATA_DIR.glob("*_text.json")):
        process_one(j)

if __name__ == "__main__":
    main()
