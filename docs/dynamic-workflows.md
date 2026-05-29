# Dynamic workflows

Source signals:

- X post `2060054180379689074` from `_catwu`: Claude Code dynamic workflows — mention "workflow" and the agent creates an orchestration plan, kicks off many tasks, runs implementer/verifier/fixer stages, and returns when done.
- X post `2060069083878408689` from `mfpiccolo`: article titled "How to build your own agent harness???" — strong signal that the harness itself matters, not only prompt wording.

## Target behavior

When a user asks for a workflow, says to use agents/team/lane/review/fix/verify, or gives a broad objective that naturally splits into independent workstreams, Hermes should not behave like a single linear chat. It should:

1. Draft a concise orchestration plan.
2. Split independent work into specialized lanes.
3. Run implementer/reviewer/verifier lanes with `delegate_task` batch mode when synchronous and bounded.
4. Use background terminal, cron, or kanban when the workflow must outlive the current turn.
5. Adopt lane outputs only after checking their artifacts or proof.
6. Run a fixer lane or local fix pass for failed verifications.
7. Return a short proof-first summary when done.

## First implementation slice (shipped)

Prompt/runtime guidance plus a testable planning module:

- `agent/dynamic_workflows.py` — lane types (`implementer`, `reviewer`, `fixer`, `verifier`), `LaneSpec`, `plan_to_delegate_tasks()`, adoption gate text, and backend selection helper.
- `agent/system_prompt.py` — injects `DYNAMIC_WORKFLOW_GUIDANCE` when `delegate_task` is in `valid_tool_names`.
- `tests/agent/test_dynamic_workflows.py` — unit tests for lane planning and guidance content.

This is intentionally small and cache-stable:

- no new runtime state
- no new schema surface
- no brittle keyword router
- no prompt-cache invalidation per user turn

## Next product slice

Add an explicit `workflow` tool or slash command that turns a goal into a typed plan object:

```json
{
  "goal": "",
  "mode": "sync|background|kanban",
  "lanes": [
    {
      "name": "implementer",
      "objective": "",
      "allowed_actions": [],
      "blocked_actions": [],
      "expected_artifact": "",
      "verification_gate": ""
    }
  ],
  "adoption_gate": ""
}
```

That tool should be a harness around existing primitives, not a parallel orchestration stack. It should call `delegate_task` for bounded sync lanes and create kanban/background jobs for durable lanes.

## Verification gates

- System prompt includes dynamic workflow guidance only when `delegate_task` is available.
- Prompt does not include the block when delegation is unavailable.
- Existing prompt-cache stability rules remain intact because the block is stable for the lifetime of the agent instance.
