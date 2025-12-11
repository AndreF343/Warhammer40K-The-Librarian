# Warhammer 40K Automation Stack (n8n Regression Checks)

Turn-key automation suite that scrapes Warhammer 40,000 wiki data, stores it in Postgres/pgvector, evaluates LLM behaviors with synthetic tests, and exposes a tool-using chat agent – all orchestrated through n8n and Docker Compose.

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
| **Docker Compose Stack** | Spins up n8n, Postgres (with pgvector), and optional helpers like Adminer/Qdrant/Langfuse. | See `docker-compose.yaml` – n8n binds `./data` into `/workspace/data`, Postgres credentials live in `.env`. |
| **Data Ingestion** | Converts MediaWiki pages into cleaned sections, infobox key/value maps, and vector embeddings. | `WH40K_Ingestion` workflow. |
| **Knowledge Graph Scripts** | Standalone Python tooling for bulk scraping, cleaning, and LightRAG export. | `KnowledgeGraph/` scripts mirror the n8n transforms. |
| **LLM Evaluation** | Generates Airtable-managed synthetic test suites and runs DeepEval/Langchain-style checks. | `Synthetic Test Generation` workflow. |
| **Retrieval & QA Agent** | Tool-using GPT-5.1 agent that chains hybrid search, SQL, knowledge graph, and PG vector store. | `Multitool Agent` workflow. |

---

## Prerequisites

- Docker + Docker Compose v3.9+
- Bash (needed for the `./up` helper script)
- n8n-compatible credentials:
  - OpenAI API key (`openAiApi`)
  - Airtable Personal Access Token (`airtableTokenApi`)
  - Cohere API key (`cohereApi`)
  - Postgres DSN (`postgres` / “Postgres account 2”)
- (Optional) Langfuse, Qdrant, or additional services if you enable their compose overlays.

---

## Getting Started

1. **Copy environment defaults**

   ```bash
   cp .env.example .env   # use .env to set n8n auth, OpenAI, Langfuse, etc.
