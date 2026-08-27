"""agent/rag/build_index.py — (Re)build the RAG vector index from
agent/rag/knowledge_base/*.md.

Run directly: `python -m agent.rag.build_index`
Also callable programmatically (retriever.py calls this automatically if no
index exists yet on first use, so a fresh checkout works with zero manual
setup steps).

Chunking strategy: split each doc on level-2 markdown headers ("## "),
keeping the H1 title as context on every chunk. For docs this short
(a few hundred to ~1000 characters per section) this gives noticeably more
precise retrieval than indexing whole documents — a query about "PCA主成分"
should surface the RSEI doc's PCA section specifically, not force a reader
through the whole document.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from agent.config import get_settings
from agent.rag.embedding import build_embedder
from agent.rag.vector_store import build_vector_store

KB_DIR = Path(__file__).resolve().parent / "knowledge_base"
INDEX_DIR = Path(__file__).resolve().parent / "index"


def _chunk_markdown(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    title_match = re.match(r"^#\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    sections = re.split(r"\n(?=##\s+)", text)
    chunks = []
    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue
        heading_match = re.match(r"^##\s+(.+)$", section, re.MULTILINE)
        heading = heading_match.group(1).strip() if heading_match else title
        body = section if not section.startswith("#") else "\n".join(section.splitlines()[1:]).strip()
        if not body:
            continue
        chunks.append(
            {
                "chunk_id": f"{path.stem}#{i}",
                "doc_id": path.stem,
                "doc_title": title,
                "heading": heading,
                "text": f"{title} — {heading}\n{body}",
            }
        )
    return chunks


def build_index(persist: bool = True) -> tuple:
    md_files = sorted(KB_DIR.glob("*.md"))
    if not md_files:
        raise RuntimeError(f"no knowledge base docs found in {KB_DIR}")

    all_chunks: list[dict] = []
    for path in md_files:
        all_chunks.extend(_chunk_markdown(path))

    settings = get_settings()
    embedder = build_embedder(settings.embedding_backend)

    corpus = [c["text"] for c in all_chunks]
    if hasattr(embedder, "fit"):
        embedder.fit(corpus)  # TF-IDF needs a fit pass; bge does not (pretrained)
    vectors = embedder.embed(corpus)

    store = build_vector_store()
    ids = [c["chunk_id"] for c in all_chunks]
    metadata = [{"doc_id": c["doc_id"], "doc_title": c["doc_title"], "heading": c["heading"], "text": c["text"]} for c in all_chunks]
    store.build(ids, vectors, metadata)

    if persist:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        store.save(INDEX_DIR)
        if hasattr(embedder, "to_dict"):
            import json

            (INDEX_DIR / "tfidf_embedder.json").write_text(
                json.dumps(embedder.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
        # Record the backend the embedder ACTUALLY ended up being (e.g.
        # "tfidf" after a silent bge -> tfidf fallback), not
        # settings.embedding_backend as configured/declared — otherwise a
        # restart sees the marker still say "bge", treats the cache as
        # fresh, and hands retriever.py a brand-new *unfit* TfidfEmbedder
        # (see rag/retriever.py's _load_or_build for the matching half of
        # this fix).
        actual_backend = getattr(embedder, "backend_name", settings.embedding_backend)
        (INDEX_DIR / "embedding_backend.txt").write_text(actual_backend, encoding="utf-8")

    return store, embedder, all_chunks


if __name__ == "__main__":
    store, embedder, chunks = build_index()
    print(f"Indexed {len(chunks)} chunks from {len(list(KB_DIR.glob('*.md')))} documents -> {INDEX_DIR}")
