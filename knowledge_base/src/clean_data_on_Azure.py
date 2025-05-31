from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv
import os

load_dotenv()
endpoint = os.getenv("AZ_SEARCH_ENDPOINT")
key      = os.getenv("AZ_SEARCH_KEY")
index    = "nestle-data"

client = SearchClient(endpoint=endpoint,
                      index_name=index,
                      credential=AzureKeyCredential(key))

ids = []
results = client.search(search_text="*",
                        select=["id"],
                        top=1000)   
for doc in results:
    ids.append(doc["id"])

print(f"Found {len(ids)} documents to delete.")

batch_size = 500
for i in range(0, len(ids), batch_size):
    batch_ids = ids[i : i + batch_size]
    actions = [{"id": did} for did in batch_ids]
    result = client.delete_documents(documents=actions)
    print(f"Deleted batch {i // batch_size + 1}: {len(actions)} docs.")

print("✅ All existing documents deleted.")
