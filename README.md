# AXON — Industrial Knowledge Operating System

An evidence-grounded RAG system: it answers questions from your documents with
inline citations, refuses (rather than guesses) when the evidence is weak, and
exposes every retrieval and validation decision. It began as the 48-hour AXON
hackathon slice (the *"pump about to fail, saved by the brain"* story) and has
since grown a full query-understanding, reranking, validation and evaluation
stack.

> Ask *"P-101 vibration is trending up — what's happening and what should I do?"*
> and AXON answers with cause, remaining-useful-life, a safety hard-gate, a
> drafted work order and clickable citations — while the knowledge graph lights
> up. Ask *"what is multi-head attention?"* about an uploaded paper and it
> answers from the paper, cited, refusing anything the evidence doesn't support.

---

## Pipeline (what happens to every question)

```
Query
  │  Query understanding (rag/ package)
  ▼  spell correction · style detection · pronoun/topic memory · intent · entities
Hybrid retrieval        dense (TF-IDF) + BM25 + graph proximity, weighted RRF
  ▼
Reranking               bge-reranker-base cross-encoder + entity coverage + metadata
  ▼
Evidence selection      MMR diversity · acceptance gate · aspect-coverage repair
  ▼
Generation              adaptive prompt (sections chosen per intent) → LLM gateway
  ▼
Validation              claim extraction → NLI entailment + numeric grounding
  ▼  citation repair · one LLM self-correction (accept only if coverage improves)
Confidence policy       coverage floor → answer / caveat / refuse (never hallucinate)
```

Everything is observable: `data/traces.jsonl` gets one JSON line per request
(per-stage latency, coverage, verdict, tokens), and the server log narrates
spell corrections, intent, reranker collapse, citation repair and
self-correction.

## Capabilities

