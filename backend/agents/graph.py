"""
LangGraph workflow: Ingestion → Retrieval → Detection → Audit → Remediation → Patch verification (loop).

Per AGENTS.md §4 and §5.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from backend.database.retrieve import context_chunks_to_strings, get_context_chunks

from .state import AgentState
from .nodes import (
    ingestion_node,
    auditor_node,
    detector_node,
    patch_audit_node,
    remediator_node,
)


# ---------------------------------------------------------------------------
# 4.2 Retrieval node
# ---------------------------------------------------------------------------

def make_retrieval_node(collection: Any):
    """Call get_context_chunks / context_chunks_to_strings, write to context_chunks."""

    def retrieval_node(state: AgentState) -> dict[str, Any]:
        raw_diff = state.get("raw_diff") or ""
        repository = state.get("repository")
        file_path = state.get("file_path")
        chunks = get_context_chunks(
            raw_diff,
            collection,
            repository=repository,
            file_path=file_path,
        )
        return {"context_chunks": context_chunks_to_strings(chunks)}

    return retrieval_node


def _route_after_detection(state: AgentState) -> Literal["audit", "end"]:
    """If no vulnerabilities, end; else go to Audit."""
    vulns = state.get("vulnerabilities") or []
    return "end" if not vulns else "audit"


def _route_after_audit(state: AgentState) -> Literal["remediation", "end"]:
    """If not verified, end; else go to Remediation."""
    if state.get("is_verified"):
        return "remediation"
    return "end"


def _route_after_patch_audit(state: AgentState) -> Literal["remediation", "end"]:
    """If patch is secure, end; else loop back to Remediator (AGENTS.md §5)."""
    if state.get("is_verified"):
        return "end"
    return "remediation"


def build_workflow(collection: Any):
    """
    Build and compile the ShipSafe agent workflow.

    Args:
        collection: Chroma collection for context retrieval (from backend.database.chroma).

    Returns:
        Compiled LangGraph runnable. Invoke with initial AgentState (at least raw_diff, file_path; optional repository, original_code).
    """
    workflow = StateGraph(AgentState)

    retrieval_node_fn = make_retrieval_node(collection)

    workflow.add_node("ingestion", ingestion_node)
    workflow.add_node("retrieval", retrieval_node_fn)
    workflow.add_node("detection", detector_node)
    workflow.add_node("audit", auditor_node)
    workflow.add_node("remediation", remediator_node)
    workflow.add_node("patch_audit", patch_audit_node)

    workflow.add_edge(START, "ingestion")
    workflow.add_edge("ingestion", "retrieval")
    workflow.add_edge("retrieval", "detection")
    workflow.add_conditional_edges(
        "detection",
        _route_after_detection,
        {"audit": "audit", "end": END},
    )
    workflow.add_conditional_edges(
        "audit",
        _route_after_audit,
        {"remediation": "remediation", "end": END},
    )
    workflow.add_edge("remediation", "patch_audit")
    workflow.add_conditional_edges(
        "patch_audit",
        _route_after_patch_audit,
        {"remediation": "remediation", "end": END},
    )

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)
