"""
embed_and_upload.py

Ingest local slices, generate embeddings, upload to Azure Search, and dump to slices_with_embed.jsonl.gz.
"""
import os, json, gzip, itertools, math
from pathlib import Path
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
import openai

# ────────── Environment Variables & Client ──────────
load_dotenv()
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
AZ_SEARCH_KEY      = os.getenv("AZ_SEARCH_KEY")
AZ_SEARCH_ENDPOINT = os.getenv("AZ_SEARCH_ENDPOINT")

INDEX_NAME          = "nestle-data"
IN_FILE             = Path("slices.jsonl.gz")
OUT_FILE            = Path("slices_with_embed.jsonl.gz")
EMBED_MODEL         = "text-embedding-3-small"
BATCH_SIZE_EMBED    = 16
BATCH_SIZE_UPLOAD   = 100

openai.api_key      = OPENAI_API_KEY
search_client = SearchClient(
    endpoint   = AZ_SEARCH_ENDPOINT,
    index_name = INDEX_NAME,
    credential = AzureKeyCredential(AZ_SEARCH_KEY),
)

# ────────── Utility Functions ──────────
def read_slices(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)

def grouper(it, n):
    it = iter(it)
    while chunk := list(itertools.islice(it, n)):
        yield chunk

# ────────── Main Process ──────────
def embed_and_upload():
    docs_iter = read_slices(IN_FILE)
    total, uploaded = 0, 0

    with gzip.open(OUT_FILE, "wt", encoding="utf-8") as fout:

        for batch in grouper(docs_iter, BATCH_SIZE_EMBED):
            texts = [d.get("text", d["content"]) for d in batch]

            # --- Embedding ---
            resp = openai.Embedding.create(model=EMBED_MODEL, input=texts)
            vectors = [row.embedding for row in resp.data]

            # --- Merge embedding field ---
            for doc, vec in zip(batch, vectors):
                doc["embedding"] = vec
                if "content" not in doc: 
                    doc["content"] = doc.pop("text")

                fout.write(json.dumps(doc, ensure_ascii=False) + "\n")

            # ---  Batch upload to Azure ---
            for upload_chunk in grouper(batch, BATCH_SIZE_UPLOAD):
                actions = [
                    {
                        "id":           d["id"],
                        "url":          d["url"],
                        "website_title": d["title"],
                        "category":     d.get("category", "Uncategorized"),
                        "images":       d.get("images", []),
                        "image_titles": d.get("image_titles", []),
                        "content":      d["content"],
                        "embedding":    d["embedding"],
                    }
                    for d in upload_chunk
                ]
                search_client.merge_or_upload_documents(actions)
                uploaded += len(actions)
                print(f"Uploaded {len(actions)} slices…")

            total += len(batch)

    print(f"✅ Completed: Embedded {total} documents; uploaded to Azure and wrote to {OUT_FILE}")

if __name__ == "__main__":
    embed_and_upload()
