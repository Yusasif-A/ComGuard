"""
Nutrition Data Retriever for Beta Food
Uses ensemble retriever (semantic + BM25) for better results
"""

import os
import logging
from dotenv import load_dotenv

from config import QWEN_EMBED_URL

logger = logging.getLogger(__name__)
load_dotenv()

# Try to import dependencies
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
    logger.warning(f"⚠️ Retriever dependencies not available: {e}")
    RETRIEVER_AVAILABLE = False


# Configuration
CHROMA_PATH = "nfcms_chroma_store"
COLLECTION_NAME = "NFCMS_2021"
K_DOCS = 2

# Initialize retriever
ensemble_retriever = None

if RETRIEVER_AVAILABLE:
    try:
        # Check if ChromaDB exists
        if not os.path.exists(CHROMA_PATH):
            logger.warning(f"📁 ChromaDB not found: {CHROMA_PATH}")
            logger.warning(f"   Run embed_nutrition.py to create it")
            logger.warning(f"   Agent will work without retrieval")
        else:
            # Load embedding model (same as embed_nutrition.py)
            embedding = OpenAIEmbeddings(
                base_url=QWEN_EMBED_URL,
                openai_api_key=os.getenv("QWEN_EMBED_API_KEY", ""),
                model="Qwen/Qwen3-Embedding-0.6B",
                check_embedding_ctx_length=False
            )

            # Load Chroma store
            nutrition_chroma = Chroma(
                collection_name=COLLECTION_NAME,
                embedding_function=embedding,
                persist_directory=CHROMA_PATH
            )

            # Get all documents for BM25
            stored_docs = nutrition_chroma.get(include=['documents', 'metadatas'])
            nutrition_documents = [
                Document(page_content=doc, metadata=meta)
                for doc, meta in zip(stored_docs['documents'], stored_docs['metadatas'])
            ]

            logger.info(f"📚 Loaded {len(nutrition_documents)} nutrition documents")

            # Create semantic retriever
            semantic_retriever = nutrition_chroma.as_retriever(search_kwargs={"k": K_DOCS})

            # Create BM25 retriever
            bm25_retriever = BM25Retriever.from_documents(
                nutrition_documents,
                preprocess_func=word_tokenize,
                k=K_DOCS
            )

            # Create ensemble retriever (60% semantic, 40% keyword)
            ensemble_retriever = EnsembleRetriever(
                retrievers=[semantic_retriever, bm25_retriever],
                weights=[0.6, 0.4]
            )

            logger.info("✅ Nutrition ensemble retriever ready (semantic + BM25)")

    except Exception as e:
        logger.error(f"❌ Failed to initialize retriever: {e}")
        logger.warning("   Agent will work without retrieval")
        ensemble_retriever = None

# Fallback dummy retriever
if ensemble_retriever is None:
    class DummyRetriever:
        def invoke(self, query: str):
            logger.debug(f"🔍 Query: {query} (retrieval disabled)")
            return []

    ensemble_retriever = DummyRetriever()
    logger.warning("⚠️ Using dummy retriever - agent will work without retrieval")
3