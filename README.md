# Nestle Chatbot Test

This repository implements an end-to-end pipeline for scraping, processing, embedding, graph construction, and chatbot deployment. 

Azure chatbot link: http://nestle-chatbot-app-jiamei-shu.azurewebsites.net/

Flask application deployed to Azure through Azure CLI

<img width="1507" alt="Screenshot 2025-05-31 at 14 54 50" src="https://github.com/user-attachments/assets/3e503e79-9632-4da1-a9f3-889dad39365a" />
<img width="1510" alt="Screenshot 2025-05-31 at 14 55 00" src="https://github.com/user-attachments/assets/1eaac6b2-59f9-4fe8-93b6-a88f6e41c249" />
<img width="1508" alt="Screenshot 2025-05-31 at 14 55 12" src="https://github.com/user-attachments/assets/d8257e4b-ee94-4a1b-8606-8f5ad9d66659" />
<img width="1512" alt="Screenshot 2025-05-31 at 16 23 31" src="https://github.com/user-attachments/assets/b70b5aea-3823-41be-8f84-05eaa50be19e" />
<img width="1512" alt="Screenshot 2025-05-31 at 14 58 14" src="https://github.com/user-attachments/assets/1a6d0a39-2ec4-48b8-a540-e0558c60bde5" />


## Structure - Content Ingestion
The files and their relationships (left → right) follow the execution order from top to bottom.

1. **knowledge_base/src/save\_auth\_cookie.py → knowledge_base/src/auth.json**

   * Saves authentication cookies for pages requiring consent or login.
   * Generates `knowledge_base/src/auth.json` with session tokens used by other scripts.

2. **knowledge_base/src/scrape\_full.py → knowledge_base/scraped\_data\_async**

   * Recursively crawls Nestlé website concurrently.
   * Scraped over 700 subpages from the MadeWithNestlé website.
   * Outputs raw HTML, cleaned text (`*_text.json`), extracted tables (`*_tables.json`), and image binaries into `data/scraped_data_async`.

3. **knowledge_base/src/scrape\_incremental.py**

   * (Optional) Re-crawls only new or updated pages since last run for efficient updates.

4. **knowledge_base/src/classify\_urls.py → knowledge_base/classified\_urls.jsonl**

   * Reads scraped URLs, applies regex rules and LLM calls to assign each URL to a category (Product, Recipe, Blog, etc.).
   * Writes category mappings to `knowledge_base/classified_urls.jsonl`.

5. **knowledge_base/src/enrich\_assets.py**

   * Cleans and deduplicates assets.
   * Fixes encoding issues and enriches metadata like url, image, table before slicing.

6. **knowledge_base/src/page.py → knowledge_base/src/text\_splitter.py → knowledge_base/src/splitter.py → knowledge_base/slices**

   * `page.py`: Utilities to load and normalize individual page JSON files.
   * `text_splitter.py`: LLM-based intelligent document chunking by semantic sections.
   * `splitter.py`: Heuristic text splitter for character/sentence-based chunks.
   * Combined tools produce gzipped slices `knowledge_base/slices.jsonl.gz` for embedding.

7. **knowledge_base/src/embed\_and\_upload.py → knowledge_base/slices\_with\_embed.jsonl.gz**

   * Fetches slices, calls OpenAI Embeddings API per chunk, averages vectors.
   * Writes embedded slices to `knowledge_base/slices_with_embed.jsonl.gz`, and merges embedding back into Azure Search index.

8. **knowledge_base/src/upload\_to\_neo4j.py**

   * Reads documents and embeddings from Azure Search.
   * Load slices_with_embed.jsonl(.gz) and import all data into Neo4j with the following structure:(:Category)<-[:IN_CATEGORY]-(:Page)-[:HAS_SLICE]->(:Slice)-[:HAS_IMAGE]->(:Image)
   * Each Image node stores `url` and `title` properties.

## Structure - Chatbot and UI

**train.py**

* Script that reads intents.json, trains the classifier, and saves data.pth.

**chat.py**

* Contains the chatbot logic: intent classification, retrieval (Neo4j/Azure Search), and calls to the OpenAI API.
* Traceability Mechanism: Each chatbot response automatically includes the URLs of the source websites, allowing users to click and view the original information directly.
* Feedback Mechanism: When the chatbot detects that a user wants to provide feedback, it prompts the user for their feedback and email address. The submitted information is temporarily stored in feedback.csv for the administrator to process.

**app.py**

* The Flask application entry point. Defines the HTTP routes and spins up the web server.

**data.pth**

* Serialized PyTorch model weights for the intent classifier.

**intents.json**

* The intent definitions: user example sentences (“patterns”) and canned responses.

**model.py**

* Defines the NeuralNet PyTorch architecture used for intent classification.

**nltk_utils.py**

* Tokenization and bag-of-words helpers for converting user input into model features.





## Real-time Scraping Updates

