"""
Build the ComGuard corpus.

    python ingest_corpus.py ./corpus

Walks a folder of official documents — municipal tax codes, state gazettes,
penal law, SEMA bulletins — splits them into passages and writes them to the
Chroma store the assistant reads at startup.

Chunking matters more than usual here. A levy amount and the authority allowed
to collect it are often one sentence apart, and a chunk boundary between them
turns a citable answer into a misleading half-answer. Hence large chunks with
generous overlap.

Put the source folder outside the repo, or in a gitignored path: some of these
documents are freely public, but the folder is a convenient place for someone to
drop a file that is not.
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ingest")

SUPPORTED = {".pdf", ".txt", ".md"}

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 250


def load_documents(folder: Path) -> list:
    from langchain_community.document_loaders import PyPDFLoader, TextLoader

    documents = []
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUPPORTED)

    if not files:
        logger.error(f"No {'/'.join(sorted(SUPPORTED))} files found under {folder}")
        return []

    for path in files:
        try:
            if path.suffix.lower() == ".pdf":
                loaded = PyPDFLoader(str(path)).load()
            else:
                loaded = TextLoader(str(path), encoding="utf-8").load()

            # The filename is the citation the assistant shows the user, so it
            # is worth naming files after the document, not "scan_003.pdf".
            for doc in loaded:
                doc.metadata.setdefault("source", path.stem.replace("_", " "))
                doc.metadata.setdefault("file", path.name)

            documents.extend(loaded)
            logger.info(f"📄 {path.name}: {len(loaded)} page(s)")
        except Exception as e:
            logger.error(f"❌ Could not read {path.name}: {e}")

    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description="Index official documents for ComGuard")
    parser.add_argument("folder", help="Folder containing PDF/TXT/MD source documents")
    parser.add_argument("--reset", action="store_true",
                        help="Delete the existing store before indexing")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        logger.error(f"Not a folder: {folder}")
        return 1

    from langchain_chroma import Chroma
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from config import CHROMA_COLLECTION, CHROMA_PATH
    from retriever import build_embeddings

    documents = load_documents(folder)
    if not documents:
        return 1

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        # Prefer breaking at a blank line, then a line, then a sentence — so a
        # chunk boundary lands between clauses rather than inside an amount.
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    logger.info(f"✂️ {len(documents)} document(s) → {len(chunks)} chunks")

    if args.reset:
        import shutil
        store = Path(CHROMA_PATH)
        if store.exists():
            shutil.rmtree(store)
            logger.info(f"🗑️ Removed existing store at {store}")

    store = Chroma(
        collection_name=CHROMA_COLLECTION,
        embedding_function=build_embeddings(),
        persist_directory=CHROMA_PATH,
    )

    # Batched because embedding endpoints reject very large single requests, and
    # a failure part-way leaves the earlier batches already committed.
    batch = 64
    for i in range(0, len(chunks), batch):
        store.add_documents(chunks[i:i + batch])
        logger.info(f"   indexed {min(i + batch, len(chunks))}/{len(chunks)}")

    logger.info(f"✅ Corpus written to {CHROMA_PATH} (collection: {CHROMA_COLLECTION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
