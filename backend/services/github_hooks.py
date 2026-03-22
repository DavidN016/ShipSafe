"""
Create/delete repository webhooks via GitHub REST API (demo: connect repo in UI).
Requires OAuth token with admin:repo_hook (or fine-grained Administration on repository).
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


def get_public_webhook_base_url() -> str | None:
    """
    Base URL reachable by GitHub (no trailing slash), e.g. https://abc.ngrok-free.app
    Full callback will be {base}/webhook/github
    """
    raw = (os.environ.get("SHIPSAFE_WEBHOOK_PUBLIC_URL") or "").strip().rstrip("/")
    return raw or None


def webhook_callback_url() -> str | None:
    base = get_public_webhook_base_url()
    return f"{base}/webhook/github" if base else None


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def list_repo_hooks(owner: str, repo: str, token: str) -> list[dict[str, Any]]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/hooks"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(token))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []


def find_hook_id_for_url(
    hooks: list[dict[str, Any]], callback_url: str
) -> int | None:
    """Return hook id if a webhook already points to our callback URL."""
    want = callback_url.rstrip("/")
    for h in hooks:
        cfg = h.get("config") or {}
        u = (cfg.get("url") or "").rstrip("/")
        if u == want and h.get("id") is not None:
            return int(h["id"])
    return None


def create_repo_webhook(
    owner: str,
    repo: str,
    token: str,
    callback_url: str,
    secret: str,
    events: list[str] | None = None,
) -> int:
    """Create a new repo webhook; returns hook id."""
    events = events or ["push", "pull_request"]
    url = f"{GITHUB_API}/repos/{owner}/{repo}/hooks"
    body = {
        "name": "webhook",
        "active": True,
        "events": events,
        "config": {
            "url": callback_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, headers=_headers(token), json=body)
        r.raise_for_status()
        data = r.json()
        return int(data["id"])


def delete_repo_webhook(owner: str, repo: str, hook_id: int, token: str) -> None:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/hooks/{hook_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.delete(url, headers=_headers(token))
        if r.status_code == 404:
            return
        r.raise_for_status()


def ensure_repo_webhook(
    owner: str,
    repo: str,
    token: str,
    callback_url: str,
    secret: str,
) -> int:
    """
    Create webhook if none matches callback_url; otherwise return existing hook id.
    """
    hooks = list_repo_hooks(owner, repo, token)
    existing = find_hook_id_for_url(hooks, callback_url)
    if existing is not None:
        return existing
    return create_repo_webhook(owner, repo, token, callback_url, secret)


def parse_owner_repo(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repo full_name: {full_name!r}")
    return parts[0], parts[1]


def get_webhook_secret_for_config() -> str:
    """Secret stored in GitHub hook config; must match server verification."""
    s = os.environ.get("GITHUB_WEBHOOK_SECRET") or os.environ.get(
        "SHIPSAFE_GITHUB_WEBHOOK_SECRET"
    )
    if s:
        return s
    # Demo default so local/ngrok works without extra env (not for production).
    return "shipsafe-demo-webhook-secret"


def callback_url_is_safe_for_demo(url: str) -> bool:
    """Block obviously invalid callback URLs."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in ("https", "http") and bool(p.netloc)
