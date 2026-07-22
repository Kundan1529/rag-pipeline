# AXON — Enterprise AI Document Intelligence Platform

An **evidence-grounded RAG system**: it answers questions from your documents with
inline citations, **refuses (rather than guesses)** when the evidence is weak, and
exposes every retrieval and validation decision. On top of the RAG core it adds an
**Asset360** dashboard for physical assets, an **intelligent document-ingestion
workflow** that turns uploaded maintenance reports into structured Asset360 history,
and an **Adaptive Response Engine** that shapes each answer to the format, reading
level and persona the user asks for.

> Ask *"P-101 vibration is trending up — what's happening and what should I do?"*
> and AXON answers with cause, remaining-useful-life, a safety hard-gate, a drafted
> work order and clickable citations — while the knowledge graph lights up. Upload a
> bearing report and AXON detects it's a maintenance document, asks permission, then
> folds a verified work-order into Asset360 live.

📐 **Architecture diagram (7-layer RAG pipeline):** the presentation-ready diagram
lives as a Claude artifact — open it, screenshot it into your deck, or print to PDF.

---

## Contents

1. [Quick start](#quick-start)
2. [Using AXON](#using-axon) — chat, adaptive answers, Asset360, document ingestion
3. [How it works](#how-it-works)
4. [Feature map](#feature-map)
5. [Configuration & LLM gateway](#configuration--llm-gateway)
6. [API reference](#api-reference)
7. [Testing & evaluation](#testing--evaluation)
8. [Project layout](#project-layout)
9. [Troubleshooting](#troubleshooting)
10. [Roadmap / not built](#roadmap--not-built)

---

## Quick start

```bash
cd pipeline-review-and-fix
pip install -r requirements.txt          # 1. Python dependencies

# 2. (optional) image OCR needs the tesseract BINARY, not just the pip package:
brew install tesseract                   # macOS  (apt-get install tesseract-ocr on Linux)
#    without it, images still upload and index by metadata — text inside them
#    just isn't extracted.

cp .env.example .env                      # 3. config; paste YOUR token (see below):
#    HUGGINGFACEHUB_ACCESS_TOKEN=hf_xxxxxxxx   (or ANTHROPIC_API_KEY=sk-ant-...)

python backend/main.py                    # 4. serves http://127.0.0.1:8000
```

Then open **http://127.0.0.1:8000**.

- First launch downloads the reranker (`bge-reranker-base`, ~1.1 GB), the validation
  cross-encoder and the NLI model — one-time, then cached. Give it a minute.
- Run a second instance on another port with `PORT=8021 python backend/main.py`.
- **No API key?** It still runs — answers come back as grounded *extractive* summaries
  instead of composed prose, so the demo never breaks (see [LLM gateway](#configuration--llm-gateway)).

The `.venv` in the repo root already has the dependencies if you prefer:
`../.venv/bin/python backend/main.py`.

---

## Using AXON

### 1. Ask questions — grounded and cited

Type a question in the composer. AXON retrieves from the plant corpus **and everything
you've uploaded**, composes an answer, and attaches:

- **Inline citations** `[1] [2]` you can expand under **Sources**
- A **confidence badge** and a verdict (`RELEASE` / `RELEASE WITH CAVEATS` / `REFUSE`)
- A **Reasoning trace** (every agent stage) and a **knowledge-graph highlight** of what was used
- **Follow-up suggestions** to keep exploring

If the evidence doesn't support an answer, AXON **refuses and logs a knowledge gap**
rather than hallucinating.

### 2. Adaptive answers — say *how* you want it (Module 02)

The **Adaptive Response Engine** reads each question for the format, depth, length and
persona you want, and shapes the answer accordingly (while keeping grounding and
citations intact). The detected style shows as chips under the answer.

| Say something like… | You get |
|---|---|
| "…**in bullet points**" / "**as a table**" / "**as a checklist**" / "**as JSON**" | that output format |
| "**in simple words**" / "**for a manager**" / "**for an engineer**" / "**expert-level**" | reading level tuned to the audience |
| "**keep it short**" / "**detailed**" / "**a research-level report**" / "**walk me through the whole document**" | response length |
| "**act as a teacher**" / "**as an auditor**" / "**as an interviewer**" | persona-shaped answer |

Examples: *"explain the attention mechanism in simple words as bullet points"*,
*"compare LCEL and LangGraph as a table for a manager"*,
*"summarise P-101 failures as a checklist, act as a technician"*.

### 3. Asset360 — the digital profile of a physical asset

Click **⬢ Asset360** in the top bar and pick an asset (e.g. **P-101**). You get a live
dashboard assembled from the system of record:

- **Live condition + predicted remaining useful life** (vibration sparkline vs the ISO 10816 alert/danger lines)
- **Failure history / maintenance timeline** (work orders, downtime, failure modes)
- **Spares** (with low-stock flags), **connected equipment** from the P&ID, and **documents referencing the asset**

Nothing here is generated — it's a view over the knowledge graph, maintenance log,
spares list and sensor stream.

### 4. Turn a document into Asset360 history (Module 01)

Uploaded documents are **always indexed into RAG** (search) — that's unchanged. On top
of that, AXON runs an **intelligent ingestion workflow** so a maintenance report can
also update Asset360 — but **never automatically**; you decide.

```
Upload → OCR/parse → classify → maintenance-related?
   ├─ no  → Index into RAG only (current behaviour)
   └─ yes → ask permission → extract → preview & edit → confirm → Asset360 updates live
```

**Step by step in the UI:**

1. **Add a document** (sidebar **+ Add document** — pdf / docx / xlsx / csv / html / images / …).
   It's indexed for search immediately.
2. If it's classified as a **Maintenance Report / Inspection / Work Order / Breakdown /
   Root-Cause / Sensor Report**, a modal appears: *"This document appears to contain
   maintenance information. Update Asset360?"* → **Update Asset360** or **Index only**.
   Everything else is simply indexed.
3. **Index only** = exactly the old behaviour (chunk → embed → graph → search), no Asset360 change.
4. **Update Asset360** → if several assets are referenced you pick which one(s) (bonus),
   then AXON **extracts structured fields** (asset, date, work order, failure mode, root
   cause, action, parts, downtime, engineer, cost, source page, confidence).
5. A **preview dialog** shows the extracted event with **validation** (asset exists?
   part exists? duplicate work order? valid date?) and **Confirm / Edit / Cancel**.
   Nothing is written until you press **Confirm**.
6. On **Confirm**, Asset360 updates **live, without restarting the backend** — failure
   history, timeline, spares (drawn down), knowledge graph
   (`HAS_FAILURE` / `CAUSED_BY` / `USED_PART` / `RECOMMENDED`), documents and statistics.
7. **Source traceability:** the new history entry carries its source PDF, page and
   confidence — click it to open the original document at the extracted page beside the
   extracted fields.

Events are stored as a proper `maintenance_events` model in
`data/maintenance_events.json` and mirrored into `data/maintenance_log.csv` for backward
compatibility (no database required).

---

## How it works

Every question flows through the same pipeline:

```
Query
  │  Query understanding: adaptive style detection · spell correction · pronoun/topic memory · intent · entities
  ▼
Hybrid retrieval        dense (TF-IDF) + BM25 + graph proximity, weighted RRF
  ▼
Reranking               bge-reranker-base cross-encoder + entity coverage + metadata boost
  ▼
Evidence selection      MMR diversity · acceptance gate · aspect-coverage repair
  │                      (+ for a monitored asset: telemetry & maintenance history promoted to citable evidence)
  ▼
Generation              adaptive prompt (sections chosen per intent + requested format) → LLM gateway
  ▼
Validation              claim extraction → NLI entailment + numeric grounding + citation alignment/repair
  ▼
Confidence policy       fabrication floor → answer / caveat / refuse (grounded asset-data template for assets)
```

Everything is observable: `data/traces.jsonl` gets one JSON line per request (per-stage
latency, coverage, verdict, tokens), and the server log narrates spell corrections,
detected response profile, reranker collapse, evidence promotion, citation repair and
self-correction.

The full 7-layer view (UI → Document Processing → Indexing → Retrieval → Reasoning →
Validation → Response, plus Vision AI / Knowledge Graph / Memory / Personalization
cross-cutting services) is in the **architecture diagram artifact**.

---

## Feature map

| Area | What it does | Where |
|---|---|---|
| **Adaptive Response Engine** | detects output format (bullets/table/checklist/JSON/CSV/timeline/flowchart/page-wise/…), reading level (beginner→expert), length (short→walkthrough) and persona (teacher/engineer/auditor/…); shapes the prompt + follow-ups accordingly | `backend/response_engine.py`, `backend/llm.py` |
| **Query understanding** | corpus-grounded spell correction, conversation memory (resolves "it"/topic across turns), intent classification, entity extraction | `backend/rag/`, `backend/query_processor.py`, `backend/conversation.py` |
| **Multi-format ingestion** | PDF, Markdown/TXT, HTML, Word (.docx), Excel (.xlsx), CSV/TSV, JSON, images (OCR) — 14 formats, one handler registry | `backend/ingest.py` |
| **Asset360 document ingestion** | classify → permission → extract → validate → preview → commit; turns maintenance PDFs into structured Asset360 events with source traceability | `backend/document_classifier.py`, `maintenance_extractor.py`, `maintenance_validator.py`, `maintenance_service.py`, `asset360_updater.py`, `history_repository.py` |
| **Hybrid retrieval** | TF-IDF (dense stand-in) + BM25 + graph proximity, weighted Reciprocal Rank Fusion | `backend/retrieval.py` |
| **Reranking** | `bge-reranker-base` cross-encoder on the *clean* question, fused with entity coverage + bounded metadata boost; collapse fallback for out-of-domain text | `backend/retrieval.py` |
| **Validation** | claim-level checking with an **NLI entailment** model, numeric grounding, citation alignment + repair; a fabrication floor that distinguishes grounded synthesis from invented content | `backend/validator.py`, `backend/agents.py` |
| **Knowledge graph** | ontology-anchored graph (Equipment, SOP, Permit, WorkOrder, FailureMode, Cause, Part, Concept) with typed edges; in-process stand-in for Neo4j | `backend/kg.py` |
| **Asset360** | per-asset dashboard: live condition + RUL, maintenance history, spares, linked documents, P&ID neighbourhood | `/api/asset/{id}`, frontend ⬢ Asset360 |
| **Predictive / multi-agent** | anomaly + RUL by trend extrapolation to the ISO 10816 limit; Supervisor → Planner → Predictive · RootCause · Knowledge · Maintenance · Safety → Risk → Critic | `backend/predictive.py`, `backend/agents.py` |
| **Evaluation** | golden-set retrieval benchmark (hit@k, MRR, recall, nDCG) with a CI regression gate; opt-in full-pipeline answer scoring | `backend/evaluate.py`, `data/golden_qa.jsonl` |

---

## Configuration & LLM gateway

Answers are composed by the first available provider, then it degrades safely:

```
Hugging Face (HF_MODEL)  →  Anthropic Claude  →  deterministic extractive answer
```

`.env` keys (copy from `.env.example`):

| Key | Purpose |
|---|---|
| `HUGGINGFACEHUB_ACCESS_TOKEN` | **primary** provider. `HF_MODEL` defaults to `meta-llama/Llama-3.1-8B-Instruct` (free tiers work; large models need a paid Inference-Providers tier). |
| `ANTHROPIC_API_KEY` | **fallback** to `claude-opus-4-8` when HF is unavailable (also the only path to true image *understanding*). |
| `HF_MODEL`, `CLAUDE_MODEL` | override the model per provider. |
| `LLM_PROVIDER_ORDER` | reorder providers, e.g. `claude,hf`. |

- **No credentials at all** still works — the app answers from retrieved passages
  (extractive) and still honours the requested output format (e.g. renders a table).
- The page header and `GET /api/status` show the **active provider**. If answers come
  back as raw passages, the LLM leg is unavailable — check the token, or that your HF
  tier can serve `HF_MODEL`. A depleted-credits (402) or missing-key error disables that
  provider for the session so the app fails fast to the fallback instead of stalling.

> **Sharing note:** `.env` holds your secret token — it's git-ignored and must **not**
> be shared. Ship `.env.example`; each person adds their own token.

---

## API reference

**Core**

| Method & path | Purpose |
|---|---|
| `POST /api/ask` | ask a question → answer, citations, confidence, verdict, trace, `response_profile`, `followups` |
| `POST /api/compare` | side-by-side Vanilla RAG vs AXON GraphRAG |
| `GET  /api/status` | corpus/graph counts + active LLM provider |
| `GET  /api/assets`, `GET /api/asset/{id}` | asset list + full Asset360 profile |
| `GET  /api/graph`, `/api/graph/neighborhood/{id}`, `/api/telemetry`, `/api/pid` | graph, telemetry, P&ID |
| `POST /api/upload` | upload + index a document; response includes `classification`, `maintenance_detected`, `candidate_assets` |
| `GET/DELETE /api/documents[/{doc_no}]` | list / delete uploaded documents |

**Asset360 ingestion (Module 01)**

| Method & path | Purpose |
|---|---|
| `POST /api/maintenance/analyze` | classify a document; report whether it carries maintenance info |
| `POST /api/maintenance/extract` | extract structured events for the preview dialog (read-only) — body `{doc_no, asset_ids?}` |
| `POST /api/maintenance/commit` | validate + apply confirmed events to Asset360 live — body `{events:[…]}` |
| `GET  /api/maintenance/events`, `/api/maintenance/event/{id}` | list / fetch stored events |
| `GET  /api/uploads/{name}` | serve the original source file (for the traceability viewer) |

---

## Testing & evaluation

```bash
cd backend
python test_rag.py                 # unit tests for the rag/ package + validator (fast)
python evaluate.py                 # retrieval benchmark, gated against the baseline
python evaluate.py --no-rerank     # fast lexical-only run (seconds)
python evaluate.py --answers-only --limit 7   # full-pipeline answer scoring (uses the LLM)
python evaluate.py --update-baseline          # accept intentional metric changes
```

`evaluate.py` exits non-zero if any gated metric drops beyond tolerance — run it after
every change. **When you add a document, add 2–3 golden cases for it** in
`data/golden_qa.jsonl`, or the benchmark stops covering it.

---

## Project layout

```
pipeline-review-and-fix/
├─ backend/
│  ├─ main.py                 FastAPI app: serves the UI + all API endpoints
│  ├─ ingest.py               multi-format loader → corpus (chunks, docs, pid, maintenance, spares, sensors)
│  ├─ retrieval.py            hybrid retrieval + reranking
│  ├─ agents.py               multi-agent orchestration, synthesis, confidence policy
│  ├─ validator.py            claim validation (NLI + numeric + citations)
│  ├─ llm.py                  provider gateway + adaptive answer templates
│  ├─ kg.py                   knowledge graph
│  ├─ response_engine.py      Adaptive Response Engine (Module 02)
│  ├─ document_classifier.py  ┐
│  ├─ maintenance_extractor.py│
│  ├─ maintenance_validator.py│  Asset360 ingestion workflow (Module 01)
│  ├─ maintenance_service.py  │
│  ├─ asset360_updater.py     │
│  ├─ history_repository.py   ┘  maintenance_events store (+ CSV mirror)
│  └─ evaluate.py, test_rag.py
├─ frontend/                  index.html + app.js (single-page UI)
└─ data/                      SOPs, manuals, P&ID, CSVs, sensors, uploads/, traces.jsonl
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Answers come back as raw passages ("extractive summary…") | No working LLM. Check `HUGGINGFACEHUB_ACCESS_TOKEN` / `ANTHROPIC_API_KEY` in `.env`, or that your HF tier serves `HF_MODEL`. |
| `402 Payment Required` in the log | HF inference credits depleted — top up, subscribe to HF PRO, or set `ANTHROPIC_API_KEY`. AXON keeps working via the extractive fallback. |
| Requested a table but got prose (with an LLM) | Small models don't always comply; the offline fallback always renders a real table. Larger models (Claude) follow formats reliably. |
| Text inside images isn't searchable | Install the tesseract **binary** (`brew install tesseract`) — the pip package alone isn't enough. |
| First request is slow | One-time model downloads (reranker + NLI). Subsequent runs are cached. |
| A correct maintenance answer got refused | Fixed: a monitored asset's telemetry + work-order history are promoted to citable evidence, and a grounded asset-data template answers instead of refusing. |

---

## Roadmap / not built

Multi-tenancy / ABAC, Docker/Neo4j/Qdrant/OpenSearch/Kafka (replaced by in-process
equivalents with the same semantics), token streaming, and a **vision model** for
diagram *understanding* (the ingest seam is ready; OCR covers text-in-images today).

## Corpus (shipped demo data)

- 4 SOPs + 1 vendor manual (`data/sops`, `data/manuals`)
- 1 P&ID topology (`data/pid/pid_area1.json`), CMMS log + spares (`data/*.csv`)
- 14 days of hourly P-101 telemetry with an accelerating bearing-wear trend
- Uploaded documents live in `data/uploads/` (git-ignored)
