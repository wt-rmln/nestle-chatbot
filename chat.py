"""
chat.py

Description:
1. Local intent classification: quick replies for intents like greeting, thanks, goodbye.
2. Brand-first retrieval: if the user's sentence contains a brand keyword, prioritize searching slices whose URL contains that brand slug (with or without hyphens).
   - If the user asks about "website"/"site" (e.g., "website of the kitkat mini bar frozen dessert"), return:
     "You can find more information here: <page_url>"
   - Otherwise, construct an LLM prompt from the slice text, generate an answer with the LLM, and append:
     "You can find more information here: <page_url>"
3. Category-aware RAG: if no brand match and model confidence is very high (conf > 0.90) and the predicted tag is in CATEGORY_TAGS, perform category-based retrieval.
4. Fallback: if none of the above apply, attempt Azure Search, then use the LLM to generate an answer and append:
   "You can find more information here: <page_url>"
"""

import os
import json
import random
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple, Optional, Any, Dict
import csv

import torch
from dotenv import load_dotenv
import openai
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from neo4j import GraphDatabase

from model import NeuralNet
from nltk_utils import tokenize, bag_of_words

# ─────────────────────────────────────────────────────────────────────────────
# Environment Variables & Client Initialization
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AZ_ENDPOINT    = os.getenv("AZ_SEARCH_ENDPOINT")
AZ_KEY         = os.getenv("AZ_SEARCH_KEY")
AZ_INDEX       = os.getenv("AZ_SEARCH_INDEX")
NEO_URI        = os.getenv("NEO4J_URI")
NEO_USER       = os.getenv("NEO4J_USER")
NEO_PASS       = os.getenv("NEO4J_PASS")

openai.api_key = OPENAI_API_KEY
search_client  = (
    SearchClient(endpoint=AZ_ENDPOINT,
                 index_name=AZ_INDEX,
                 credential=AzureKeyCredential(AZ_KEY))
    if (AZ_ENDPOINT and AZ_KEY and AZ_INDEX) else None
)
neo_driver     = (
    GraphDatabase.driver(NEO_URI, auth=(NEO_USER, NEO_PASS))
    if (NEO_URI and NEO_USER and NEO_PASS) else None
)

# ─────────────────────────────────────────────────────────────────────────────
# Local Intent Classification Model Loading
# ─────────────────────────────────────────────────────────────────────────────
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
state  = torch.load("data.pth", map_location=DEVICE)

all_words   = state["all_words"]
intent_tags = state["tags"]
model = NeuralNet(state["input_size"], state["hidden_size"], state["output_size"]).to(DEVICE)
model.load_state_dict(state["model_state"])
model.eval()

with open("intents.json", "r", encoding="utf-8") as f:
    intents_cfg = json.load(f)["intents"]
INTENT_LOOKUP = {it["tag"]: it for it in intents_cfg}

# These tags use category-aware retrieval when confidence is high
CATEGORY_TAGS = {
    "product", "recipe", "promo", "blog", "support", "article", "sustainability",
    "video", "search", "about", "document"
}

DEBUG_PRINT = True

# ─────────────────────────────────────────────────────────────────────────────
# Brand List & Normalization Function
# ─────────────────────────────────────────────────────────────────────────────
BRANDS = [
    "Aero", "COFFEE CRISP", "Coffee Mate", "Nescaf", "Drumstick", "Kit Kat",
    "Smarties", "Turtles", "After Eight", "Big Turk", "Crunch",
    "Quality Street", "Rolo", "Mackintosh Toffee", "Mirage",
    "Confectionery Frozen Desserts", "Häagen-Dazs", "iÖGO", "Del Monte", "Parlour",
    "Real Dairy", "Nido", "Nestlé Materna", "MAGGI", "BOOST Kids", "Boost",
    "Essentia", "Maison Perrier", "Perrier", "San Pellegrino", "GoodHost",
    "Milo", "NESTEA", "Nesfruta", "Carnation Hot Chocolate", "Nesquik"
]

