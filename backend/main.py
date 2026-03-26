from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

import httpx

# Ensure project root is on path so "backend" resolves when run from backend/ (e.g. uvicorn main:app)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root / ".env")

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.chroma import get_collection
from backend.database.users_db import (
    ConnectedRepo,
    ScanResult,
    ScanRun,
    User,
    get_db,
    init_db,
    is_repository_connected,
    record_scan_results,
)
from backend.ingest import chunk_text_for_extension, async_batch_upload, ingest_repo_paths
from backend.database.retrieve import get_context_chunks, context_chunks_to_strings
from backend.agents.graph import build_prepush_workflow, build_workflow
from backend.services.github_hooks import (
    callback_url_is_safe_for_demo,
    delete_repo_webhook,
    ensure_repo_webhook,
    get_webhook_secret_for_config,
    parse_owner_repo,
    webhook_callback_url,
)
from backend.services.github_workflow_file import ensure_shipsafe_workflow_file
from backend.services.github_webhook import (
    extract_push_compare,
    fetch_compare_diff,
    fetch_pull_request_diff,
    get_github_token,
    get_max_webhook_files,
    parse_repo_from_payload,
    should_process_pull_request,
    split_unified_diff,
    verify_github_signature,
)

HOST = os.environ.get("SHIPSAFE_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHIPSAFE_PORT", "8000"))

