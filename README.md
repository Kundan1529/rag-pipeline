# AXON — Industrial Knowledge Operating System (MVP slice)

The 48-hour hackathon vertical slice from the AXON design document (§17): the
**"pump about to fail, saved by the brain"** story, end to end.

> Ask: *"P-101 vibration is trending up — what's happening and what should I do?"*
> and AXON answers with cause, RUL, safety hard-gate, a drafted work order, and
> clickable citations — while the knowledge graph lights up.

## What's in the slice

| Design-doc concept | MVP implementation |
|---|---|
| Ingestion pipeline (§5) | `backend/ingest.py` — frontmatter metadata, structure-aware chunking by section, tag/entity extraction |
| Vision: P&ID → graph (§9) | `data/pid/pid_area1.json` simulates the vision service output (symbols + line tracing + tag OCR) and is rewired into `CONNECTED_TO` edges |
| Knowledge graph (§7) | `backend/kg.py` — ontology-anchored graph (Equipment, SOP, Permit, WorkOrder, FailureMode) with `CONNECTED_TO / GOVERNS / REQUIRES / PERFORMED_ON / CAUSED_BY / MEASURED_BY`. In-process stand-in for Neo4j |
| Hybrid retrieval (§4.4a) | `backend/retrieval.py` — TF-IDF (dense stand-in) + BM25 (OpenSearch stand-in) + graph proximity, fused with Reciprocal Rank Fusion |
| Multi-agent system (§6) | `backend/agents.py` — Supervisor → Planner → Predictive · RootCause · Knowledge · Maintenance · Safety (hard gate) → Risk → Critic |
| Chain-of-verification (§8) | Critic checks each claim against sources → calibrated confidence, grounded-or-refuse (< 0.7 escalates) |
| Predictive intelligence (§10) | `backend/predictive.py` — anomaly detection + RUL by trend extrapolation to the ISO 10816 danger limit |
| LLM gateway (§4.4d) | `backend/llm.py` — Claude (`claude-opus-4-8`) synthesizes the grounded answer when credentials exist; deterministic template otherwise, so the demo never breaks |
| UX (§12) | `frontend/index.html` — RUL countdown tile, vibration sparkline with alarm limits, agent chat with citations + confidence badge, "agents involved" trace, interactive knowledge graph that highlights the traversed subgraph |

**Deliberately cut (per §17 "cut ruthlessly"):** multi-tenancy, ABAC, Docker/Neo4j/Qdrant/OpenSearch/Kafka
(replaced by in-process equivalents with the same interfaces/semantics), streaming tokens, ColPali.

## Run it (setup — works from a fresh clone/zip)

```bash
cd axon
pip install -r requirements.txt          # 1. install dependencies

cp .env.example .env                     # 2. create your config
#    then edit .env and paste YOUR Hugging Face token:
#    HUGGINGFACEHUB_ACCESS_TOKEN=hf_xxxxxxxx

python backend/main.py                   # 3. serves http://127.0.0.1:8000
```

Open http://127.0.0.1:8000 and ask about the documents in `data/uploads/`
(or upload your own with **+ Add document**).

### If a friend runs this and answers come back as raw passages

That means the Hugging Face model isn't reachable with **their** token — the app
falls back to the deterministic engine. Fixes, in order:

1. Confirm their token is in `.env` (`HUGGINGFACEHUB_ACCESS_TOKEN=hf_...`, no quotes/spaces).
2. In `.env`, set `HF_MODEL` to a model their tier can serve. `meta-llama/Llama-3.1-8B-Instruct`
   works on most free tiers; large/reasoning models like `Qwen/Qwen3-32B` need a paid
   Inference-Providers tier. The header at the top of the page shows the active model.
3. Or set `ANTHROPIC_API_KEY` in `.env` to use Claude as the answer engine instead.

> **Sharing note:** `.env` holds your secret token — it's git-ignored and should
> **not** be included when you share the zip. Ship `.env.example`; each person
> adds their own token. Rotate your token if it was ever shared.

The app runs with **no credentials at all** — it just answers from retrieved
passages (deterministic engine) instead of composing prose.

## Demo script (the 5-minute story, §20)

1. Open http://127.0.0.1:8000 — the dashboard shows the red **RUL countdown** on P-101.
2. Ask: **"P-101 vibration is trending up — what's happening and what should I do?"**
3. Watch the *agents involved* trace populate and the **graph light up** around P-101.
4. Read the answer: cause (misalignment → bearing wear, matched to 3 past work orders),
   RUL, the **LOTO hard gate** (SAF-12), drafted work order, spares in stock — every
   claim cited, with a confidence badge from the Critic.
5. Ask "What torque do the bearing housing bolts on P-101 need?" — exact-term (BM25)
   retrieval finds 45 N·m with the SOP-207 citation.

## Corpus

- 4 SOPs + 1 vendor manual (markdown, `data/sops`, `data/manuals`)
- 1 P&ID topology (`data/pid/pid_area1.json`)
- CMMS maintenance log + spares (`data/*.csv`)
- 14 days of hourly P-101 telemetry with an accelerating bearing-wear trend
  (`data/sensors_p101.csv`)