def normalize_text(s: str) -> str:
    """
    1) Normalize accents using NFD, then remove combining marks: "Häagen-Dazs" -> "Haagen-Dazs"
    2) Remove all non-alphanumeric, non-space, non-underscore characters: "Haagen-Dazs" -> "HaagenDazs"
    3) Replace spaces/underscores with hyphens and convert to lowercase: -> "haagen-dazs"
    """
    nfd = unicodedata.normalize("NFD", s)
    no_combining = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^A-Za-z0-9\s_]", "", no_combining)
    hyphened = re.sub(r"[\s_]+", "-", cleaned)
    return hyphened.lower()

# ─────────────────────────────────────────────────────────────────────────────
# Category-based Neo4j Search (Category-aware RAG)
# ─────────────────────────────────────────────────────────────────────────────
def neo4j_category_search(category: str, question: str, top_k: int = 5
                          ) -> List[Tuple[str, str, Optional[str]]]:
    """
    MATCH (c:Category {name:$category})<-[:IN_CATEGORY]-(p:Page)
    CALL db.index.fulltext.queryNodes('sliceFulltext', $kw)
       YIELD node AS s, score
    WHERE (p)-[:HAS_SLICE]->(s)
    OPTIONAL MATCH (p)-[:HAS_IMAGE]->(img:Image)
    RETURN s.content AS text, p.url AS page_url, img.url AS img_url
    ORDER BY score DESC
    LIMIT $k
    """
    if neo_driver is None:
        return []
    cypher = """
      MATCH (c:Category {name:$cat})<-[:IN_CATEGORY]-(p:Page)
      CALL db.index.fulltext.queryNodes('sliceFulltext', $kw)
        YIELD node AS s, score
      WHERE (p)-[:HAS_SLICE]->(s)
      OPTIONAL MATCH (p)-[:HAS_IMAGE]->(img:Image)
      RETURN s.content AS text, p.url AS page_url, img.url AS img_url
      ORDER BY score DESC
      LIMIT $k
    """
    with neo_driver.session() as sess:
        recs = sess.run(cypher,
                        cat=category.capitalize(),
                        kw=question,
                        k=top_k)
        return [(r["text"], r["page_url"], r["img_url"]) for r in recs]

# ─────────────────────────────────────────────────────────────────────────────
# Brand-based Neo4j Search: only consider pages whose URL contains brand slug or slug without hyphens
# ─────────────────────────────────────────────────────────────────────────────
def neo4j_brand_search(brand_slug: str, question: str, top_k: int = 5
                       ) -> List[Tuple[str, str, Optional[str]]]:
    """
    BRAND retrieval:
      CALL db.index.fulltext.queryNodes('sliceFulltext', $kw) YIELD node AS s, score
      MATCH (p:Page)-[:HAS_SLICE]->(s),
            (b:Brand {slug:$brandSlug})<-[:HAS_BRAND]-(p)
      WHERE toLower(p.url) CONTAINS toLower($brandSlug)
         OR toLower(p.url) CONTAINS toLower(replace($brandSlug, "-", ""))
      OPTIONAL MATCH (p)-[:HAS_IMAGE]->(img:Image)
      RETURN s.content AS text, p.url AS page_url, img.url AS img_url, score
      ORDER BY score DESC
      LIMIT $k
    """
    if neo_driver is None:
        return []

    cypher = """
      CALL db.index.fulltext.queryNodes('sliceFulltext', $kw) YIELD node AS s, score
      MATCH (p:Page)-[:HAS_SLICE]->(s),
            (b:Brand {slug:$brandSlug})<-[:HAS_BRAND]-(p)
      WHERE toLower(p.url) CONTAINS toLower($brandSlug)
         OR toLower(p.url) CONTAINS toLower(replace($brandSlug, "-", ""))
      OPTIONAL MATCH (p)-[:HAS_IMAGE]->(img:Image)
      RETURN s.content AS text, p.url AS page_url, img.url AS img_url, score
      ORDER BY score DESC
      LIMIT $k
    """
    with neo_driver.session() as sess:
        recs = sess.run(cypher,
                        brandSlug=brand_slug,
                        kw=question,
                        k=top_k)
        return [(r["text"], r["page_url"], r["img_url"]) for r in recs]

