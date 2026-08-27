"""agent/rag/embedding.py — Pluggable text embedding for the RAG pipeline.

Two backends behind one interface:

  TfidfEmbedder  — pure numpy + jieba, no network, no model download. This
      is what actually builds and serves the index in this repo's tests and
      in any offline/sandboxed environment. TF-IDF with jieba word
      segmentation is a completely legitimate lightweight retrieval method
      for a ~8-document, ~30-chunk knowledge base — not a placeholder.

  BgeEmbedder    — real sentence-transformers `BAAI/bge-small-zh-v1.5`
      (dense, higher recall quality, especially for paraphrase / semantic
      matches TF-IDF's lexical overlap misses). Requires
      `pip install sentence-transformers` and internet access to download
      model weights from Hugging Face on first use — neither of which was
      available in the sandbox this project was built in, so this backend
      is written for real use but was NOT exercised end-to-end here. Set
      EMBEDDING_BACKEND=bge once you have both.

`get_embedder()` picks one based on agent.config.Settings.embedding_backend
and *always* falls back to TfidfEmbedder if the bge backend can't actually
be constructed (missing package, no network) — so a misconfigured
.env never hard-crashes RAG retrieval.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

import jieba
import numpy as np

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    """jieba word segmentation for CJK text, plus a fallback regex pass so
    embedded Latin words/numbers (e.g. "GEE", "0.5") aren't dropped."""
    tokens = [t for t in jieba.cut(text) if t.strip()]
    # jieba already handles most of this well; the regex pass below only
    # matters for punctuation-adjacent tokens jieba sometimes keeps attached.
    cleaned = []
    for tok in tokens:
        cleaned.extend(_TOKEN_RE.findall(tok)) if not _TOKEN_RE.fullmatch(tok) else cleaned.append(tok)
    return [t.lower() for t in cleaned if t.strip()]


class Embedder(Protocol):
    dim: int
    backend_name: str  # the backend actually in use — see TfidfEmbedder/BgeEmbedder

    def embed(self, texts: list[str]) -> np.ndarray: ...


class TfidfEmbedder:
    """Fit-once TF-IDF vectorizer. `fit()` at index-build time establishes
    the vocabulary + IDF weights; `embed()` at query time reuses them
    (a query is just transformed into the same vector space, never refit).
    """

    backend_name = "tfidf"  # the backend a caller ACTUALLY gets, regardless
    # of what settings.embedding_backend was configured to — see
    # build_embedder()'s fallback and build_index.py's marker-file write.

    def __init__(self) -> None:
        self.vocabulary: dict[str, int] = {}
        self.idf: np.ndarray | None = None
        self.dim: int = 0

    def fit(self, corpus: list[str]) -> None:
        doc_token_sets = [set(_tokenize(doc)) for doc in corpus]
        df: Counter[str] = Counter()
        for tokens in doc_token_sets:
            df.update(tokens)

        # Keep every token that appears in >=1 doc — corpus is tiny (a few
        # dozen chunks), so there's no need to prune a huge vocabulary.
        self.vocabulary = {tok: i for i, tok in enumerate(sorted(df.keys()))}
        self.dim = len(self.vocabulary)

        n_docs = len(corpus)
        idf = np.zeros(self.dim, dtype=np.float32)
        for tok, idx in self.vocabulary.items():
            # smoothed idf, standard formulation: idf = ln((1+N)/(1+df)) + 1
            idf[idx] = math.log((1 + n_docs) / (1 + df[tok])) + 1.0
        self.idf = idf

    def _vectorize_one(self, text: str) -> np.ndarray:
        assert self.idf is not None, "TfidfEmbedder.fit() must be called before embed()"
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = _tokenize(text)
        if not tokens:
            return vec
        counts = Counter(tokens)
        max_count = max(counts.values())
        for tok, count in counts.items():
            idx = self.vocabulary.get(tok)
            if idx is None:
                continue  # out-of-vocabulary at query time — ignored, standard TF-IDF behaviour
            tf = 0.5 + 0.5 * (count / max_count)  # augmented term frequency, dampens long-doc bias
            vec[idx] = tf * self.idf[idx]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vectorize_one(t) for t in texts])

    def to_dict(self) -> dict:
        return {"vocabulary": self.vocabulary, "idf": self.idf.tolist() if self.idf is not None else None, "dim": self.dim}

    @classmethod
    def from_dict(cls, data: dict) -> "TfidfEmbedder":
        obj = cls()
        obj.vocabulary = data["vocabulary"]
        obj.idf = np.array(data["idf"], dtype=np.float32) if data["idf"] is not None else None
        obj.dim = data["dim"]
        return obj


class BgeEmbedder:
    """Real sentence-transformers backend — see module docstring for why
    this couldn't be exercised in the build/test sandbox."""

    backend_name = "bge"

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        from sentence_transformers import SentenceTransformer  # optional dep

        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)


def build_embedder(backend: str) -> Embedder:
    if backend == "bge":
        try:
            return BgeEmbedder()
        except Exception:
            pass  # fall through to tfidf — see module docstring
    return TfidfEmbedder()