To support near real-time ingestion, I’ve maintained two distinct scripts: scrape_full.py and scrape_incremental.py. In this project I ran scrape_full.py to crawl and capture the entire site (700+ subpages) from scratch. For ongoing updates, scrape_incremental.py can track each page’s last‐scrape timestamp and compare it against the source’s Last-Modified header—only pages modified since the last run will be re-scraped. This incremental approach can be automated (for example, via a cron job or Azure Function) to run every 30 minutes, ensuring fresh content without repeatedly crawling the entire site.



## RAG with Azure Cognitive Search
 
To enable effective retrieval-augmented generation, I first prepare the scraped Nestlé content through three coordinated steps:
 
1. **Comprehensive Data Preparation**  
   - Gather every HTML page and its associated text for maximum coverage.  
   - Deduplicate and trim navigation bars, scripts, and repeated boilerplate.  
   - Extract page titles from `<title>`, `og:title`, or the first heading as human-readable identifiers.  
   - Flatten tables into inline “[table] header | cell | …” lines.  
   - Collect up to five meaningful images per page, assigning roles and captions while filtering out icons and decorative elements.  
   - Annotate each document’s metadata with token counts, table counts, and image counts.  
 
2. **Slicing and Embedding**  
   - Split each cleaned document into manageable “slices” (e.g., paragraph-level chunks) to balance retrieval granularity with context.  
   - Generate vector embeddings for every slice using OpenAI’s `text-embedding-3-small` model, retaining both the slice content and its embedding.  
 
3. **Batch Upload to Azure Search**  
   - Upload slices in tuned batches (16 for embedding requests, 100 for index ingestion) to the `nestle-data` Azure Search index.  
   - Index each record with:  
     - A unique identifier and source URL  
     - Page title and category  
     - Associated images and captions  
     - Slice text content  
     - Precomputed embedding vector  
 
This pipeline ensures that Azure Search holds semantically rich, fine-grained segments of the entire site. At query time, similarity searches over these embeddings surface the most relevant passages, with attached metadata (categories, titles, images) providing essential context for downstream LLM prompts.



## GraphRAG with Neo4j
 
To complement vector retrieval with structural context, I mirror the sliced, embedded content into a Neo4j graph following the hierarchy Category → (Product → Brand → Page) Page → Slice → Image:
 
1. **Node and Relationship Schema**  
   - **Category** nodes group pages by high-level classification (e.g., Recipe, Product).  
   - **Brand** nodes groups brands in the product category, linked to their pages.
   - **Page** nodes represent individual URLs, storing title and category link.  
   - **Slice** nodes store each text chunk’s content, unique ID, and embedding.  
   - **Image** nodes capture meaningful visuals (URL and title), linked to their slices.  
   - Relationships:  
     - `(:Category)<-[:IN_CATEGORY]-(:Page)`  
     - `(:Page)-[:HAS_BRAND]->(:Brand)`  
     - `(:Page)-[:HAS_SLICE]->(:Slice)`  
     - `(:Slice)-[:HAS_IMAGE]->(:Image)`  
 
2. **Batch Ingestion Workflow**  
   - Read `slices_with_embed.jsonl.gz` in configurable batches (e.g., 500 slices per transaction).  
   - For each slice:  
     - Merge on category and page to avoid duplicates.  
     - Merge on slice ID, setting content and embedding upon creation.  
     - Create or update Image nodes with titles.  
   - Commit each batch in a single Cypher transaction for atomicity and performance.  
 
3. **Enabling Graph-Enhanced Retrieval**  
   - Match on slice embeddings (via Neo4j’s vector plugin) to find top-k relevant slices.  
   - Traverse from those slices to their parent pages and categories for broader context.  
   - Include related images or sequential slice links for richer answer construction.  
 
By unifying vector similarity (Azure Search) with explicit document structure and metadata (Neo4j), the dual-layer RAG architecture empowers the chatbot to retrieve the most semantically pertinent passages while understanding their placement within the overall site hierarchy—delivering precise, context-aware responses.


## User-Driven GraphRAG Customization

To make the chatbot adaptable to evolving knowledge, I’ve introduced a “feedback” intent that lets end users flag missing or outdated content. A set of example utterances trains the intent detector, so when a user reports an issue (“this page isn’t up to date,” “I have a suggestion,” etc.), the bot captures that feedback and writes it to a staging database. Administrators can then review and approve additions or new relationships via the process_feedback.py pipeline before they’re merged into the Neo4j graph. This workflow empowers continual, controlled enrichment of the GraphRAG knowledge base.

<img width="1512" alt="Screenshot 2025-05-31 at 16 17 55" src="https://github.com/user-attachments/assets/8c6f86ec-c3bf-4ca5-9359-546a41856535" />
<img width="1512" alt="Screenshot 2025-05-31 at 16 18 16" src="https://github.com/user-attachments/assets/bfad5ab7-9639-40db-ba13-1b4fa0cb2a37" />



## Limitations

One limitation is that, due to time constraints, I did not further refine the relationships between individual nodes in the Neo4j graph. Establishing more granular connections—such as linking related slices across pages, defining sequential or thematic edges, or modeling user interaction patterns—would enable the chatbot to traverse the knowledge graph more intelligently and provide even more precise, contextually rich answers. Future work could explore these finer-grained relationship schemas to enhance overall retrieval accuracy and conversational relevance.



---

*End of README*
