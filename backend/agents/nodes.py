"""
Agent nodes for the ShipSafe LangGraph workflow.

Detector, Auditor, and Remediator per agents.md.
"""

import json
import os
import re
from typing import Any

import torch
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from torch.nn import functional as F
from transformers import AutoModel, AutoTokenizer

from .state import AgentState, Vulnerability

# Regex to strip markdown code blocks from LLM JSON/diff output
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


# ---------------------------------------------------------------------------
# 4.1 Ingestion node
# ---------------------------------------------------------------------------

def ingestion_node(state: AgentState) -> dict[str, Any]:
    """
    Parse webhook payload (diff + file_path, repo/commit) and initialize AgentState.
    Accepts either state.payload (dict) or top-level raw_diff, file_path, repository, commit_sha.
    """
    payload = state.get("payload")
    if isinstance(payload, dict):
        return {
            "raw_diff": payload.get("raw_diff", ""),
            "file_path": payload.get("file_path", ""),
            "repository": payload.get("repository"),
            "commit_sha": payload.get("commit_sha"),
            "original_code": payload.get("original_code"),
        }
    return {
        "raw_diff": state.get("raw_diff", ""),
        "file_path": state.get("file_path", ""),
        "repository": state.get("repository"),
        "commit_sha": state.get("commit_sha"),
        "original_code": state.get("original_code"),
    }


# ---------------------------------------------------------------------------
# LLM (Detector / Remediator)
# ---------------------------------------------------------------------------

def _get_llm(model: str | None = None, temperature: float = 0.1):
    """Chat model for Detector/Remediator. Prefer Claude via env; fallback to OpenAI."""
    model = model or os.getenv("SHIPSAFE_LLM_MODEL", "gpt-4o")
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
    # Prefer Anthropic when configured (typical for Detector/Remediator)
    if os.getenv("ANTHROPIC_API_KEY") and ("claude" in model.lower() or not os.getenv("OPENAI_API_KEY")):
        try:
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model or "claude-sonnet-4-20250514", temperature=temperature)
        except ImportError:
            pass
    return ChatOpenAI(model=model, temperature=temperature, api_key=api_key)


# ---------------------------------------------------------------------------
# CodeBERT scoring (Auditor truth source)
# ---------------------------------------------------------------------------

_CODEBERT_MODEL_NAME = os.getenv("SHIPSAFE_CODEBERT_MODEL", "microsoft/codebert-base")
_codebert_tokenizer: AutoTokenizer | None = None
_codebert_model: AutoModel | None = None


def _get_codebert():
    """Lazy-load CodeBERT encoder used as the Auditor's scorer."""
    global _codebert_tokenizer, _codebert_model
    if _codebert_tokenizer is None or _codebert_model is None:
        _codebert_tokenizer = AutoTokenizer.from_pretrained(_CODEBERT_MODEL_NAME)
        _codebert_model = AutoModel.from_pretrained(_CODEBERT_MODEL_NAME)
        _codebert_model.eval()
    return _codebert_tokenizer, _codebert_model


def _codebert_similarity(a: str, b: str) -> float:
    """Cosine similarity between CLS embeddings of two texts in [0, 1]."""
    tokenizer, model = _get_codebert()
    with torch.no_grad():
        toks_a = tokenizer(a, return_tensors="pt", truncation=True, max_length=256)
        toks_b = tokenizer(b, return_tensors="pt", truncation=True, max_length=256)
        emb_a = model(**toks_a).last_hidden_state[:, 0, :]
        emb_b = model(**toks_b).last_hidden_state[:, 0, :]
        cos = F.cosine_similarity(emb_a, emb_b).item()
    # Map from [-1, 1] to [0, 1]
    return (cos + 1.0) / 2.0


def _score_vulnerabilities_with_codebert(
    vulnerabilities: list[Vulnerability],
    original_code: str,
) -> tuple[float, list[float]]:
    """Return (max_score, per-vulnerability scores) using CodeBERT similarity."""
    scores: list[float] = []
    code_snippet = original_code[:4000] if original_code else ""
    for v in vulnerabilities:
        desc = f"{v.get('type', '')}: {v.get('description', '')}"
        text_v = desc.strip() or "potential vulnerability"
        text_c = code_snippet or "no code"
        score = _codebert_similarity(text_v, text_c)
        scores.append(score)
    return (max(scores) if scores else 0.0), scores


