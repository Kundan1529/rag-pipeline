#!/usr/bin/env python
"""Offline evaluation harness for the AXON retrieval pipeline.

Runs the golden QA set (data/golden_qa.jsonl) through query understanding +
hybrid retrieval and reports:

    hit@k          — expected document present in the top-k evidence
    MRR            — 1 / rank of the first expected-document chunk
    term coverage  — expected terms present in the retrieved evidence text
    intent acc     — detected intent matches the labelled intent
    latency        — per-query retrieve() wall time

Results are written to data/eval_results.json. When a previous result file
exists it becomes the baseline: metric drops beyond --tolerance are reported
as REGRESSIONS and the process exits non-zero (CI-friendly). Use
--update-baseline to accept the new numbers.

Deterministic and LLM-free by design: it exercises Phases 1-3 (the layers
where quality regressions are silent) without provider cost. Pass
--no-rerank to skip the cross-encoder for a fast lexical-only run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from statistics import mean

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

METRICS = ("hit_rate", "mrr", "term_coverage", "intent_accuracy")


def _term_present(term: str, text: str) -> bool:
    return re.search(
        r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])",
        text.lower(),
    ) is not None


def run(golden_path: Path, k: int, no_rerank: bool) -> dict:
    if no_rerank:
        import retrieval
        retrieval.CrossEncoder = None

    from ingest import load_corpus
    from retrieval import HybridIndex
    from query_processor import QueryProcessor

    corpus = load_corpus()
    index = HybridIndex(corpus.chunks)
    qp = QueryProcessor()

    cases = [json.loads(line) for line in golden_path.read_text().splitlines()
             if line.strip()]

    rows = []
    for case in cases:
        plan = qp.process(case["query"])
        t0 = time.time()
        hits = index.retrieve(plan.retrieval_queries[0], query_plan=plan, k=k)
        latency_ms = round((time.time() - t0) * 1000, 1)

        expected_doc = case["expected_doc"].lower()
        rank = next(
            (i + 1 for i, h in enumerate(hits)
             if expected_doc in f"{h['doc_no']} {h['doc_title']}".lower()),
            None,
        )
        blob = " ".join(h["text"] for h in hits)
        terms = case.get("expected_terms", [])
        covered = [t for t in terms if _term_present(t, blob)]

        wanted_intent = case.get("intent")
        rows.append({
            "id": case["id"],
            "query": case["query"],
            "hit": rank is not None,
            "rank": rank,
            "rr": (1.0 / rank) if rank else 0.0,
            "term_coverage": len(covered) / len(terms) if terms else 1.0,
            "missing_terms": [t for t in terms if t not in covered],
            "intent_ok": (plan.intent == wanted_intent) if wanted_intent else None,
            "detected_intent": plan.intent,
            "latency_ms": latency_ms,
        })

    intent_rows = [r for r in rows if r["intent_ok"] is not None]
    summary = {
        "k": k,
        "reranker": not no_rerank,
        "cases": len(rows),
        "hit_rate": round(mean(r["hit"] for r in rows), 3),
        "mrr": round(mean(r["rr"] for r in rows), 3),
        "term_coverage": round(mean(r["term_coverage"] for r in rows), 3),
        "intent_accuracy": round(
            mean(r["intent_ok"] for r in intent_rows), 3,
        ) if intent_rows else None,
        "latency_ms_mean": round(mean(r["latency_ms"] for r in rows), 1),
        "latency_ms_max": max(r["latency_ms"] for r in rows),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--golden", type=Path,
                    default=BACKEND.parent / "data" / "golden_qa.jsonl")
    ap.add_argument("--results", type=Path,
                    default=BACKEND.parent / "data" / "eval_results.json")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="max allowed drop per metric before failing")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--no-rerank", action="store_true",
                    help="skip the cross-encoder (fast lexical-only run)")
    args = ap.parse_args()

    result = run(args.golden, args.k, args.no_rerank)
    s = result["summary"]

    print(f"\n{'id':<5} {'hit':<4} {'rank':<5} {'terms':<6} "
          f"{'intent':<12} {'ms':<7} query")
    for r in result["rows"]:
        print(f"{r['id']:<5} {str(r['hit']):<4} {str(r['rank']):<5} "
              f"{r['term_coverage']:<6.2f} {r['detected_intent']:<12} "
              f"{r['latency_ms']:<7} {r['query'][:48]}")
        if r["missing_terms"]:
            print(f"      missing terms: {r['missing_terms']}")

    print(f"\nSUMMARY  hit@{s['k']}={s['hit_rate']}  MRR={s['mrr']}  "
          f"terms={s['term_coverage']}  intent={s['intent_accuracy']}  "
          f"latency mean={s['latency_ms_mean']}ms max={s['latency_ms_max']}ms")

    # ------------------------------------------------- regression check
    exit_code = 0
    if args.results.exists() and not args.update_baseline:
        baseline = json.loads(args.results.read_text())["summary"]
        regressions = []
        for m in METRICS:
            old, new = baseline.get(m), s.get(m)
            if old is None or new is None:
                continue
            delta = round(new - old, 3)
            marker = ""
            if delta < -args.tolerance:
                marker = "  << REGRESSION"
                regressions.append(m)
            print(f"  {m:<16} baseline={old}  now={new}  Δ={delta:+}{marker}")
        if regressions:
            print(f"\nFAIL: regression in {regressions} "
                  f"(tolerance {args.tolerance}). Baseline NOT updated; "
                  f"pass --update-baseline to accept intentionally.")
            return 1
        print("\nOK: no regression vs baseline.")

    args.results.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Results written to {args.results}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
