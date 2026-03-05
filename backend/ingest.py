"""Code chunking utility using LangChain's RecursiveCharacterTextSplitter.

Given a file path, this module chooses a language-aware splitter (when
supported by LangChain) and returns chunks of code for downstream RAG / Chroma.
"""

from __future__ import annotations

from pathlib import Path
from typing import List
import asyncio
import logging

try:
    # Newer LangChain splitters package
    from langchain_text_splitters import (
        RecursiveCharacterTextSplitter,
        Language,
    )
except ImportError:  # pragma: no cover - fallback for older LangChain versions
    from langchain.text_splitter import (  # type: ignore[no-redef]
        RecursiveCharacterTextSplitter,
        Language,
    )


CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

_EXTENSION_LANGUAGE_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JS,
    ".ts": Language.JS,
    ".tsx": Language.JS,
}


def _get_splitter_for_path(path: Path) -> RecursiveCharacterTextSplitter:
    """Return a RecursiveCharacterTextSplitter configured for this file type.

    - For known code extensions (py, js, ts, tsx), use the language-aware
      splitter via `from_language`.
    - For everything else, fall back to a default character splitter with the
      same chunk size and overlap.
    """
    lang = _EXTENSION_LANGUAGE_MAP.get(path.suffix.lower())
    if lang is not None:
        return RecursiveCharacterTextSplitter.from_language(
            language=lang,
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )

    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )


def chunk_file(path_str: str) -> List[str]:
    """Read a code file and return text chunks."""
    path = Path(path_str)
    text = path.read_text(encoding="utf-8")
    splitter = _get_splitter_for_path(path)
    return splitter.split_text(text)


def chunk_text_for_extension(text: str, extension: str) -> List[str]:
    """Chunk an in-memory string, given a file extension (e.g., '.py', '.ts')."""
    dummy_path = Path("dummy").with_suffix(extension.lower())
    splitter = _get_splitter_for_path(dummy_path)
    return splitter.split_text(text)


def batch_upload(
    collection,
    documents: List[str],
    metadatas: List[dict],
    ids: List[str],
    batch_size: int = 100,
) -> None:
    """Upload chunks to ChromaDB in manageable batches.

    Example:
        batch_upload(collection, all_texts, all_metadata, all_ids)
    """
    total_chunks = len(documents)

    for i in range(0, total_chunks, batch_size):
        batch_docs = documents[i : i + batch_size]
        batch_metas = metadatas[i : i + batch_size]
        batch_ids = ids[i : i + batch_size]

        collection.add(
            documents=batch_docs,
            metadatas=batch_metas,
            ids=batch_ids,
        )
        print(f"Uploaded batch {i // batch_size + 1}: {len(batch_docs)} chunks...")


async def async_batch_upload(
    collection,
    documents: List[str],
    metadatas: List[dict],
    ids: List[str],
    batch_size: int = 100,
) -> None:
    """Async variant of batch_upload using asyncio.to_thread for Chroma writes."""
    total_chunks = len(documents)

    for i in range(0, total_chunks, batch_size):
        batch_docs = documents[i : i + batch_size]
        batch_metas = metadatas[i : i + batch_size]
        batch_ids = ids[i : i + batch_size]

        await safe_batch_upload(collection, batch_docs, batch_metas, batch_ids)
        print(f"[async] Uploaded batch {i // batch_size + 1}: {len(batch_docs)} chunks...")


async def safe_batch_upload(
    collection,
    batch_docs: List[str],
    batch_metas: List[dict],
    batch_ids: List[str],
    max_retries: int = 3,
) -> None:
    """Wrap a single batch upload with retry logic.

    Retries on transient errors like SQLite 'database is locked' up to max_retries.
    """
    for attempt in range(max_retries):
        try:
            await asyncio.to_thread(
                collection.add,
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids,
            )
            return
        except Exception as e:
            message = str(e).lower()
            if "locked" in message and attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            logging.error(
                f"Failed to upload batch (attempt {attempt + 1}/{max_retries}): {e}"
            )
            raise