def _score_patch_with_codebert(
    vulnerabilities: list[Vulnerability],
    original_code: str,
    remediation_patch: str,
) -> float:
    """Single score for patch quality based on CodeBERT similarity."""
    vuln_summaries = [
        f"{v.get('type', '')}: {v.get('description', '')}" for v in vulnerabilities
    ]
    vuln_text = "\n".join(vuln_summaries) or "no vulnerabilities"
    before = f"{vuln_text}\n\nOriginal code:\n{original_code[:3000]}"
    after = f"{vuln_text}\n\nPatch:\n{remediation_patch[:3000]}"
    return _codebert_similarity(before, after)


# ---------------------------------------------------------------------------
# Subtask 2.1: Detector
# ---------------------------------------------------------------------------

def _detector_prompt(raw_diff: str, context_chunks: list[str]) -> str:
    context = "\n\n--- Context (relevant code) ---\n".join(context_chunks) if context_chunks else "(none)"
    return f"""Analyze this code change for security vulnerabilities.

Consider: missing auth middleware, unsanitized DB queries, user-controlled input flows, privilege escalation, IDOR, SQL injection, XSS, broken authentication, authorization bypass.

Return a JSON object with a single key "vulnerabilities": a list of objects. Each object must have:
- "type": string (e.g. "SQL Injection", "XSS", "IDOR")
- "line_number": integer
- "description": string
- "confidence_score": float between 0.0 and 1.0

If no vulnerabilities are found, return {{"vulnerabilities": []}}.

--- Diff ---
{raw_diff}

--- Context (relevant code) ---
{context}
"""


def detector_node(state: AgentState) -> dict[str, Any]:
    """
    Input: raw_diff, context_chunks.
    Output: vulnerabilities (list of {type, line_number, description, confidence_score}).
    """
    raw_diff = state.get("raw_diff") or ""
    context_chunks = state.get("context_chunks") or []
    llm = _get_llm()
    prompt = _detector_prompt(raw_diff, context_chunks)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content if hasattr(response, "content") else str(response)
        # Parse JSON from response (handle markdown code blocks)
        text = text.strip()
        if "```" in text:
            match = _JSON_BLOCK_RE.search(text)
            if match:
                text = match.group(1).strip()
        data = json.loads(text)
        vulns = data.get("vulnerabilities", [])
        out: list[Vulnerability] = [
            {
                "type": v.get("type", ""),
                "line_number": int(v.get("line_number", 0)),
                "description": v.get("description", ""),
                "confidence_score": float(v.get("confidence_score", 0.0)),
            }
            for v in vulns
        ]
        return {"vulnerabilities": out}
    except (KeyError, TypeError, ValueError) as e:
        return {"vulnerabilities": [], "analysis_summary": f"Detector parse error: {e}"}


# ---------------------------------------------------------------------------
# Subtask 2.2: Auditor (CodeBERT scorer)
# ---------------------------------------------------------------------------

_CODEBERT_VERIFY_THRESHOLD = 0.55


def auditor_node(state: AgentState) -> dict[str, Any]:
    """
    Input: vulnerabilities, original_code.
    Output: is_verified, audit_feedback.
    """
    vulnerabilities = state.get("vulnerabilities") or []
    original_code = state.get("original_code") or ""
    if not vulnerabilities:
        return {
            "is_verified": False,
            "auditor_confirmed_vulnerable": False,
            "audit_feedback": "No findings to verify.",
        }

    max_score, scores = _score_vulnerabilities_with_codebert(vulnerabilities, original_code)
    is_verified = max_score >= _CODEBERT_VERIFY_THRESHOLD

    parts: list[str] = [f"CodeBERT max score={max_score:.2f} (threshold={_CODEBERT_VERIFY_THRESHOLD:.2f})."]
    for idx, (v, s) in enumerate(zip(vulnerabilities, scores), start=1):
        parts.append(
            f"[#{idx}] {v.get('type', '')}: {v.get('description', '')} -> score={s:.2f}"
        )
    audit_feedback = "\n".join(parts)

    return {
        "is_verified": is_verified,
        "auditor_confirmed_vulnerable": bool(is_verified),
        "audit_feedback": audit_feedback,
    }


# ---------------------------------------------------------------------------
# Subtask 2.3: Remediator
# ---------------------------------------------------------------------------

def _remediator_prompt(vulnerabilities: list[Vulnerability], context_chunks: list[str]) -> str:
    vuln_text = json.dumps(vulnerabilities, indent=2)
    context = "\n\n--- Context ---\n".join(context_chunks) if context_chunks else "(none)"
    return f"""Generate a secure fix as a unified diff (patch). Preserve project style and existing behavior.

Vulnerabilities to fix:
{vuln_text}

Relevant code context:
{context}

Output ONLY a valid unified diff: lines starting with " ", "-", or "+". No explanation before or after. Example:
--- a/file.py
+++ b/file.py
@@ -40,3 +40,4 @@
-query = f"SELECT * FROM users WHERE id = {{user_id}}"
+query = "SELECT * FROM users WHERE id = %s"
+cursor.execute(query, (user_id,))
"""