| Area | What it does | Where |
|---|---|---|
| **Query understanding** | corpus-grounded spell correction, response-style detection (points / table / short / detailed), conversation memory (resolves "it"/topic across turns), intent classification, entity extraction | `backend/rag/`, `backend/query_processor.py`, `backend/conversation.py` |
| **Multi-format ingestion** | PDF, Markdown/TXT, HTML, **Word (.docx)**, **Excel (.xlsx)**, CSV/TSV, JSON, and **images (OCR)** — 14 formats, one handler registry | `backend/ingest.py` |
| **Hybrid retrieval** | TF-IDF (dense stand-in) + BM25 + graph proximity, weighted Reciprocal Rank Fusion, inverted-index backed | `backend/retrieval.py` |
| **Reranking** | `bge-reranker-base` cross-encoder judged on the *clean* question, fused with IDF-weighted entity coverage and bounded metadata boost; collapse fallback for out-of-domain text | `backend/retrieval.py` |
| **Validation** | claim-level checking with an **NLI entailment** model (catches entity-swap / plausible-but-wrong claims a relevance scorer can't), numeric grounding, citation alignment + repair | `backend/validator.py` |
| **Self-correction & confidence** | one grounded rewrite accepted only if coverage improves; a confidence floor suppresses low-coverage fabrications | `backend/agents.py`, `backend/validator.py` |
| **Knowledge graph** | ontology-anchored graph (Equipment, SOP, Permit, WorkOrder, FailureMode, Concept) with typed edges; in-process stand-in for Neo4j | `backend/kg.py` |
| **Asset360** | per-asset dashboard: live condition + RUL, maintenance history, spares, linked documents, P&ID neighbourhood — a view over the system of record | `/api/asset/{id}`, frontend ⬢ Asset360 |
| **Predictive / multi-agent** | anomaly + RUL by trend extrapolation to the ISO 10816 limit; Supervisor → Planner → Predictive · RootCause · Knowledge · Maintenance · Safety → Risk → Critic | `backend/predictive.py`, `backend/agents.py` |
| **Evaluation** | golden-set retrieval benchmark (hit@k, MRR, recall@1/5, nDCG, precision@k) with a CI regression gate; opt-in full-pipeline answer scoring (faithfulness, hallucination rate, refusal accuracy) | `backend/evaluate.py`, `data/golden_qa.jsonl` |

## Install

```bash
cd pipeline-review-and-fix
pip install -r requirements.txt          # 1. Python dependencies

# 2. (optional) image OCR needs the tesseract BINARY, not just the pip package:
brew install tesseract                   # macOS  (apt-get install tesseract-ocr on Linux)
#    without it, images still upload and index by metadata — text inside them
#    just isn't extracted.

cp .env.example .env                      # 3. config; paste YOUR token:
#    HUGGINGFACEHUB_ACCESS_TOKEN=hf_xxxxxxxx   (or ANTHROPIC_API_KEY=sk-ant-...)

python backend/main.py                    # 4. serves http://127.0.0.1:8000
```

First launch downloads the reranker (`bge-reranker-base`, ~1.1 GB), the
validation cross-encoder and the NLI model — one-time, then cached.
Set `PORT=8021 python backend/main.py` to run a second instance on another port.

Open http://127.0.0.1:8000, ask about the documents in `data/uploads/`, or add
your own with **+ Add document** (pdf / docx / xlsx / csv / html / images / …).

## LLM gateway

Answers are composed by the first available provider, then it degrades safely:

```
Hugging Face (HF_MODEL)  →  Anthropic Claude  →  deterministic extractive answer
```

- **HF is primary.** Put `HUGGINGFACEHUB_ACCESS_TOKEN` in `.env`. `HF_MODEL`
  defaults to `meta-llama/Llama-3.1-8B-Instruct` (works on most free tiers;
  large models need a paid Inference-Providers tier).
- **Claude fallback:** set `ANTHROPIC_API_KEY` to use `claude-opus-4-8` when HF
  is unavailable. (Claude is also the only path to true image *understanding* —
  OCR reads text in images, not diagrams.)
- **No credentials at all** still works: the app answers from retrieved
  passages (extractive) instead of composing prose, so the demo never breaks.

If answers come back as raw passages, the LLM leg is unavailable — check the
token in `.env`, or that your HF tier can serve `HF_MODEL` (the page header
shows the active provider). Provider order is configurable via
`LLM_PROVIDER_ORDER`.

> **Sharing note:** `.env` holds your secret token — it's git-ignored and must
> **not** be shared. Ship `.env.example`; each person adds their own token.

## Evaluate

```bash
cd backend
python evaluate.py                 # retrieval benchmark, gated against the baseline
python evaluate.py --no-rerank     # fast lexical-only run (seconds)
python evaluate.py --answers-only --limit 7   # full-pipeline answer scoring (LLM cost)
python evaluate.py --update-baseline          # accept intentional metric changes
python test_rag.py                 # unit tests for the rag/ package + validator
```

`evaluate.py` exits non-zero if any gated metric drops beyond tolerance — run it
after every change. **When you add a document, add 2–3 golden cases for it** in
`data/golden_qa.jsonl`, or the benchmark stops covering it.

## Demo script

1. Open http://127.0.0.1:8000 — click **⬢ Asset360**, pick **P-101**: red RUL
   countdown, vibration sparkline crossing the ISO 10816 alert line, 3 past
   bearing failures, spares at minimum stock.
2. Ask **"P-101 vibration is trending up — what's happening and what should I do?"**
   — the agents trace populates, the graph lights up around P-101, and the answer
   gives cause → RUL → LOTO hard-gate (SAF-12) → drafted work order, every claim
   cited with a confidence badge.
3. Upload a PDF/resume/screenshot and ask about it — *"summarize the whole paper"*
   (document mode), *"compare X and Y as a table"* (adaptive format), or
   *"what is the pricing of …"* about something not in the corpus (watch it refuse).

## Corpus (shipped demo data)

- 4 SOPs + 1 vendor manual (`data/sops`, `data/manuals`)
- 1 P&ID topology (`data/pid/pid_area1.json`), CMMS log + spares (`data/*.csv`)
- 14 days of hourly P-101 telemetry with an accelerating bearing-wear trend
- Uploaded documents live in `data/uploads/` (git-ignored — the LangChain books
  and the Attention paper are examples, not committed)

## Deliberately not built

Multi-tenancy / ABAC, Docker/Neo4j/Qdrant/OpenSearch/Kafka (replaced by
in-process equivalents with the same semantics), token streaming, and a **vision
model** for diagram *understanding* (blocked on a vision-capable provider key —
the ingest seam is ready; OCR covers text-in-images today).
