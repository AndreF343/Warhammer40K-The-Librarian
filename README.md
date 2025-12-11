# Warhammer 40K Automation Stack

<img width="2816" height="1536" alt="Gemini_Generated_Image_f20a91f20a91f20a" src="https://github.com/user-attachments/assets/839e6561-d1f2-4b86-9cc8-5492654f900f" />

Automation suite that scrapes Warhammer 40,000 wiki data, stores it in Postgres/pgvector, evaluates LLM behaviors with synthetic tests, and serves a tool-using chat agent — all orchestrated with n8n and Docker Compose.

---

## Table of Contents
1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Getting Started](#getting-started)
4. [Key Workflows](#key-workflows)
5. [Data & Knowledge Graph Utilities](#data--knowledge-graph-utilities)
6. [Testing & Evaluation Loop](#testing--evaluation-loop)
7. [Directory Layout](#directory-layout)
8. [Troubleshooting & Tips](#troubleshooting--tips)
9. [Roadmap](#roadmap)

---

## Architecture

| Layer | Purpose | Notes |
| --- | --- | --- |
| **Docker Compose Stack** | Spins up n8n, Postgres (pgvector), and optional helpers (Adminer, Qdrant, Langfuse). | See `docker-compose.yaml` — n8n binds `./data` into `/workspace/data`; Postgres credentials live in `.env`. |
| **Data Ingestion** | Converts MediaWiki pages into cleaned sections, infobox key/value maps, and vector embeddings. | Implemented in the `WH40K_Ingestion` workflow. |
| **Knowledge Graph Scripts** | Python tooling for bulk scraping, cleaning, and LightRAG export. | Lives in `KnowledgeGraph/` and mirrors the n8n transforms. |
| **LLM Evaluation** | Generates Airtable-managed synthetic test suites and runs DeepEval/LangChain-style checks. | Implemented in the `Synthetic Test Generation` workflow. |
| **Retrieval & QA Agent** | GPT-5.1 agent that chains hybrid search, SQL, knowledge graph, and PG vector store. | Implemented in the `Multitool Agent` workflow. |

---

## Prerequisites

- Docker + Docker Compose v3.9+
- Bash (for the `./up` helper script)
- Credentials for:
  - OpenAI API (`openAiApi`)
  - Airtable Personal Access Token (`airtableTokenApi`)
  - Cohere API (`cohereApi`)
  - Postgres DSN (`postgres`, “Postgres account 2” inside n8n)
- (Optional) Langfuse, Qdrant, or other services if you enable their compose overlays

---

## Getting Started

1. **Copy environment defaults**

   ```bash
   cp .env.example .env
   ```

   Populate `.env` with n8n auth, OpenAI, Langfuse, etc.

2. **Launch services**

   ```bash
   chmod +x up
   ./up Postgres Adminer
   ```

   The helper script layers `docker-compose.yaml` with any `<Service>/docker-compose.yml` files you name.

3. **Access n8n**
   - URL: `http://localhost:5678`
   - Auth: values from `.env` (`N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD`)

4. **Import workflows**
   - In n8n, go to *Workflows → Import from File*.
   - Import each JSON from `N8N_Workflows/` (`WH40K_Ingestion`, `Synthetic Test Generation`, `Multitool Agent`).

5. **Relink credentials**
   - Update OpenAI, Airtable, Cohere, and Postgres credentials inside each workflow.

6. **Seed data (optional)**
   - Generate URL lists via `python get_urls.py --out data/wh40k_urls.csv`.
   - Place source CSV/JSON files into `./data` to match the container mount.

---

## Key Workflows

<img width="1582" height="670" alt="n8npic1" src="https://github.com/user-attachments/assets/1c865b6f-830c-4a0f-8a30-7a690870caf7" />

### WH40K_Ingestion

- **Input:** `/workspace/data/wh40k_urls.csv`
- **Highlights:** Reads URLs → calls MediaWiki `parse` → custom JS nodes clean HTML, split sections, flatten infobox entries → generates embeddings → writes to `pages` (metadata) and `warhammer_vectors` (pgvector chunks).
- **Goal:** Keep relational metadata and vector store synchronized with the wiki.

### Synthetic Test Generation

- **Purpose:** Build Airtable-managed evaluation suites.
- **Highlights:** Fetch metric catalog from Airtable → sample random documents from Postgres → chunk and summarize text → prompt OpenAI for structured JSON test cases → write test cases/run metadata back to Airtable and trigger executions.
- **Config knobs:** `Set Vars` node controls chunk size and questions per chunk; prompts define evaluation schema.

<img width="1850" height="627" alt="n8npic2" src="https://github.com/user-attachments/assets/f8a373c1-11c7-4be2-b5c2-6b5442008388" />

### Multitool Agent

- **Entry points:** Chat trigger or callable workflow input (for other flows).
- **Tool stack:** Hybrid search (`hybrid_search` SQL), Warhammer SQL (read-only Postgres), Warhammer Expert Tool (knowledge graph), PG vector store + context expansion, Cohere rerank.
- **System prompt:** Enforces tool usage order, grounding rules, and fallback (“Sorry I don't know” if nothing relevant is found).
- **Outcome:** GPT-5.1 answers with citations based strictly on retrieved context.

---

## Data & Knowledge Graph Utilities

Located under `KnowledgeGraph/`:

| Script | Description |
| --- | --- |
| `fetch_warhammer_pages_for_lightrag_v4.py` | Requests MediaWiki pages via `parse`, converts them into Markdown with front matter, saves under `KnowledgeGraph/out/pages`. |
| `csv_to_lightrag_docs.py` | Converts `pages_kg_pc.csv` rows into LightRAG-ready Markdown documents. |
| `url_split.py` | Dataclass pipeline that normalizes categories, cleans page content, and writes Markdown docs with YAML headers. |
| `pages_kg.csv`, `pages_kg_pc.csv` | Intermediate CSV exports shared between Python scripts and n8n workflows. |
| `lightgraph_processing.png` | Architecture diagram for the knowledge graph flow. |

Use these scripts for bulk ingestion or when you need offline LightRAG artifacts.

---

## Testing & Evaluation Loop

1. Run `WH40K_Ingestion` to refresh Postgres + pgvector content.
2. Run `Synthetic Test Generation` to create or update Airtable test suites.
3. Execute tests (Airtable workflows / DeepEval integrations) and monitor statuses (`Enabled → In Progress → Complete/Error`).
4. Iterate on prompts, chunking, or metrics based on failures or coverage gaps.

---

## Directory Layout

```
├─ docker-compose.yaml
├─ .env
├─ .gitignore
├─ up
├─ data/
│  ├─ warhammer_urls.json
│  ├─ wh40k_urls*.csv
│  └─ out/pages
├─ KnowledgeGraph/
│  ├─ fetch_warhammer_pages_for_lightrag_v4.py
│  ├─ csv_to_lightrag_docs.py
│  ├─ url_split.py
│  └─ out/pages
├─ N8N_Workflows/
│  ├─ WH40K_Ingestion.json
│  ├─ Synthetic Test Generation.json
│  └─ Multitool Agent.json
├─ Adminer/
├─ Postgres/
└─ lightgraph_processing.png
```

---

## Troubleshooting & Tips

- **n8n auth failures:** Verify `.env` matches the credentials you use to log in.
- **File path errors:** Remember n8n runs inside the container; ensure files exist in `./data` on the host.
- **Slow embeddings:** Adjust `batchSize` or concurrency in the Embeddings node; confirm `OPENAI_API_KEY`.
- **Postgres connection issues:** Confirm the `pg_n8n_data` volume exists and the `pgvector/pgvector:pg18` image is running.
- **Airtable throttling:** Set batching delays on nodes like `Generate Questions` or `Create the Test Case`.

---
