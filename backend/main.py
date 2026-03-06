from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

# Ensure project root is on path so "backend" resolves when run from backend/ (e.g. uvicorn main:app)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from database.chroma import collection
from backend.ingest import chunk_text_for_extension, async_batch_upload, ingest_repo_paths

HOST = os.environ.get("SHIPSAFE_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHIPSAFE_PORT", "8000"))

app = FastAPI(title="ShipSafe Backend", version="0.1.0")


class FileChange(BaseModel):
    path: str
    content: str


class AnalyzeRequest(BaseModel):
    repository: Optional[str] = None
    commit_sha: Optional[str] = None
    files: List[FileChange]


class DiffPayload(BaseModel):
    """Payload sent by pre-push hook. Use POST /analyze/diff to test the hook."""
    raw_diff: str
    file_path: str


class RepoIngestRequest(BaseModel):
    """Request to ingest user-selected repos (local paths) into Chroma."""
    repo_paths: List[str]
    repository: Optional[str] = None  # optional label; defaults to folder name per repo


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze_changes(payload: AnalyzeRequest) -> dict:
    """Receive changed files (e.g., from a GitHub webhook) and ingest into Chroma."""
    all_documents: List[str] = []
    all_metadatas: List[dict] = []
    all_ids: List[str] = []

    for file in payload.files:
        ext = Path(file.path).suffix or ".txt"
        chunks = chunk_text_for_extension(file.content, ext)

        for idx, chunk in enumerate(chunks):
            all_documents.append(chunk)
            all_metadatas.append(
                {
                    "file_path": file.path,
                    "chunk_index": idx,
                    "commit_sha": payload.commit_sha,
                    "repository": payload.repository,
                }
            )
            all_ids.append(f"{payload.commit_sha or 'no-commit'}::{file.path}::{idx}")

    if all_documents:
        await async_batch_upload(
            collection=collection,
            documents=all_documents,
            metadatas=all_metadatas,
            ids=all_ids,
        )

    return {
        "ingested_files": len(payload.files),
        "ingested_chunks": len(all_documents),
    }


@app.post("/repos/ingest")
async def ingest_repos(payload: RepoIngestRequest) -> dict:
    """Ingest user-selected repo directories into Chroma.

    Walks each path, skips EXCLUDE_PATHS (e.g. .git, node_modules) and
    EXCLUDE_EXTENSIONS (e.g. images, archives), chunks code, and uploads.
    Reuses the same ingestion/chunking pipeline as /analyze.
    """
    return await ingest_repo_paths(
        collection=collection,
        repo_paths=payload.repo_paths,
        repository=payload.repository,
    )


@app.post("/analyze/diff")
async def analyze_diff(payload: DiffPayload) -> dict:
    """Temporary: accept pre-push hook payload (raw_diff + file_path), return 200.
    Use this endpoint to test the hook; point the hook at SHIPSAFE_API_URL/analyze/diff."""
    return {
        "received": True,
        "raw_diff_length": len(payload.raw_diff),
        "file_path": payload.file_path,
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=HOST,
        port=PORT,
        reload=True,
    )

