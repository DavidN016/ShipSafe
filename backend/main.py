from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from backend.database.chroma import collection
from backend.ingest import chunk_text_for_extension, async_batch_upload


app = FastAPI(title="ShipSafe Backend", version="0.1.0")


class FileChange(BaseModel):
    path: str
    content: str


class AnalyzeRequest(BaseModel):
    repository: Optional[str] = None
    commit_sha: Optional[str] = None
    files: List[FileChange]


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


# For local dev:
# uvicorn backend.main:app --reload