app = FastAPI(title="ShipSafe Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# Lazily initialized Chroma collection (persistent vector store)
_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        _collection = get_collection()
    return _collection


# Compiled LangGraph workflow (ingestion → retrieval → detection → audit → remediation → patch_audit loop)
_workflow = None
_prepush_workflow = None


def _get_workflow():
    global _workflow
    if _workflow is None:
        _workflow = build_workflow(_get_collection())
    return _workflow


def _get_prepush_workflow():
    global _prepush_workflow
    if _prepush_workflow is None:
        _prepush_workflow = build_prepush_workflow(_get_collection())
    return _prepush_workflow


async def _run_agent_workflow(initial: dict[str, Any]) -> dict[str, Any]:
    """Run LangGraph pipeline in a thread pool (same as /analyze/diff)."""
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


async def _run_prepush_workflow(initial: dict[str, Any]) -> dict[str, Any]:
    """Run prepush graph (ingestion→retrieval→detection→audit) in a thread pool."""
    workflow = _get_prepush_workflow()
    loop = asyncio.get_event_loop()
    final = await loop.run_in_executor(None, lambda: workflow.invoke(initial))
    return {
        "file_path": final.get("file_path"),
        "vulnerabilities": final.get("vulnerabilities") or [],
        "is_verified": final.get("is_verified"),
        "audit_feedback": final.get("audit_feedback"),
        "remediation_patch": "",  # not produced in prepush flow
        "analysis_summary": final.get("analysis_summary"),
    }


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


# --- Users & connected repos ---

class UserUpsertRequest(BaseModel):
    github_id: str
    login: str


class ConnectedRepoAddRequest(BaseModel):
    repo_full_name: str


class PrepushRequest(BaseModel):
    raw_diff: str
    repository: Optional[str] = None
    commit_sha: Optional[str] = None


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok"}


@app.post("/users", response_model=dict)
def upsert_user(payload: UserUpsertRequest, db: Session = Depends(get_db)) -> dict:
    """Create or get user by GitHub id. Returns user id and login."""
    user = db.query(User).filter(User.github_id == payload.github_id).first()
    if user is None:
        user = User(github_id=payload.github_id, login=payload.login)
        db.add(user)
        db.commit()
        db.refresh(user)
    return {"id": user.id, "github_id": user.github_id, "login": user.login}


@app.get("/users/{github_id}/connected-repos", response_model=dict)
def list_connected_repos(github_id: str, db: Session = Depends(get_db)) -> dict:
    """List connected repo full names for a user."""
    user = db.query(User).filter(User.github_id == github_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    repos = [r.repo_full_name for r in user.connected_repos]
    return {"repos": repos}


def _try_register_github_webhook(
    repo_full_name: str, github_access_token: Optional[str]
) -> tuple[Optional[int], Optional[str]]:
    """
    Register (or reuse) a repo webhook pointing at SHIPSAFE_WEBHOOK_PUBLIC_URL/webhook/github.
    Returns (hook_id, error_message). hook_id None if skipped or failed.
    """
    cb = webhook_callback_url()
    if not github_access_token:
        return None, "Missing X-GitHub-Access-Token header (sign in again after scope change)"
    if not cb or not callback_url_is_safe_for_demo(cb):
        return None, "Set SHIPSAFE_WEBHOOK_PUBLIC_URL to your public API base (e.g. https://abc.ngrok-free.app)"
    try:
        owner, repo = parse_owner_repo(repo_full_name)
    except ValueError as e:
        return None, str(e)
    secret = get_webhook_secret_for_config()
    try:
        hook_id = ensure_repo_webhook(owner, repo, github_access_token, cb, secret)
        return hook_id, None
    except httpx.HTTPStatusError as e:
        msg = (e.response.text or "")[:300]
        return None, f"GitHub API {e.response.status_code}: {msg}"
    except httpx.RequestError as e:
        return None, f"GitHub request failed: {e}"


def _try_install_shipsafe_workflow(
    repo_full_name: str, github_access_token: Optional[str]
) -> tuple[Optional[bool], Optional[str]]:
    """
    Commit ``.github/workflows/shipsafe.yml`` via GitHub Contents API.
    Returns (installed_or_unchanged, error_message). Skips when token missing.
    """
    if not github_access_token:
        return None, "Missing X-GitHub-Access-Token header (required to add workflow file)"
    ok, err = ensure_shipsafe_workflow_file(repo_full_name, github_access_token)
    if ok:
        return True, None
    return False, err


@app.post("/users/{github_id}/connected-repos", response_model=dict)
def add_connected_repo(
    github_id: str,
    payload: ConnectedRepoAddRequest,
    db: Session = Depends(get_db),
    x_github_access_token: Optional[str] = Header(default=None, alias="X-GitHub-Access-Token"),
) -> dict:
    """Connect a repo for a user (idempotent). Registers GitHub webhook when token + public URL are set."""
    user = db.query(User).filter(User.github_id == github_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    existing = (
        db.query(ConnectedRepo)
        .filter(
            ConnectedRepo.user_id == user.id,
            ConnectedRepo.repo_full_name == payload.repo_full_name,
        )
        .first()
    )
    if existing is not None:
        out: dict[str, Any] = {
            "repo_full_name": payload.repo_full_name,
            "connected": True,
            "webhook_registered": existing.github_hook_id is not None,
        }
        if existing.github_hook_id is None:
            hook_id, err = _try_register_github_webhook(
                payload.repo_full_name, x_github_access_token
            )
            if hook_id is not None:
                existing.github_hook_id = hook_id
                db.commit()
                out["webhook_registered"] = True
            elif err:
                out["webhook_error"] = err
        wf_ok, wf_err = _try_install_shipsafe_workflow(
            payload.repo_full_name, x_github_access_token
        )
        if wf_ok is True:
            out["shipsafe_workflow"] = "installed_or_unchanged"
        elif wf_ok is False and wf_err:
            out["workflow_error"] = wf_err
        return out

    hook_id, hook_err = _try_register_github_webhook(
        payload.repo_full_name, x_github_access_token
    )
    conn = ConnectedRepo(
        user_id=user.id,
        repo_full_name=payload.repo_full_name,
        github_hook_id=hook_id,
    )
    db.add(conn)
    db.commit()
    resp: dict[str, Any] = {
        "repo_full_name": payload.repo_full_name,
        "connected": True,
        "webhook_registered": hook_id is not None,
    }
    if hook_err and hook_id is None:
        resp["webhook_error"] = hook_err
    wf_ok, wf_err = _try_install_shipsafe_workflow(
        payload.repo_full_name, x_github_access_token
    )
    if wf_ok is True:
        resp["shipsafe_workflow"] = "installed_or_unchanged"
    elif wf_ok is False and wf_err:
        resp["workflow_error"] = wf_err
    return resp


@app.post("/users/{github_id}/repos/shipsafe-workflow", response_model=dict)
def install_shipsafe_workflow_route(
    github_id: str,
    payload: ConnectedRepoAddRequest,
    db: Session = Depends(get_db),
    x_github_access_token: Optional[str] = Header(default=None, alias="X-GitHub-Access-Token"),
) -> dict[str, Any]:
    """
    Create or update ``.github/workflows/shipsafe.yml`` in the given repo (must already be connected).
    Use this to retry after fixing token permissions or to refresh the workflow template.
    """
    user = db.query(User).filter(User.github_id == github_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    conn = (
        db.query(ConnectedRepo)
        .filter(
            ConnectedRepo.user_id == user.id,
            ConnectedRepo.repo_full_name == payload.repo_full_name,
        )
        .first()
    )
    if conn is None:
        raise HTTPException(
            status_code=400,
            detail="Repository is not connected; connect it first",
        )
    wf_ok, wf_err = _try_install_shipsafe_workflow(
        payload.repo_full_name, x_github_access_token
    )
    if wf_ok is True:
        return {
            "repo_full_name": payload.repo_full_name,
            "shipsafe_workflow": "installed_or_unchanged",
        }
    if wf_ok is None:
        raise HTTPException(status_code=400, detail=wf_err or "Missing GitHub token")
    raise HTTPException(status_code=502, detail=wf_err or "Could not install workflow")


@app.delete("/users/{github_id}/connected-repos", response_model=dict)
def remove_connected_repo(
    github_id: str,
    repo_full_name: str,  # query param: ?repo_full_name=owner%2Frepo
    db: Session = Depends(get_db),
    x_github_access_token: Optional[str] = Header(default=None, alias="X-GitHub-Access-Token"),
) -> dict:
    """Disconnect a repo; removes GitHub webhook when hook id and token are available."""
    user = db.query(User).filter(User.github_id == github_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    conn = (
        db.query(ConnectedRepo)
        .filter(
            ConnectedRepo.user_id == user.id,
            ConnectedRepo.repo_full_name == repo_full_name,
        )
        .first()
    )
    if conn is None:
        raise HTTPException(status_code=404, detail="Connected repo not found")
    if conn.github_hook_id is not None and x_github_access_token:
        try:
            owner, repo = parse_owner_repo(repo_full_name)
            delete_repo_webhook(owner, repo, conn.github_hook_id, x_github_access_token)
        except httpx.HTTPStatusError:
            pass
        except httpx.RequestError:
            pass
    db.delete(conn)
    db.commit()
    return {"repo_full_name": repo_full_name, "connected": False}


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
            collection=_get_collection(),
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
        collection=_get_collection(),
        repo_paths=payload.repo_paths,
        repository=payload.repository,
    )


@app.post("/retrieve")
async def retrieve_context(payload: RetrieveRequest) -> dict:
    """Embed the diff, query Chroma, return context_chunks for the pipeline."""
    chunks_with_metadata = get_context_chunks(
        payload.raw_diff,
        _get_collection(),
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
    initial: dict[str, Any] = {
        "raw_diff": payload.raw_diff,
        "file_path": payload.file_path,
        "repository": payload.repository,
        "commit_sha": payload.commit_sha,
        "original_code": payload.original_code or "",
    }
    return await _run_agent_workflow(initial)


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_github_event: Optional[str] = Header(default=None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """
    GitHub webhook entrypoint (agents.md §6).

    - Verifies ``X-Hub-Signature-256`` when ``GITHUB_WEBHOOK_SECRET`` is set.
    - **pull_request** (opened, synchronize, reopened, ready_for_review): fetches PR diff via API,
      splits by file, runs the agent workflow per file (cap: ``SHIPSAFE_WEBHOOK_MAX_FILES``, default 25).
    - **push**: compares ``before...after`` and runs the workflow per changed file (same cap).
    - **ping**: acknowledges without running agents.

    Requires ``GITHUB_TOKEN`` (or ``SHIPSAFE_GITHUB_TOKEN`` / ``GH_TOKEN``) with ``repo`` scope
    to fetch diffs from the GitHub API.
    """
    body = await request.body()
    if not verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    event = (x_github_event or "").lower()
    if event == "ping":
        return {"ok": True, "event": "ping", "message": "pong"}

    token = get_github_token()
    max_files = get_max_webhook_files()

    if event == "pull_request":
        if not token:
            raise HTTPException(
                status_code=503,
                detail="GITHUB_TOKEN (or SHIPSAFE_GITHUB_TOKEN / GH_TOKEN) is required to fetch diffs",
            )
        run, skip_reason = should_process_pull_request(payload)
        if not run:
            return {"ok": True, "event": event, "skipped": True, "reason": skip_reason}

        parsed = parse_repo_from_payload(payload)
        if not parsed:
            raise HTTPException(status_code=400, detail="Missing repository.full_name in payload")
        owner, repo_name, full_name = parsed
        if not is_repository_connected(db, full_name):
            return {
                "ok": True,
                "event": event,
                "skipped": True,
                "reason": "repository not connected in ShipSafe",
            }
        pr = payload.get("pull_request") or {}
        pr_number = pr.get("number")
        if pr_number is None:
            raise HTTPException(status_code=400, detail="Missing pull_request.number")
        head = pr.get("head") or {}
        commit_sha = head.get("sha") or ""

        try:
            full_diff = fetch_pull_request_diff(owner, repo_name, int(pr_number), token)
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=502,
                detail=f"GitHub API error fetching PR diff: {e.response.status_code} {e.response.text[:500]}",
            ) from e
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"GitHub API request failed: {e}") from e

        file_diffs = split_unified_diff(full_diff)
        if not file_diffs and full_diff.strip():
            file_diffs = [("unknown", full_diff)]

        results: list[dict[str, Any]] = []
        truncated = len(file_diffs) > max_files
        for path, diff_text in file_diffs[:max_files]:
            initial = {
                "raw_diff": diff_text,
                "file_path": path,
                "repository": full_name,
                "commit_sha": commit_sha,
                "original_code": "",
                "pr_number": int(pr_number),
            }
            out = await _run_agent_workflow(initial)
            results.append(out)

        try:
            record_scan_results(
                db,
                source="webhook_pr",
                repository=full_name,
                commit_sha=commit_sha or None,
                results=results,
            )
        except Exception:
            # best-effort persistence; don't fail webhook response
            pass

        return {
            "ok": True,
            "event": event,
            "repository": full_name,
            "pr_number": int(pr_number),
            "files_analyzed": len(results),
            "files_total_in_diff": len(file_diffs),
            "truncated": truncated,
            "results": results,
        }

    if event == "push":
        if not token:
            raise HTTPException(
                status_code=503,
                detail="GITHUB_TOKEN (or SHIPSAFE_GITHUB_TOKEN / GH_TOKEN) is required to fetch diffs",
            )
        compare = extract_push_compare(payload)
        if not compare:
            return {
                "ok": True,
                "event": event,
                "skipped": True,
                "reason": "no compare range (e.g. new branch or missing repository)",
            }
        owner, repo_name, full_name, before, after = compare
        if not is_repository_connected(db, full_name):
            return {
                "ok": True,
                "event": event,
                "skipped": True,
                "reason": "repository not connected in ShipSafe",
            }

        try:
            full_diff = fetch_compare_diff(owner, repo_name, before, after, token)
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=502,
                detail=f"GitHub API error fetching compare diff: {e.response.status_code} {e.response.text[:500]}",
            ) from e
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"GitHub API request failed: {e}") from e

        file_diffs = split_unified_diff(full_diff)
        if not file_diffs and full_diff.strip():
            file_diffs = [("unknown", full_diff)]

        results: list[dict[str, Any]] = []
        truncated = len(file_diffs) > max_files
        for path, diff_text in file_diffs[:max_files]:
            initial = {
                "raw_diff": diff_text,
                "file_path": path,
                "repository": full_name,
                "commit_sha": after,
                "original_code": "",
            }
            out = await _run_agent_workflow(initial)
            results.append(out)

        try:
            record_scan_results(
                db,
                source="webhook_push",
                repository=full_name,
                commit_sha=after or None,
                results=results,
            )
        except Exception:
            pass

        return {
            "ok": True,
            "event": event,
            "repository": full_name,
            "commit_sha": after,
            "files_analyzed": len(results),
            "files_total_in_diff": len(file_diffs),
            "truncated": truncated,
            "results": results,
        }

    return {"ok": True, "event": event or "unknown", "skipped": True, "reason": "event type not handled"}


def _require_prepush_token(authorization: Optional[str]) -> None:
    required = (os.environ.get("SHIPSAFE_PREPUSH_TOKEN") or "").strip()
    if not required:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    provided = authorization.split(" ", 1)[1].strip()
    if provided != required:
        raise HTTPException(status_code=403, detail="Invalid token")


@app.post("/hooks/prepush")
async def hooks_prepush(
    payload: PrepushRequest,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """
    Endpoint used by:
    - local git pre-push hook (`install.sh`)
    - GitHub Actions workflow (`.github/workflows/shipsafe.yml`)

    Returns: {allow_push: bool, reason?: str, results: [...]}
    """
    _require_prepush_token(authorization)

    raw_diff = payload.raw_diff or ""
    if not raw_diff.strip():
        return {"allow_push": True, "reason": "empty diff", "results": []}

    file_diffs = split_unified_diff(raw_diff)
    if not file_diffs:
        file_diffs = [("unknown", raw_diff)]

    results: list[dict[str, Any]] = []
    for path, diff_text in file_diffs[: get_max_webhook_files()]:
        initial = {
            "raw_diff": diff_text,
            "file_path": path,
            "repository": payload.repository,
            "commit_sha": payload.commit_sha,
            "original_code": "",
        }
        out = await _run_prepush_workflow(initial)
        out["auditor_confirmed_vulnerable"] = bool(out.get("is_verified")) and bool(
            out.get("vulnerabilities")
        )
        results.append(out)

    allow_push = not any(r.get("auditor_confirmed_vulnerable") for r in results)
    reason = None if allow_push else "auditor-confirmed findings detected"

    try:
        record_scan_results(
            db,
            source="prepush",
            repository=payload.repository,
            commit_sha=payload.commit_sha,
            results=results,
        )
    except Exception:
        pass

    return {"allow_push": allow_push, "reason": reason, "results": results}


@app.get("/users/{github_id}/findings", response_model=dict)
def list_findings_for_user(
    github_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return recent scan results for repos connected by this user."""
    user = db.query(User).filter(User.github_id == github_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    connected = [r.repo_full_name for r in user.connected_repos]
    if not connected:
        return {"runs": []}

    limit = max(1, min(int(limit), 200))
    runs = (
        db.query(ScanRun)
        .filter(ScanRun.repository.in_(connected))
        .order_by(ScanRun.created_at.desc(), ScanRun.id.desc())
        .limit(limit)
        .all()
    )

    import json as _json

    out_runs: list[dict[str, Any]] = []
    for run in runs:
        results = (
            db.query(ScanResult)
            .filter(ScanResult.scan_run_id == run.id)
            .order_by(ScanResult.id.asc())
            .all()
        )
        out_runs.append(
            {
                "id": run.id,
                "source": run.source,
                "repository": run.repository,
                "commit_sha": run.commit_sha,
                "created_at": run.created_at.isoformat() if getattr(run, "created_at", None) else None,
                "results": [
                    {
                        "file_path": r.file_path,
                        "auditor_confirmed_vulnerable": bool(r.auditor_confirmed_vulnerable),
                        "vulnerabilities": _json.loads(r.vulnerabilities_json or "[]"),
                        "audit_feedback": r.audit_feedback,
                        "remediation_patch": r.remediation_patch,
                    }
                    for r in results
                ],
            }
        )

    return {"runs": out_runs}


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=HOST,
        port=PORT,
        reload=True,
    )

