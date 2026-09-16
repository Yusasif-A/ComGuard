"""
Retrieval over the ComGuard corpus.

The corpus is the indexed official record: municipal tax codes, state gazettes,
penal law, and emergency agency (SEMA) bulletins. It is what lets the assistant
answer "is this levy real, and how much is it actually supposed to be?" with the
rule rather than with an opinion.

Semantic search alone is weak here. Somebody quoting a notice gives an exact
string — a section number, an agency name, an amount — and dense vectors are bad
at exact tokens, while BM25 is good at them. The ensemble runs both, weighted
towards semantic for phrasing and towards keyword for those literals.

Build the store with:  python ingest_corpus.py ./corpus
Until it exists, the retriever returns nothing and the assistant falls back to
saying it could not confirm the official position — which is the correct
behaviour, and much better than inventing a statute.
"""

import logging
import os

from config import (
    CHROMA_COLLECTION,
    CHROMA_PATH,
    EMBED_MODEL,
    QWEN_EMBED_API_KEY,
    QWEN_EMBED_URL,
)

logger = logging.getLogger(__name__)

K_DOCS = 3

try:
    import nltk
    from langchain_chroma import Chroma
    from langchain_classic.retrievers.ensemble import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document
    from langchain_openai import OpenAIEmbeddings
    from nltk.tokenize import word_tokenize

    nltk.download("punkt_tab", quiet=True)
    RETRIEVER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Retriever dependencies unavailable: {e}")
    RETRIEVER_AVAILABLE = False


def build_embeddings():
    """Embedding client, shared with ingest_corpus.py.

    Queries and documents MUST be embedded by the same model, so this lives here
    and the ingest script imports it rather than configuring its own.
    """
    return OpenAIEmbeddings(
        base_url=QWEN_EMBED_URL,
        openai_api_key=QWEN_EMBED_API_KEY,
        model=EMBED_MODEL,
        check_embedding_ctx_length=False,
    )


class _EmptyRetriever:
    """Stand-in when the corpus is missing, so callers need no None checks."""

    def invoke(self, query: str):
        logger.debug(f"🔍 '{query}' — corpus not indexed, returning nothing")
        return []


def _build_retriever():
    if not RETRIEVER_AVAILABLE:
        return _EmptyRetriever()

    if not os.path.exists(CHROMA_PATH):
        logger.warning(
            f"📁 Corpus not found at '{CHROMA_PATH}' — run "
            f"`python ingest_corpus.py <folder>` to index the gazettes, tax "
            f"codes and SEMA bulletins. The assistant runs without it, but "
            f"cannot cite official sources."
        )
        return _EmptyRetriever()

    try:
        chroma = Chroma(
            collection_name=CHROMA_COLLECTION,
            embedding_function=build_embeddings(),
            persist_directory=CHROMA_PATH,
        )

        stored = chroma.get(include=["documents", "metadatas"])
        documents = [
            Document(page_content=text, metadata=meta)
            for text, meta in zip(stored["documents"], stored["metadatas"])
        ]

        if not documents:
            logger.warning("📁 Corpus store exists but is empty — re-run ingest_corpus.py")
            return _EmptyRetriever()

        logger.info(f"📚 Loaded {len(documents)} documents from the ComGuard corpus")

        semantic = chroma.as_retriever(search_kwargs={"k": K_DOCS})
        keyword = BM25Retriever.from_documents(
            documents, preprocess_func=word_tokenize, k=K_DOCS
        )

        retriever = EnsembleRetriever(retrievers=[semantic, keyword], weights=[0.6, 0.4])
        logger.info("✅ ComGuard retriever ready (semantic + BM25)")
        return retriever

    except Exception as e:
        logger.error(f"❌ Could not initialise retriever: {e}")
        return _EmptyRetriever()


ensemble_retriever = _build_retriever()


def format_sources(docs, limit: int = 2) -> str:
    """Render retrieved documents for the agent, each with its citation.

    Capped at two: the model has to write a short WhatsApp reply, and handing it
    five long statute extracts reliably produces a wall of quoted law instead of
    the plain answer the person needs.
    """
    if not docs:
        return ""

    blocks = []
    for doc in docs[:limit]:
        meta = doc.metadata or {}
        source = meta.get("source") or meta.get("title") or meta.get("file") or "official record"
        page = meta.get("page")
        citation = f"{source}, p.{page}" if page else source
        blocks.append(f"[Source: {citation}]\n{doc.page_content.strip()}")

    return "\n\n---\n\n".join(blocks)
