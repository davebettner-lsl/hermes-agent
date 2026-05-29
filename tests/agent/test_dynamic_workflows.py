"""Tests for agent/dynamic_workflows.py — lane planning and adoption gates."""

from agent.dynamic_workflows import (
    DYNAMIC_WORKFLOW_GUIDANCE,
    LaneSpec,
    WorkflowLane,
    adoption_gate_checklist,
    build_lane_context,
    choose_execution_backend,
    lane_to_delegate_task,
    plan_to_delegate_tasks,
    should_orchestrate_dynamically,
)


class TestShouldOrchestrateDynamically:
    def test_workflow_trigger(self):
        assert should_orchestrate_dynamically("Set up a workflow for this refactor")

    def test_lane_trigger(self):
        assert should_orchestrate_dynamically("Use implementer and verifier lanes")

    def test_simple_question_false(self):
        assert not should_orchestrate_dynamically("What is 2+2?")

    def test_empty_false(self):
        assert not should_orchestrate_dynamically("")


class TestChooseExecutionBackend:
    def test_durable_work_uses_kanban(self):
        assert (
            choose_execution_backend(outlives_current_turn=True)
            == "kanban"
        )

    def test_human_review_gate_uses_kanban(self):
        assert (
            choose_execution_backend(needs_human_review_gate=True)
            == "kanban"
        )

    def test_parallel_lanes_use_delegate(self):
        assert (
            choose_execution_backend(parallel_independent_lanes=True)
            == "delegate_task"
        )


class TestLanePlanning:
    def test_build_lane_context_includes_contract(self):
        spec = LaneSpec(
            lane=WorkflowLane.REVIEWER,
            goal="Review auth module",
            context="Repo at /tmp/app",
            expected_artifact="src/auth/*.py",
            verification_gate="List findings only",
            blocked_actions=["write files", "run destructive commands"],
        )
        ctx = build_lane_context(spec)
        assert "Lane role: reviewer" in ctx
        assert "Repo at /tmp/app" in ctx
        assert "Expected artifact: src/auth/*.py" in ctx
        assert "Do NOT modify files" in ctx
        assert "Blocked actions: write files" in ctx

    def test_lane_to_delegate_task_defaults_toolsets(self):
        spec = LaneSpec(
            lane=WorkflowLane.IMPLEMENTER,
            goal="Add health check endpoint",
        )
        task = lane_to_delegate_task(spec)
        assert task["goal"] == "Add health check endpoint"
        assert task["toolsets"] == ["terminal", "file"]
        assert "Lane role: implementer" in task["context"]

    def test_plan_to_delegate_tasks_preserves_order(self):
        specs = [
            LaneSpec(lane=WorkflowLane.IMPLEMENTER, goal="Build"),
            LaneSpec(lane=WorkflowLane.VERIFIER, goal="Prove"),
        ]
        tasks = plan_to_delegate_tasks(specs)
        assert len(tasks) == 2
        assert tasks[0]["goal"] == "Build"
        assert tasks[1]["goal"] == "Prove"


class TestDynamicWorkflowGuidance:
    def test_guidance_covers_all_lane_roles(self):
        for lane in ("Implementer", "Reviewer", "Fixer", "Verifier"):
            assert f"**{lane}**" in DYNAMIC_WORKFLOW_GUIDANCE

    def test_guidance_mentions_primitives(self):
        assert "delegate_task" in DYNAMIC_WORKFLOW_GUIDANCE
        assert "kanban_create" in DYNAMIC_WORKFLOW_GUIDANCE
        assert "notify_on_complete" in DYNAMIC_WORKFLOW_GUIDANCE

    def test_adoption_gate_in_guidance(self):
        assert "unadopted" in DYNAMIC_WORKFLOW_GUIDANCE
        assert adoption_gate_checklist() in DYNAMIC_WORKFLOW_GUIDANCE
