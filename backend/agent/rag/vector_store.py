"""agent/rag/vector_store.py — Vector similarity search.

FaissVectorStore is the default and does real cosine-similarity search
(IndexFlatIP over L2-normalized vectors == cosine similarity) using
faiss-cpu, which installed cleanly via pip in this project's environment.
NumpyVectorStore is a brute-force fallback behind the identical interface,
used automatically if faiss isn't importable. At this corpus's scale
(dozens of chunks) brute-force cosine similarity IS what FAISS's
IndexFlatIP computes internally anyway — there's no approximation being
traded away, just a dependency swap — but the interface is what matters:
swap to IndexIVFFlat/HNSW inside FaissVectorStore without touching any
caller if the knowledge base grows to a scale where exact search gets slow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class SearchHit:
    doc_id: str
    score: float
    metadata: dict[str, Any]


class NumpyVectorStore:
    def __init__(self) -> None:
        self._ids: list[str] = []
        self._vectors: np.ndarray | None = None
        self._metadata: list[dict[str, Any]] = []

    def build(self, ids: list[str], vectors: np.ndarray, metadata: list[dict[str, Any]]) -> None:
        self._ids = ids
        self._vectors = vectors.astype(np.float32)
        self._metadata = metadata

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchHit]:
        if self._vectors is None or len(self._ids) == 0:
            return []
        sims = self._vectors @ query_vector.astype(np.float32)  # vectors already L2-normalized
        top_idx = np.argsort(-sims)[:top_k]
        return [SearchHit(doc_id=self._ids[i], score=float(sims[i]), metadata=self._metadata[i]) for i in top_idx]

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        np.save(path / "vectors.npy", self._vectors)
        (path / "meta.json").write_text(
            json.dumps({"ids": self._ids, "metadata": self._metadata}, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> "NumpyVectorStore":
        store = cls()
        store._vectors = np.load(path / "vectors.npy")
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        store._ids = meta["ids"]
        store._metadata = meta["metadata"]
        return store


class FaissVectorStore:
    def __init__(self) -> None:
        self._ids: list[str] = []
        self._metadata: list[dict[str, Any]] = []
        self._index = None
        self._dim = 0

    def build(self, ids: list[str], vectors: np.ndarray, metadata: list[dict[str, Any]]) -> None:
        import faiss

        vectors = np.ascontiguousarray(vectors.astype(np.float32))
        self._dim = vectors.shape[1]
        self._index = faiss.IndexFlatIP(self._dim)  # inner product on L2-normalized vecs = cosine sim
        self._index.add(vectors)
        self._ids = ids
        self._metadata = metadata

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchHit]:
        if self._index is None or self._index.ntotal == 0:
            return []
        query = np.ascontiguousarray(query_vector.astype(np.float32).reshape(1, -1))
        scores, idx = self._index.search(query, min(top_k, self._index.ntotal))
        hits = []
        for score, i in zip(scores[0], idx[0]):
            if i < 0:
                continue
            hits.append(SearchHit(doc_id=self._ids[i], score=float(score), metadata=self._metadata[i]))
        return hits

    def save(self, path: Path) -> None:
        import faiss

        path.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(path / "index.faiss"))
        (path / "meta.json").write_text(
            json.dumps({"ids": self._ids, "metadata": self._metadata, "dim": self._dim}, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "FaissVectorStore":
        import faiss

        store = cls()
        store._index = faiss.read_index(str(path / "index.faiss"))
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        store._ids = meta["ids"]
        store._metadata = meta["metadata"]
        store._dim = meta["dim"]
        return store


def build_vector_store():
    try:
        import faiss  # noqa: F401

        return FaissVectorStore()
    except ImportError:
        return NumpyVectorStore()


def load_vector_store(path: Path):
    if (path / "index.faiss").exists():
        return FaissVectorStore.load(path)
    if (path / "vectors.npy").exists():
        return NumpyVectorStore.load(path)
    raise FileNotFoundError(f"no vector store found at {path}")
