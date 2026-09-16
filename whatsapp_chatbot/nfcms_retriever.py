"""
Dedicated retriever for NFCMS 2021 data only.
Use nfcms_ensemble_retriever for queries against the NFCMS Chroma store.
"""

import os
import logging
from dotenv import load_dotenv
from config import QWEN_EMBED_URL

logger = logging.getLogger(__name__)
load_dotenv()

try:
    from langchain_classic.retrievers.ensemble import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document
    from langchain_chroma import Chroma
    from langchain_openai import OpenAIEmbeddings
    from nltk.tokenize import word_tokenize
    import nltk
    nltk.download("punkt_tab", quiet=True)
    RETRIEVER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ NFCMS retriever dependencies not available: {e}")
    RETRIEVER_AVAILABLE = False

CHROMA_PATH     = "nfcms_chroma_store"
COLLECTION_NAME = "NFCMS_2021"
K_DOCS          = 3

nfcms_ensemble_retriever = None

if RETRIEVER_AVAILABLE:
    try:
        if not os.path.exists(CHROMA_PATH):
            logger.warning(f"📁 NFCMS ChromaDB not found at '{CHROMA_PATH}'")
            logger.warning("   Run embed_nfcms_only.py from the Chop-beta root to create it")
        else:
            embedding = OpenAIEmbeddings(
                base_url=QWEN_EMBED_URL,
                openai_api_key=os.getenv("QWEN_EMBED_API_KEY", ""),
                model="Qwen/Qwen3-Embedding-0.6B",
                check_embedding_ctx_length=False
            )

            nfcms_chroma = Chroma(
                collection_name=COLLECTION_NAME,
                embedding_function=embedding,
                persist_directory=CHROMA_PATH
            )

            stored = nfcms_chroma.get(include=["documents", "metadatas"])
            nfcms_docs = [
                Document(page_content=doc, metadata=meta)
                for doc, meta in zip(stored["documents"], stored["metadatas"])
            ]

            logger.info(f"📚 Loaded {len(nfcms_docs)} NFCMS documents")

            semantic_retriever = nfcms_chroma.as_retriever(search_kwargs={"k": K_DOCS})
            bm25_retriever = BM25Retriever.from_documents(
                nfcms_docs,
                preprocess_func=word_tokenize,
                k=K_DOCS
            )

            nfcms_ensemble_retriever = EnsembleRetriever(
                retrievers=[semantic_retriever, bm25_retriever],
                weights=[0.6, 0.4]
            )

            logger.info("✅ NFCMS ensemble retriever ready (semantic + BM25)")

    except Exception as e:
        logger.error(f"❌ Failed to initialize NFCMS retriever: {e}")
        nfcms_ensemble_retriever = None

if nfcms_ensemble_retriever is None:
    class _DummyRetriever:
        def invoke(self, query: str):
            logger.debug(f"🔍 NFCMS query: {query} (retrieval disabled)")
            return []
    nfcms_ensemble_retriever = _DummyRetriever()
    logger.warning("⚠️ Using dummy NFCMS retriever — run embed_nfcms_only.py to enable it")
