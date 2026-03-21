"""
GitHub webhook helpers: HMAC verification, PR/push diff fetch, split unified diff per file.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

import httpx

# diff --git a/path b/path (paths may contain spaces in rare cases; GitHub escapes)
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")


def get_webhook_secret() -> str | None:
    return os.environ.get("GITHUB_WEBHOOK_SECRET") or os.environ.get("SHIPSAFE_GITHUB_WEBHOOK_SECRET")


def get_github_token() -> str | None:
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("SHIPSAFE_GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
    )


def verify_github_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """
    Verify X-Hub-Signature-256 when GITHUB_WEBHOOK_SECRET is set.
    If no secret is configured, verification is skipped (dev convenience).
    """
    secret = get_webhook_secret()
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[7:], digest)


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github.diff",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def fetch_pull_request_diff(owner: str, repo: str, pr_number: int, token: str) -> str:
    """Return unified diff text for a pull request."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    with httpx.Client(timeout=120.0) as client:
        r = client.get(url, headers=_github_headers(token))
        r.raise_for_status()
        return r.text


def fetch_compare_diff(owner: str, repo: str, base: str, head: str, token: str) -> str:
    """Return unified diff for base...head (e.g. push before/after SHAs)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}"
    with httpx.Client(timeout=120.0) as client:
        r = client.get(url, headers=_github_headers(token))
        r.raise_for_status()
        return r.text


def split_unified_diff(full_diff: str) -> list[tuple[str, str]]:
    """
    Split a multi-file unified diff into [(file_path, diff_chunk), ...].
    Uses the b/ path from each diff --git header.
    """
    if not full_diff or not full_diff.strip():
        return []

    lines = full_diff.splitlines(keepends=True)
    chunks: list[tuple[str, str]] = []
    current_path: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _DIFF_GIT_RE.match(line.rstrip("\n"))
        if m:
            if current_path is not None:
                chunks.append((current_path, "".join(current_lines)))
            current_path = m.group(2).strip()
            current_lines = [line]
        else:
            if current_path is not None:
                current_lines.append(line)

    if current_path is not None:
        chunks.append((current_path, "".join(current_lines)))

    return chunks


def should_process_pull_request(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (run_workflow, reason_if_skip)."""
    action = payload.get("action") or ""
    if action not in ("opened", "synchronize", "reopened", "ready_for_review"):
        return False, f"ignored action={action!r}"
    pr = payload.get("pull_request") or {}
    if pr.get("draft"):
        return False, "draft pull request"
    return True, ""


def parse_repo_from_payload(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    """
    Return (owner, repo, full_name) from webhook repository object.
    """
    repo = payload.get("repository") or {}
    full = repo.get("full_name") or ""
    if not full or "/" not in full:
        return None
    owner, name = full.split("/", 1)
    return owner, name, full


def extract_push_compare(payload: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    """
    For push events: (owner, repo, full_name, before, after) if compare is possible.
    Skips initial branch creation (before all zeros).
    """
    parsed = parse_repo_from_payload(payload)
    if not parsed:
        return None
    owner, repo, full_name = parsed
    before = payload.get("before") or ""
    after = payload.get("after") or ""
    if not after or not before or set(before) == {"0"}:
        return None
    return owner, repo, full_name, before, after


def get_max_webhook_files() -> int:
    try:
        return max(1, int(os.environ.get("SHIPSAFE_WEBHOOK_MAX_FILES", "25")))
    except ValueError:
        return 25
