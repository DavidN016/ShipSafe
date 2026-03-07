"""
Shared state for the ShipSafe LangGraph workflow.

All agents read from and write to this centralized state per AGENTS.md.
"""

from typing import List, TypedDict


class Vulnerability(TypedDict, total=False):
    """Detected security issue with metadata (per AGENTS.md example)."""

    type: str
    line_number: int
    description: str
    confidence_score: float


class AgentState(TypedDict, total=False):

    # Ingestion
    repository: str
    commit_sha: str
    raw_diff: str
    file_path: str

    # Retrieval
    context_chunks: List[str]

    # Detection
    vulnerabilities: List[Vulnerability]

    # Audit
    is_verified: bool
    audit_feedback: str

    # Remediation
    remediation_patch: str

    # Loop control
    iteration_count: int

    # Auditor support
    original_code: str

    # Final output
    analysis_summary: str
