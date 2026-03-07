"""Code chunking utility using LangChain's RecursiveCharacterTextSplitter.

Given a file path, this module chooses a language-aware splitter (when
supported by LangChain) and returns chunks of code for downstream RAG / Chroma.
"""

from __future__ import annotations

from pathlib import Path
from typing import List
import asyncio
import logging

# Path segments to exclude when walking repos (e.g. .git, node_modules)
EXCLUDE_PATHS = [
    ".git/",
    "node_modules/",
    "dist/",
    "build/",
    ".next/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
]

# File extensions to skip (binary/media/archives)
EXCLUDE_EXTENSIONS = [
    ".png", ".jpg", ".jpeg", ".gif",
    ".mp4", ".mp3",
    ".zip", ".tar", ".gz",
]

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


def should_skip_path(path: Path) -> bool:
    """Return True if this path should be excluded from ingestion."""
    path_str = path.as_posix() + ("/" if path.is_dir() else "")
    for exclude in EXCLUDE_PATHS:
        if exclude in path_str:
            return True
    if path.is_file() and path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return True
    return False


def iter_repo_files(root: Path):
    """Yield file paths under root that pass EXCLUDE_PATHS and EXCLUDE_EXTENSIONS."""
    root = Path(root).resolve()
    if not root.is_dir():
        return
    for item in root.rglob("*"):
        if item.is_file() and not should_skip_path(item):
            yield item


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


def _collect_repo_chunks(
    repo_path: Path,
    repository: str | None,
) -> tuple[List[str], List[dict], List[str]]:
    """Walk repo (respecting EXCLUDE_*), chunk files, return (documents, metadatas, ids)."""
    documents: List[str] = []
    metadatas: List[dict] = []
    ids: List[str] = []
    repo_path = Path(repo_path).resolve()
    repo_name = repository or repo_path.name

    for file_path in iter_repo_files(repo_path):
        try:
            chunks = chunk_file(str(file_path))
        except Exception as e:
            logging.warning("Skipping %s: %s", file_path, e)
            continue

        try:
            rel = file_path.relative_to(repo_path)
        except ValueError:
            rel = file_path

        rel_str = rel.as_posix()
        for idx, chunk in enumerate(chunks):
            documents.append(chunk)
            metadatas.append({
                "file_path": rel_str,
                "chunk_index": idx,
                "repository": repo_name,
            })
            ids.append(f"{repo_name}::{rel_str}::{idx}")

    return documents, metadatas, ids


async def ingest_repo_paths(
    collection,
    repo_paths: List[str],
    repository: str | None = None,
) -> dict:
    """Ingest one or more repo directories into Chroma using the same chunking/upload as /analyze.

    Uses EXCLUDE_PATHS and EXCLUDE_EXTENSIONS to skip unwanted files.
    Returns counts: ingested_files, ingested_chunks, repo_chunks (per-repo breakdown).
    """
    total_documents: List[str] = []
    total_metadatas: List[dict] = []
    total_ids: List[str] = []
    repo_chunks: List[dict] = []

    for repo_path in repo_paths:
        path = Path(repo_path)
        if not path.exists():
            logging.warning("Repo path does not exist: %s", repo_path)
            continue
        if not path.is_dir():
            logging.warning("Repo path is not a directory: %s", repo_path)
            continue

        name = repository or path.name
        docs, metas, ids = _collect_repo_chunks(path, name)
        if docs:
            total_documents.extend(docs)
            total_metadatas.extend(metas)
            total_ids.extend(ids)
            repo_chunks.append({
                "repo_path": str(path.resolve()),
                "repository": name,
                "chunks": len(docs),
            })

    if total_documents:
        await async_batch_upload(
            collection=collection,
            documents=total_documents,
            metadatas=total_metadatas,
            ids=total_ids,
        )

    return {
        "ingested_repos": len(repo_chunks),
        "ingested_chunks": len(total_documents),
        "repos": repo_chunks,
    }