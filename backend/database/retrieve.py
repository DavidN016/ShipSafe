"""Chroma retrieval: embed a diff (or snippet), query the collection, return context chunks.

Used by the agent pipeline to get semantically similar code chunks for RAG.
"""

from __future__ import annotations

from typing import Any

# Default max characters of diff-derived query (avoids token limits)
DEFAULT_MAX_DIFF_CHARS = 8_000


def extract_diff_code(diff: str) -> str:
    """Extract only added/removed line content from a unified diff."""
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
            continue
        if line.startswith("+") or line.startswith("-"):
            out.append(line[1:])
    return "\n".join(out)


def _query_text(diff: str, max_chars: int | None) -> str:
    """Build query from diff: only changed lines, then optionally truncate."""
    code = extract_diff_code(diff)
    if not code.strip():
        return diff
    if max_chars is None or len(code) <= max_chars:
        return code
    return code[:max_chars] + "\n[... truncated ...]"


def _build_where_filter(
    where: dict[str, Any] | None,
    repository: str | None,
    file_path: str | None,
) -> dict[str, Any] | None:
    """Build Chroma where filter: explicit where, or repository (and optional file_path)."""
    if where is not None:
        return where
    if repository is None:
        return None
    base = {"repository": repository}
    return {**base, "file_path": file_path} if file_path else base


def get_context_chunks(
    diff: str,
    collection: Any,
    *,
    repository: str | None = None,
    file_path: str | None = None,
    n_results: int = 10,
    max_diff_chars: int | None = DEFAULT_MAX_DIFF_CHARS,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Embed the diff, query Chroma, and return context chunks with metadata.

    Chroma will embed the diff (or snippet) using the collection's embedding
    function and return the nearest stored chunks. When multiple repos are
    stored, pass repository to restrict the search to that repo (faster and
    more relevant).

    Args:
        diff: Raw diff text (e.g. from webhook).
        collection: Chroma collection (from backend.database.chroma).
        repository: Repository name to restrict search to (uses metadata.repository).
          When provided and where is not set, query is scoped to this repo only.
        file_path: Optional path to further narrow results (combined with where when provided).
        n_results: Number of chunks to return.
        max_diff_chars: Cap diff length used for the query; None = use full diff.
        where: Optional Chroma metadata filter. If set, overrides default repository filter.

    Returns:
        List of dicts, each with:
          - "text": chunk content (str)
          - "metadata": Chroma metadata (file_path, chunk_index, repository, etc.)
          - "distance": similarity distance (lower = more similar)
    """
    query_text = _query_text(diff, max_diff_chars)
    where_filter = _build_where_filter(where, repository, file_path)

    kwargs: dict[str, Any] = {
        "query_texts": [query_text],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if where_filter is not None:
        kwargs["where"] = where_filter

    result = collection.query(**kwargs)

    # query() returns lists of lists (one per query; we have a single query)
    documents = result.get("documents") or [[]]
    metadatas = result.get("metadatas") or [[]]
    distances = result.get("distances") or [[]]

    docs = documents[0] if documents else []
    metas = metadatas[0] if metadatas else []
    dists = distances[0] if distances else []

    chunks: list[dict[str, Any]] = []
    for i, text in enumerate(docs):
        meta = metas[i] if i < len(metas) else {}
        dist = float(dists[i]) if i < len(dists) else 0.0
        chunks.append({
            "text": text or "",
            "metadata": meta,
            "distance": dist,
        })
    return chunks


def context_chunks_to_strings(chunks: list[dict[str, Any]]) -> list[str]:
    """Return just the text of each chunk, for pipeline state context_chunks field."""
    return [c["text"] for c in chunks if c.get("text")]
