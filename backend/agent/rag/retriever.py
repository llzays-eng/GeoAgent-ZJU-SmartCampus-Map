"""agent/rag/retriever.py — Query-time retrieval.

`retrieve(query, top_k)` is the only function the Orchestrator calls (in its
RAG_RETRIEVAL state). It lazily builds the index on first call if
agent/rag/index/ doesn't exist yet, so a fresh checkout works without a
manual build step, then reuses the loaded index + embedder for the rest of
the process's lifetime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent.config import get_settings
from agent.rag.build_index import INDEX_DIR, build_index
from agent.rag.embedding import Embedder, TfidfEmbedder
from agent.rag.vector_store import load_vector_store


@dataclass
class RetrievedChunk:
    doc_id: str
    doc_title: str
    heading: str
    text: str
    score: float


class Retriever:
    def __init__(self) -> None:
        self._store = None
        self._embedder: Embedder | None = None
        self._load_or_build()

    def _load_or_build(self) -> None:
        settings = get_settings()
        backend_file = INDEX_DIR / "embedding_backend.txt"
        # `persisted_backend` is the backend the on-disk index was ACTUALLY
        # built with (see build_index.py, which now records
        # embedder.backend_name rather than the merely-configured
        # settings.embedding_backend) — comparing against that, not the
        # declared setting, is what makes the fast-load branch below safe.
        persisted_backend = backend_file.read_text(encoding="utf-8").strip() if backend_file.exists() else None
        stale = persisted_backend is not None and persisted_backend != settings.embedding_backend

        if INDEX_DIR.exists() and not stale:
            try:
                self._store = load_vector_store(INDEX_DIR)
                if persisted_backend == "tfidf":
                    data = json.loads((INDEX_DIR / "tfidf_embedder.json").read_text(encoding="utf-8"))
                    self._embedder = TfidfEmbedder.from_dict(data)
                else:
                    from agent.rag.embedding import build_embedder

                    embedder = build_embedder(settings.embedding_backend)
                    if isinstance(embedder, TfidfEmbedder):
                        # build_embedder() just silently fell back (e.g.
                        # EMBEDDING_BACKEND=bge but sentence-transformers
                        # isn't installed). This freshly-constructed
                        # TfidfEmbedder has never been fit(), and it is NOT
                        # a valid stand-in for the persisted store above,
                        # which was built with the real `persisted_backend`
                        # (different vocabulary/dimensionality). Treat this
                        # exactly like a stale/missing index — fall through
                        # to a full rebuild below, which will itself fall
                        # back to tfidf *and* fit() it against the current
                        # corpus, so retrieval degrades gracefully instead
                        # of crashing on "fit() must be called before
                        # embed()".
                        raise RuntimeError("embedder backend fell back to tfidf at load time")
                    self._embedder = embedder
                return
            except (FileNotFoundError, KeyError, json.JSONDecodeError, RuntimeError):
                pass  # fall through to a fresh build below

        self._store, self._embedder, _ = build_index(persist=True)

    def retrieve(self, query: str, top_k: int = 3, min_score: float = 0.05) -> list[RetrievedChunk]:
        assert self._embedder is not None
        query_vec = self._embedder.embed([query])[0]
        hits = self._store.search(query_vec, top_k=top_k)
        return [
            RetrievedChunk(
                doc_id=h.metadata["doc_id"],
                doc_title=h.metadata["doc_title"],
                heading=h.metadata["heading"],
                text=h.metadata["text"],
                score=h.score,
            )
            for h in hits
            if h.score >= min_score
        ]


_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


def retrieve(query: str, top_k: int = 3) -> list[RetrievedChunk]:
    return get_retriever().retrieve(query, top_k=top_k)
