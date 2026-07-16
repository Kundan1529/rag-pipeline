"""AXON MVP server — FastAPI app serving the API and the single-page UI."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import re

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import llm
import predictive
from agents import AgentSystem
from conversations import ConversationStore
from ingest import (DATA_DIR, UPLOADS_DIR, load_corpus,
                    supported_upload_extensions)
from kg import build_graph
from knowledge_gaps import GapStore, load_experts, suggest_sme
from retrieval import HybridIndex

app = FastAPI(title="AXON — Industrial Knowledge OS (MVP)")

# Local demo: allow the UI to call the API from file:// or another origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

corpus = load_corpus()
graph = build_graph(corpus)
index = HybridIndex(corpus.chunks)
system = AgentSystem(corpus, graph, index)
gaps = GapStore(DATA_DIR / "knowledge_gaps.json")
experts = load_experts(DATA_DIR / "experts.csv")
conversations = ConversationStore(DATA_DIR / "conversations.json")


def _reingest():
    """Rebuild corpus, graph, index and agents after an upload."""
    global corpus, graph, index, system
    corpus = load_corpus()
    graph = build_graph(corpus)
    index = HybridIndex(corpus.chunks)
    system = AgentSystem(corpus, graph, index)


def _norm_doc_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower().replace(".pdf", ""))


def _resolve_document(doc_no: str) -> tuple[str, dict] | tuple[None, None]:
    """Resolve a document from API path input or UI display identifiers."""
    meta = corpus.docs.get(doc_no)
    if meta is not None:
        return doc_no, meta
    wanted = _norm_doc_id(doc_no)
    for candidate, candidate_meta in corpus.docs.items():
        aliases = {
            candidate,
            candidate_meta.get("title", ""),
            candidate_meta.get("source_file", ""),
            Path(candidate_meta.get("source_file", "")).stem,
        }
        if any(_norm_doc_id(alias) == wanted for alias in aliases if alias):
            return candidate, candidate_meta
    return None, None


FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class AskRequest(BaseModel):
    query: str
    history: list = []
    conversation_id: Optional[str] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


@app.get("/")
def home():
    return FileResponse(FRONTEND / "index.html")


@app.get("/app.js")
def frontend_js():
    return FileResponse(FRONTEND / "app.js", media_type="application/javascript")


@app.get("/api/status")
def status():
    concepts = sum(1 for n in graph.nodes.values() if n.get("type") == "Concept")
    entities = sum(1 for c in corpus.chunks for _ in c.entities)
    return {
        "documents": len(corpus.docs),
        "chunks": len(corpus.chunks),
        "graph_nodes": len(graph.nodes),
        "graph_edges": len(graph.edges),
        "concepts": concepts,
        "relations": len(graph.edges),
        "entities": entities,
        "sensor_points": len(corpus.sensors),
        "llm": llm.MODEL if llm.llm_available() else "deterministic fallback (no credentials)",
    }


@app.get("/api/graph")
def get_graph():
    """Full graph with PageRank scores (for node sizing) and degree counts."""
    ranks = graph.rank_nodes()
    max_rank = max(ranks.values()) if ranks else 1.0
    nodes = []
    for n in graph.nodes.values():
        node = dict(n)
        node["rank"] = round(ranks.get(n["id"], 0.0) / max_rank, 4)
        node["degree"] = len(graph.neighbors(n["id"]))
        nodes.append(node)
    return {"nodes": nodes, "edges": graph.edges}


@app.get("/api/graph/path")
def graph_path(source: str, target: str):
    """Shortest relationship path between two nodes (graph reasoning)."""
    path = graph.shortest_path(source, target)
    return {"source": source, "target": target,
            "found": bool(path), "path": path}


@app.get("/api/graph/neighborhood/{node_id}")
def graph_neighborhood(node_id: str, hops: int = 2):
    """Relevance-weighted neighborhood around a node (for the details drawer)."""
    if node_id not in graph.nodes:
        raise HTTPException(404, f"No such node: {node_id}")
    scores, edges = graph.weighted_expand({node_id}, hops=hops)
    nodes = [dict(graph.nodes[n], relevance=round(s, 3))
             for n, s in scores.items()]
    nodes.sort(key=lambda n: -n["relevance"])
    return {"anchor": node_id, "nodes": nodes, "edges": edges}


@app.get("/api/telemetry")
def telemetry():
    return {
        "series": corpus.sensors[-14 * 24:],
        "prediction": predictive.analyze(corpus.sensors),
    }


@app.get("/api/pid")
def pid():
    return corpus.pid


@app.get("/api/documents")
def documents():
    """List every ingested document with its chunk/concept counts. Uploaded
    documents are deletable; seeded plant documents are not."""
    chunk_counts: dict = {}
    for c in corpus.chunks:
        chunk_counts[c.doc_no] = chunk_counts.get(c.doc_no, 0) + 1
    concept_counts: dict = {}
    for n in graph.nodes.values():
        if n.get("type") == "Concept" and n.get("source_doc"):
            concept_counts[n["source_doc"]] = concept_counts.get(n["source_doc"], 0) + 1
    docs = []
    for doc_no, meta in corpus.docs.items():
        docs.append({
            "doc_no": doc_no,
            "title": meta.get("title", doc_no),
            "type": meta.get("type", "Document"),
            "chunks": chunk_counts.get(doc_no, 0),
            "concepts": concept_counts.get(doc_no, 0),
            "uploaded": bool(meta.get("uploaded")),
        })
    docs.sort(key=lambda d: (not d["uploaded"], d["doc_no"]))  # uploaded first
    return {"count": len(docs),
            "uploaded": sum(1 for d in docs if d["uploaded"]),
            "documents": docs}


@app.delete("/api/documents/{doc_no}")
def delete_document(doc_no: str):
    """Delete an uploaded document: remove its file and rebuild the graph so its
    nodes/edges disappear. Seeded plant documents cannot be deleted."""
    resolved_doc_no, meta = _resolve_document(doc_no)
    if meta is None:
        raise HTTPException(404, f"No such document: {doc_no}")
    if not meta.get("uploaded") or not meta.get("source_file"):
        raise HTTPException(403, "Seeded plant documents cannot be deleted")
    target = (UPLOADS_DIR / Path(meta["source_file"]).name)
    if target.exists():
        target.unlink()
    _reingest()
    return {"ok": True, "deleted": resolved_doc_no,
            "documents": len(corpus.docs),
            "graph_nodes": len(graph.nodes), "graph_edges": len(graph.edges)}


@app.post("/api/reset")
def reset_chat():
    """Start a new chat: clear the server-side conversation context."""
    system.context.clear()
    return {"ok": True}


@app.post("/api/ask")
def ask(req: AskRequest):
    # Persistent conversation memory: when a conversation_id is supplied the
    # server owns the history; otherwise fall back to the client-sent history
    # (frozen legacy contract).
    conv = None
    history = req.history
    if req.conversation_id:
        conv = conversations.get(req.conversation_id)
        if conv is None:
            conv = conversations.create()
        history = conversations.history(conv["id"])
    result = system.run_case(req.query, history)
    if conv is not None:
        conversations.append(conv["id"], "user", req.query)
        conversations.append(
            conv["id"], "assistant", result.get("answer", ""),
            meta={"entities": [c.get("doc_no", "")
                               for c in result.get("citations", [])],
                  "confidence": result.get("confidence")})
        result["conversation_id"] = conv["id"]
        result["conversation_title"] = conversations.get(conv["id"])["title"]
    # Knowledge-gap detection: capture questions AXON couldn't answer well.
    verdict = result.get("verdict", "")
    if verdict.startswith("REFUSE"):
        gap = gaps.record(req.query, "No grounded evidence in the knowledge base",
                          result.get("confidence", 0.0), "HIGH",
                          suggest_sme(req.query, experts))
        result["knowledge_gap"] = gap
    elif verdict.startswith("ESCALATE"):
        gap = gaps.record(req.query, "Answer could not be fully verified against sources",
                          result.get("confidence", 0.0), "MEDIUM",
                          suggest_sme(req.query, experts))
        result["knowledge_gap"] = gap
    result["knowledge_gaps_unresolved"] = gaps.unresolved_count()
    return result


@app.post("/api/compare")
def compare(req: AskRequest):
    """Side-by-side: Vanilla RAG vs AXON GraphRAG on the same query."""
    return system.compare(req.query)


# -------------------------------------------------- conversation management

@app.get("/api/conversations")
def list_conversations(q: str = ""):
    """List (optionally search) conversations — pinned first, most recent next."""
    return {"conversations": conversations.list(q)}


@app.post("/api/conversations")
def create_conversation():
    return conversations.create()


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    conv = conversations.get(cid)
    if conv is None:
        raise HTTPException(404, f"No such conversation: {cid}")
    return conv


@app.patch("/api/conversations/{cid}")
def update_conversation(cid: str, req: ConversationUpdate):
    conv = None
    if req.title is not None:
        conv = conversations.rename(cid, req.title)
    if req.pinned is not None:
        conv = conversations.set_pinned(cid, req.pinned)
    if conv is None:
        raise HTTPException(404, f"No such conversation: {cid}")
    return conv


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str):
    if not conversations.delete(cid):
        raise HTTPException(404, f"No such conversation: {cid}")
    return {"ok": True, "deleted": cid}


@app.post("/api/conversations/{cid}/documents/{doc_no}")
def link_conversation_document(cid: str, doc_no: str):
    conv = conversations.link_document(cid, doc_no)
    if conv is None:
        raise HTTPException(404, f"No such conversation: {cid}")
    return {"ok": True, "documents": conv["documents"]}


@app.get("/api/knowledge-gaps")
def knowledge_gaps():
    items = gaps.list()
    return {"unresolved": len(items), "gaps": items}


class GapUpdate(BaseModel):
    owner: Optional[str] = None
    status: Optional[str] = None


@app.post("/api/knowledge-gaps/{gap_id}/update")
def update_gap(gap_id: str, req: GapUpdate):
    g = None
    if req.owner is not None:
        g = gaps.assign(gap_id, req.owner)
    if req.status is not None:
        g = gaps.set_status(gap_id, req.status)
    if g is None:
        raise HTTPException(404, f"No such gap (or invalid status): {gap_id}")
    return {"ok": True, "gap": g, "unresolved": gaps.unresolved_count()}


@app.get("/api/experts")
def list_experts():
    return {"experts": [{"name": e["name"], "role": e["role"], "area": e.get("area", "")}
                        for e in experts]}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "upload.pdf").name  # strip any path components
    allowed = supported_upload_extensions()
    if not name.lower().endswith(allowed):
        raise HTTPException(
            400, f"Unsupported file type. Supported: {', '.join(allowed)}")
    dest = UPLOADS_DIR / name
    UPLOADS_DIR.mkdir(exist_ok=True)
    dest.write_bytes(await file.read())
    try:
        _reingest()
    except Exception as e:
        dest.unlink(missing_ok=True)
        _reingest()
        raise HTTPException(422, f"Could not parse {name}: {e}")
    doc = next((d for d, m in corpus.docs.items() if m.get("title") == name or d == Path(name).stem), None)
    n_chunks = sum(1 for c in corpus.chunks if c.doc_title == name or c.doc_no == (doc or ""))
    concepts = [n for n, d in graph.nodes.items()
                if d.get("type") == "Concept" and d.get("source_doc") == doc]
    return {"ok": True, "doc_no": doc, "chunks_indexed": n_chunks,
            "graph_nodes": len(graph.nodes), "graph_edges": len(graph.edges),
            "concepts": concepts,
            "linked_assets": [e["source"] for e in graph.edges
                              if e["rel"] == "DOCUMENTED_BY" and e["target"] == doc]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