def remediator_node(state: AgentState) -> dict[str, Any]:
    """
    Input: vulnerabilities, context_chunks.
    Output: remediation_patch (unified-diff string).
    """
    vulnerabilities = state.get("vulnerabilities") or []
    context_chunks = state.get("context_chunks") or []
    if not vulnerabilities:
        return {"remediation_patch": ""}
    llm = _get_llm()
    prompt = _remediator_prompt(vulnerabilities, context_chunks)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content if hasattr(response, "content") else str(response)
        # Normalize: ensure we have a single patch string (strip markdown if present)
        text = text.strip()
        if "```" in text:
            match = re.search(r"```(?:diff)?\s*([\s\S]*?)```", text)
            if match:
                text = match.group(1).strip()
        return {"remediation_patch": text or ""}
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as e:
        return {"remediation_patch": "", "audit_feedback": f"Remediator error: {e}"}


# ---------------------------------------------------------------------------
# Patch verification (self-correction loop: CodeBERT re-validates the fix)
# ---------------------------------------------------------------------------


def patch_audit_node(state: AgentState) -> dict[str, Any]:
    """
    Input: remediation_patch, original_code, vulnerabilities.
    Output: is_verified, audit_feedback (for self-correction loop).
    """
    remediation_patch = state.get("remediation_patch") or ""
    original_code = state.get("original_code") or ""
    vulnerabilities = state.get("vulnerabilities") or []
    if not remediation_patch:
        return {"is_verified": False, "audit_feedback": "No patch to verify."}

    score = _score_patch_with_codebert(vulnerabilities, original_code, remediation_patch)
    is_verified = score >= _CODEBERT_VERIFY_THRESHOLD

    feedback_lines = [
        f"CodeBERT patch score={score:.2f} (threshold={_CODEBERT_VERIFY_THRESHOLD:.2f}).",
        "Patch considered secure." if is_verified else "Patch considered insufficient; needs another remediation iteration.",
    ]
    audit_feedback = "\n".join(feedback_lines)

    out: dict[str, Any] = {"is_verified": is_verified, "audit_feedback": audit_feedback}
    if not is_verified:
        out["iteration_count"] = (state.get("iteration_count") or 0) + 1
    return out

# ---------------------------------------------------------------------------
# 5.2 GitHub Comment node
# ---------------------------------------------------------------------------

def _build_github_comment_body(state: AgentState) -> str:
    """Build PR comment: summary + remediation_patch + reasoning."""
    vulns = state.get("vulnerabilities") or []
    file_path = state.get("file_path", "")
    analysis_summary = state.get("analysis_summary", "")
    remediation_patch = state.get("remediation_patch", "")
    audit_feedback = state.get("audit_feedback", "")

    sections: list[str] = ["## ShipSafe Security Analysis\n"]

    # Summary
    summary_lines = [f"- **File:** `{file_path}`", f"- **Findings:** {len(vulns)} potential vulnerability(ies)"]
    if analysis_summary:
        summary_lines.append(f"- **Notes:** {analysis_summary}")
    for v in vulns:
        summary_lines.append(f"  - {v.get('type', '')}: {v.get('description', '')} (line {v.get('line_number', '?')})")
    sections.append("### Summary\n" + "\n".join(summary_lines))

    # Remediation patch
    if remediation_patch:
        sections.append("### Suggested fix (unified diff)\n```diff\n" + remediation_patch.strip() + "\n```")

    # Reasoning
    if audit_feedback:
        sections.append("### Reasoning\n" + audit_feedback)

    return "\n\n".join(sections)


def github_comment_node(state: AgentState) -> dict[str, Any]:
    """
    Post PR comment with summary + remediation_patch + reasoning.
    Reads repository (owner/repo), pr_number from state; no-op if missing.
    """
    repository = state.get("repository") or ""
    pr_number = state.get("pr_number")
    if not repository or pr_number is None:
        return {}
    parts = repository.split("/", 1)
    owner = parts[0].strip() if parts else ""
    repo = parts[1].strip() if len(parts) > 1 else ""
    if not owner or not repo:
        return {}
    body = _build_github_comment_body(state)
    try:
        from backend.services.github_service import post_pr_comment
        comment_url = post_pr_comment(owner, repo, int(pr_number), body)
        return {"analysis_summary": (state.get("analysis_summary") or "") + f" [Comment: {comment_url}]"}
    except Exception:
        return {}

