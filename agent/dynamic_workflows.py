"""Dynamic workflow orchestration helpers for Hermes agent harness.

Encodes the lane contract (implementer → reviewer → fixer → verifier) and
maps it onto existing primitives: ``delegate_task``, background terminal
jobs, and kanban handoffs.  The LLM sees :data:`DYNAMIC_WORKFLOW_GUIDANCE`
in the system prompt when ``delegate_task`` is loaded; this module is the
testable source of truth for that contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Sequence

ExecutionBackend = Literal["delegate_task", "kanban", "background"]


class WorkflowLane(str, Enum):
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    FIXER = "fixer"
    VERIFIER = "verifier"


_DEFAULT_TOOLSETS: Dict[WorkflowLane, List[str]] = {
    WorkflowLane.IMPLEMENTER: ["terminal", "file"],
    WorkflowLane.REVIEWER: ["file"],
    WorkflowLane.FIXER: ["terminal", "file"],
    WorkflowLane.VERIFIER: ["terminal", "file"],
}

_LANE_ROLE_LINES: Dict[WorkflowLane, str] = {
    WorkflowLane.IMPLEMENTER: (
        "Build the requested artifact. Return absolute paths changed/created, "
        "commands run, and a concise summary."
    ),
    WorkflowLane.REVIEWER: (
        "Read-only audit of the implementer output. List concrete findings "
        "(severity + location). Do NOT modify files."
    ),
    WorkflowLane.FIXER: (
        "Address reviewer findings only. Minimal diff. Re-run affected proof "
        "and return what changed."
    ),
    WorkflowLane.VERIFIER: (
        "Independent proof gate: run tests, read files, stat URLs, or diff "
        "as required. Return pass/fail with evidence handles (exit codes, "
        "paths, command output excerpts)."
    ),
}


@dataclass
class LaneSpec:
    lane: WorkflowLane
    goal: str
    context: str = ""
    toolsets: Optional[List[str]] = None
    role: str = "leaf"
    expected_artifact: str = ""
    verification_gate: str = ""
    blocked_actions: List[str] = field(default_factory=list)


def should_orchestrate_dynamically(user_message: str) -> bool:
    """Heuristic: user likely wants multi-lane orchestration, not linear chat."""
    if not user_message or not user_message.strip():
        return False
    text = user_message.casefold()
    triggers = (
        "workflow",
        "use agents",
        "use a team",
        "multi-stage",
        "orchestrat",
        "implementer",
        "reviewer",
        "verifier",
        "fixer",
        "run the team",
        "lane",
        "review and fix",
        "review/fix",
    )
    return any(t in text for t in triggers)


def choose_execution_backend(
    *,
    outlives_current_turn: bool = False,
    needs_human_review_gate: bool = False,
    parallel_independent_lanes: bool = False,
) -> ExecutionBackend:
    """Pick the primitive that matches workflow durability and shape."""
    if outlives_current_turn or needs_human_review_gate:
        return "kanban"
    if parallel_independent_lanes:
        return "delegate_task"
    return "delegate_task"


def build_lane_context(spec: LaneSpec) -> str:
    """Format lane contract fields for a delegate_task context block."""
    parts: List[str] = []
    if spec.context.strip():
        parts.append(spec.context.strip())
    parts.append(f"Lane role: {spec.lane.value}")
    parts.append(f"Lane contract: {_LANE_ROLE_LINES[spec.lane]}")
    if spec.expected_artifact:
        parts.append(f"Expected artifact: {spec.expected_artifact}")
    if spec.verification_gate:
        parts.append(f"Verification gate: {spec.verification_gate}")
    if spec.blocked_actions:
        blocked = ", ".join(spec.blocked_actions)
        parts.append(f"Blocked actions: {blocked}")
    parts.append(
        "Return a structured summary the parent can verify. Include absolute "
        "paths, command exit codes, and explicit pass/fail for any proof you ran."
    )
    return "\n\n".join(parts)


def lane_to_delegate_task(spec: LaneSpec) -> Dict[str, Any]:
    """Convert one lane spec into a delegate_task batch entry."""
    toolsets = spec.toolsets if spec.toolsets is not None else list(
        _DEFAULT_TOOLSETS[spec.lane]
    )
    entry: Dict[str, Any] = {
        "goal": spec.goal,
        "context": build_lane_context(spec),
        "toolsets": toolsets,
    }
    if spec.role and spec.role != "leaf":
        entry["role"] = spec.role
    return entry


def plan_to_delegate_tasks(specs: Sequence[LaneSpec]) -> List[Dict[str, Any]]:
    """Build a delegate_task ``tasks=[...]`` payload from lane specs."""
    return [lane_to_delegate_task(spec) for spec in specs]


def adoption_gate_checklist() -> str:
    """Proof/adoption gate the parent must apply before trusting lane output."""
    return (
        "Adoption gate (parent responsibility):\n"
        "1. Treat every subagent summary as unverified until you check its artifact.\n"
        "2. Run the lane's verification gate yourself (read file, stat path, rerun test, "
        "fetch URL) or delegate a verifier lane and then re-check its evidence.\n"
        "3. If proof fails, mark the output **unadopted** — spawn a fixer lane or repair "
        "locally; do not tell the user the work succeeded.\n"
        "4. Only adopt and report success after verifier evidence passes or you ran "
        "equivalent proof in the parent thread."
    )


DYNAMIC_WORKFLOW_GUIDANCE = (
    "# Dynamic workflows\n"
    "When the user asks for a workflow, says to use agents/team/lane/review/fix/verify, "
    "or gives a broad objective that naturally splits into independent workstreams, "
    "turn the request into an explicit orchestration plan and execute it instead of "
    "running a single linear chat.\n\n"
    "## Lane roles\n"
    "- **Implementer** — build the artifact; return changed paths and summary.\n"
    "- **Reviewer** — read-only audit; list findings, do not edit.\n"
    "- **Fixer** — address reviewer findings only; minimal diff + re-proof.\n"
    "- **Verifier** — independent proof (tests, reads, diffs, HTTP checks); "
    "return pass/fail with evidence.\n\n"
    "Typical sequence: implementer → reviewer → (fixer if needed) → verifier. "
    "Run independent implementers in parallel via `delegate_task` batch mode.\n\n"
    "## Primitive choice\n"
    "- **`delegate_task`** — synchronous lanes inside the current turn. Give each lane: "
    "objective, source context, allowed/blocked actions, expected artifact, verification gate.\n"
    "- **`terminal(background=True, notify_on_complete=True)`** — long proofs/builds "
    "that must not block the whole turn; poll or wait for notify before adoption.\n"
    "- **`kanban_create` / worker profiles** — durable handoffs, human review gates, "
    "or work that must outlive this session. Do not use `delegate_task` as a board substitute.\n\n"
    f"{adoption_gate_checklist()}\n"
)