# ─────────────────────────────────────────────────────────────────────────────
# Call LLM and append "You can find more information here: <page_url>"
# ─────────────────────────────────────────────────────────────────────────────
def llm_answer(question: str, docs: List[Tuple[str, str, Optional[str]]]) -> str:
    """
    Use top_k slice texts as context for LLM to generate an answer.
    Finally append: "You can find more information here: <page_url>"
    """
    # 1) Concatenate the most relevant slice texts into the prompt
    blocks = [d[0] for d in docs if d[0]]
    prompt = (
        "You are a knowledgeable assistant for the MadeWithNestlé site.\n"
        "Use ONLY the following extracted web content to answer the user’s question in a concise way.\n"
        "Provide a best-effort answer,\n"
        "but always indicate uncertainty. Don’t hallucinate new facts.\n\n"
        + "\n\n".join(blocks[:5])
        + f"\n\nUser question: {question}\nAnswer (in concise form):"
    )

    # 2) Call the OpenAI LLM
    completion = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    answer = completion.choices[0].message.content.strip()

    # 3) Append "You can find more information here: <page_url>"
    if docs:
        first_page_url = docs[0][1] or ""
        answer += f"\n\nYou can find more information here: {first_page_url}"

    return answer

# ─────────────────────────────────────────────────────────────────────────────
# Generic Azure Search Fallback
# ─────────────────────────────────────────────────────────────────────────────
def azure_search(question: str, top_k: int = 5) -> List[Tuple[str, str, None]]:
    """
    Use Azure Search as a fallback when Neo4j returns no results.
    Returns a list of (content, url, None).
    """
    if search_client is None:
        return []
    try:
        results = search_client.search(search_text=question, top=top_k)
        return [(r.get("content", ""), r.get("url", ""), None) for r in results]
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# Feedback State Management
# ─────────────────────────────────────────────────────────────────────────────
# Global state flags to manage feedback phases:
# awaiting_feedback_body: waiting for user to enter full feedback text
# awaiting_feedback_email: waiting for user to provide email address
awaiting_feedback_body = False 
awaiting_feedback_email = False  
temp_feedback_body = ""

# Feedback CSV filename (stored in same directory as chat.py)
FEEDBACK_CSV = Path("feedback.csv")


