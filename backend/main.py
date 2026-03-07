from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

# Ensure project root is on path so "backend" resolves when run from backend/ (e.g. uvicorn main:app)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root / ".env")

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from backend.database.chroma import collection
from backend.ingest import chunk_text_for_extension, async_batch_upload, ingest_repo_paths
from backend.database.retrieve import get_context_chunks, context_chunks_to_strings
from backend.agents.graph import build_workflow

HOST = os.environ.get("SHIPSAFE_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHIPSAFE_PORT", "8000"))

app = FastAPI(title="ShipSafe Backend", version="0.1.0")

# Compiled LangGraph workflow (ingestion → retrieval → detection → audit → remediation → patch_audit loop)
_workflow = None


def _get_workflow():
    global _workflow
    if _workflow is None:
        _workflow = build_workflow(collection)
    return _workflow


class FileChange(BaseModel):
    path: str
    content: str


class AnalyzeRequest(BaseModel):
    repository: Optional[str] = None
    commit_sha: Optional[str] = None
    files: List[FileChange]


class DiffPayload(BaseModel):
    """Payload for pipeline: diff + file; optional repo/original_code for audit and retrieval."""
    raw_diff: str
    file_path: str
    repository: Optional[str] = None
    commit_sha: Optional[str] = None
    original_code: Optional[str] = None


class RepoIngestRequest(BaseModel):
    """Request to ingest user-selected repos (local paths) into Chroma."""
    repo_paths: List[str]
    repository: Optional[str] = None  # optional label; defaults to folder name per repo


class RetrieveRequest(BaseModel):
    """Request to get context chunks for a diff (embed diff, query Chroma)."""
    raw_diff: str
    repository: Optional[str] = None  # restrict search to this repo (recommended when multiple repos in Chroma)
    file_path: Optional[str] = None
    n_results: Optional[int] = 10
    max_diff_chars: Optional[int] = None


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


@app.post("/retrieve")
async def retrieve_context(payload: RetrieveRequest) -> dict:
    """Embed the diff, query Chroma, return context_chunks for the pipeline."""
    chunks_with_metadata = get_context_chunks(
        payload.raw_diff,
        collection,
        repository=payload.repository,
        file_path=payload.file_path,
        n_results=payload.n_results or 10,
        max_diff_chars=payload.max_diff_chars,
    )
    context_chunks = context_chunks_to_strings(chunks_with_metadata)
    return {
        "context_chunks": context_chunks,
        "chunks_with_metadata": chunks_with_metadata,
    }


@app.post("/analyze/diff")
async def analyze_diff(payload: DiffPayload) -> dict:
    """Run the full pipeline: ingestion (state init) → retrieval → detection → audit → remediation → patch audit.
    Request body: raw_diff, file_path; optional repository, commit_sha, original_code.
    Returns final state so caller can post analysis/patch to GitHub (e.g. PR comment)."""
    import asyncio
    initial: dict = {
        "raw_diff": payload.raw_diff,
        "file_path": payload.file_path,
        "repository": payload.repository,
        "commit_sha": payload.commit_sha,
        "original_code": payload.original_code or "",
    }
    workflow = _get_workflow()
    loop = asyncio.get_event_loop()
    final = await loop.run_in_executor(None, lambda: workflow.invoke(initial))
    return {
        "file_path": final.get("file_path"),
        "vulnerabilities": final.get("vulnerabilities") or [],
        "is_verified": final.get("is_verified"),
        "audit_feedback": final.get("audit_feedback"),
        "remediation_patch": final.get("remediation_patch"),
        "analysis_summary": final.get("analysis_summary"),
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=HOST,
        port=PORT,
        reload=True,
    )