def store_feedback_to_csv(feedback_lines: List[str], email: str) -> None:
    """
    Write feedback and email to FEEDBACK_CSV.
    CSV columns: feedback_body, email, handled
    'handled' defaults to False; administrators can manually update.
    """
    is_new = not FEEDBACK_CSV.exists()
    with open(FEEDBACK_CSV, mode="a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["feedback_body", "email", "handled"])
        body = "\n".join(feedback_lines).strip()
        writer.writerow([body, email, "False"])

# ─────────────────────────────────────────────────────────────────────────────
# Main Logic: Brand-first → Intent Classification (Quick Replies) → Feedback → Category-aware → Fallback
# ─────────────────────────────────────────────────────────────────────────────

def get_response(sentence: str) -> str:
    global awaiting_feedback_body, awaiting_feedback_email, temp_feedback_body

    # 1) If waiting for feedback email (Phase 2), process email input
    if awaiting_feedback_email:
        user_reply = sentence.strip()
        # If user replies "none", exit feedback workflow
        if user_reply.lower() == "none":
            awaiting_feedback_email = False
            temp_feedback_body = ""
            return "Alright, no feedback submitted."

        # Check if input contains "@", a simple email validation
        if "@" not in user_reply:
            return (
                "Please provide a valid email address. "
                "If you do not wish to submit feedback, reply with ‘none’."
            )

        email = user_reply
        store_feedback_to_csv([temp_feedback_body], email)

        # Clear state and thank the user
        awaiting_feedback_email = False
        temp_feedback_body = ""
        return "Thank you for your feedback. It has been submitted to the administrator."

    # 2) If waiting for feedback body (Phase 1), capture feedback text
    if awaiting_feedback_body:
        temp_feedback_body = sentence.strip()
        awaiting_feedback_body = False
        awaiting_feedback_email = True
        return (
            "Got it. Now please provide your email address so we can follow up. "
            "If you do not wish to submit feedback, reply with ‘none’."
        )

    # 3) Intent classification: check if this is a feedback intent
    tokens0 = tokenize(sentence)
    bow0    = torch.from_numpy(bag_of_words(tokens0, all_words)).to(DEVICE)
    with torch.no_grad():
        probs0, idx0 = torch.max(torch.softmax(model(bow0), dim=0), dim=0)
        conf0 = probs0.item()
        tag0 = intent_tags[idx0.item()]

    if DEBUG_PRINT:
        print(f"[DEBUG] Intent tag: {tag0}, confidence: {conf0:.3f}")

    if tag0 == "feedback" and conf0 > 0.75:
        # Trigger Phase 1: ask user for feedback body
        awaiting_feedback_body = True
        return (
            "Sure! Understand that you have a feedback that you would like to provide." 
            "Please enter your full feedback in the next message."
        )


    # 4) Brand-first: if user mentions a brand, perform brand-based retrieval
    sent_norm = normalize_text(sentence) 
    matched_brands = []
    for brand in BRANDS:
        slug = normalize_text(brand)            # e.g. "Kit Kat" -> "kit-kat"
        slug_nohyphen = slug.replace("-", "")   #                -> "kitkat"
        if (slug in sent_norm) or (slug_nohyphen in sent_norm):
            matched_brands.append(brand)

    if matched_brands and neo_driver is not None:
        chosen_brand = matched_brands[0]
        brand_slug   = normalize_text(chosen_brand)

        # If asking about "website"/"site"/"link", return direct URL
        if re.search(r"\bwebsite\b|\bsite\b|\blink\b", sentence, re.I):
            docs_site = neo4j_brand_search(brand_slug, sentence, top_k=1)
            if docs_site:
                return f"You can find more information here: {docs_site[0][1]}"
            else:
                return f"Sorry, I couldn’t locate the exact website for {chosen_brand} right now."

        # Otherwise perform brand-based search → LLM response + URL
        docs = neo4j_brand_search(brand_slug, sentence, top_k=5)
        if not docs:
            docs = azure_search(f"{chosen_brand} {sentence}")
        if docs:
            return llm_answer(sentence, docs)
        else:
            return f"Sorry, I found brand “{chosen_brand}” but couldn’t retrieve relevant content right now."

    # 5) Intent classification: quick fixed replies & category-aware
    tokens = tokenize(sentence)
    bow    = torch.from_numpy(bag_of_words(tokens, all_words)).to(DEVICE)
    with torch.no_grad():
        probs, idx = torch.max(torch.softmax(model(bow), dim=0), dim=0)
        conf = probs.item()
        tag = intent_tags[idx.item()]
    if DEBUG_PRINT:
        print(f"[DEBUG] Intent tag: {tag}, confidence: {conf:.3f}")

    # 5.1) Quick fixed replies: greeting/thanks/goodbye, etc., respond directly
    if tag in INTENT_LOOKUP and tag not in CATEGORY_TAGS and conf > 0.85:
        return random.choice(INTENT_LOOKUP[tag]["responses"])

    # 5.2) High-confidence category intent: perform category-based search
    if conf > 0.90 and tag in CATEGORY_TAGS:
        docs = neo4j_category_search(tag, sentence, top_k=5)
        if not docs:
            docs = azure_search(sentence)
        if not docs:
            return "I am not sure about that right now."
        return llm_answer(sentence, docs)

    # 6) Fallback RAG: if not brand and not high-confidence category, perform general retrieval
    docs = neo4j_category_search(tag, sentence, top_k=5) if tag in CATEGORY_TAGS else []
    if not docs:
        docs = azure_search(sentence)
    if docs:
        return llm_answer(sentence, docs)

    # 7) Final fallback
    return "I am not sure about that right now. For more information, please check on https://www.madewithnestle.ca/"

# ─────────────────────────────────────────────────────────────────────────────
# CLI Local Interaction Test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    while True:
        q = input("you: ")
        if q.lower() in {"quit", "exit"}:
            break
        print("Bot:", get_response(q))
