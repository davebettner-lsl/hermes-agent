# Hermes Context Window Optimization Plan

**As of:** 2026-05-25

## Conclusion

Recommended path: keep `gpt-5.5` on `openai-codex`, but treat its effective window as **272K in Codex OAuth**, not the larger direct OpenAI API window. First fix the operating profile with config and observability, then let Cursor make small code changes only where the current knobs cannot prove impact.

Do not start with model shopping. The local code already has the right core machinery: explicit `model.context_length`, configurable compression threshold, configurable preserved head/tail, gateway hygiene limits, runtime context footer, and provider-specific context discovery. The likely problem is payload bloat plus aggressive preserved context, not missing compression code.

## File-Backed Findings

Hermes compression uses `ContextCompressor` as the main path. It computes:

- `context_length` from `get_model_context_length(...)`
- `threshold_tokens = max(context_length * compression.threshold, 64000)`
- `tail_token_budget = threshold_tokens * compression.target_ratio`
- compression fires when `prompt_tokens >= threshold_tokens`

Evidence:
- `agent/context_compressor.py:512-564` constructs the compressor and computes threshold/tail budgets.
- `agent/context_compressor.py:614-634` fires compression at `tokens >= threshold_tokens`, with anti-thrashing after ineffective compressions.
- `agent/agent_init.py:1205-1231` reads `compression.threshold`, `target_ratio`, `protect_last_n`, `protect_first_n`, and `abort_on_summary_failure`.
- `agent/agent_init.py:1435-1449` passes those config values into `ContextCompressor`.

Hermes estimates full request pressure, not just chat messages. Tool schemas are explicitly included because schemas alone can add **20-30K tokens**.

Evidence:
- `agent/model_metadata.py:1807-1828` defines `estimate_request_tokens_rough(messages, system_prompt, tools)` and counts system prompt, messages, and tools.
- `agent/conversation_loop.py:467-505` performs preflight compression before the LLM call using `estimate_request_tokens_rough(..., system_prompt=..., tools=agent.tools)`.
- `agent/conversation_loop.py:3522-3559` uses API-reported `prompt_tokens` when available, otherwise rough request estimate, then calls `_compress_context`.
- `agent/conversation_compression.py:454-465` recomputes post-compression prompt pressure including tool schemas.

For `openai-codex`, local code says Codex OAuth has a smaller enforced window than direct OpenAI API.

Evidence:
- `plugins/model-providers/openai-codex/__init__.py:6-13` defines provider `openai-codex`, `api_mode="codex_responses"`, and base URL `https://chatgpt.com/backend-api/codex`.
- `agent/model_metadata.py:1240-1262` documents Codex OAuth fallback context windows; `gpt-5.5` is `272_000`.
- `agent/model_metadata.py:1323-1353` resolves Codex OAuth context from the live Codex `/models` endpoint, then fallback table.
- `agent/model_metadata.py:1641-1649` uses that Codex OAuth branch before generic defaults.
- `agent/model_metadata.py:1430-1464` supports explicit `model.context_length` override before all probes.

Gateway has a second compression safety path. It is intentionally higher than agent compression and also has a message-count hard limit.

Evidence:
- `gateway/run.py:8237-8245` sets gateway hygiene threshold to `0.85`, separate from agent `compression.threshold`.
- `gateway/run.py:8274-8288` reads `compression.enabled` and `compression.hygiene_hard_message_limit`.
- `gateway/run.py:8333-8378` resolves context length, prefers `last_prompt_tokens`, falls back to message estimate, and forces compression when tokens exceed 85% or messages exceed the hard limit.
- `gateway/session.py:453-454` stores `last_prompt_tokens` for compression pre-checks.
- `gateway/run.py:8944-8950` persists `last_prompt_tokens` after each agent result.

The system prompt is a real part of the budget. Current Hermes prompt building includes skills and project context.

Evidence:
- `agent/prompt_builder.py:997-1014` builds a compact skill index and caches it.
- `agent/prompt_builder.py:1193-1214` injects mandatory skills guidance plus available skill index.
- `agent/prompt_builder.py:1427-1437` loads one project context source, priority `.hermes.md` / `HERMES.md`, then `AGENTS.md`, then `CLAUDE.md`, then Cursor rules, capped at 20,000 chars.
- `toolsets.py:31-73` core tools include web, terminal, file, vision, skills, browser, TTS, planning/memory, session search, clarify, execute/delegate, cron, messaging, Home Assistant, kanban, and computer use. More enabled tools means larger schema payload.

## Why This Session May Compact Too Often

Most likely causes, ranked:

1. **Effective Codex window is 272K.** With `compression.threshold: 0.8`, agent compression fires around `217,600` prompt tokens. That is not much room if the system prompt, AGENTS.md, skill index, and tool schemas are large.
2. **Huge invariant prompt prefix.** AGENTS.md plus skills plus broad tool schema surface consume context every turn before user content starts.
3. **`protect_last_n: 20` can preserve too much raw tail.** For Discord tool-heavy sessions, 20 messages may include large tool results and multi-turn code/tool exchanges. Compression saves less, causing repeated compactions.
4. **`target_ratio: 0.2` with threshold 217.6K preserves a tail budget around 43.5K plus minimum 20 messages.** The minimum message count can defeat the intended token budget.
5. **Gateway hard message limit is 400.** Tool-heavy Discord sessions can hit the count limit even before token pressure requires compression.
6. **Aux compression model must fit the content being summarized.** Current redacted config uses OpenRouter `google/gemini-3-flash-preview`; local code can auto-lower threshold if auxiliary context is too small, but the actual current OpenRouter metadata must be verified live before relying on it.

## Ranked Options

### 1. Quick Config Changes

Use this first. No code risk.

Recommended trial config:

```yaml
model:
  context_length: 272000

compression:
  threshold: 0.85
  target_ratio: 0.12
  protect_first_n: 1
  protect_last_n: 8
  hygiene_hard_message_limit: 800
  abort_on_summary_failure: true

display:
  runtime_footer:
    enabled: true
    fields: ["model", "context_pct", "cwd"]
```

Rationale:
- `model.context_length: 272000` pins Codex OAuth reality and makes all math explicit.
- `threshold: 0.85` delays agent compaction to roughly `231K`; still below the true window.
- `target_ratio: 0.12` makes each compaction more effective.
- `protect_last_n: 8` keeps recent flow without pinning too many tool messages.
- `protect_first_n: 1` preserves the system prompt plus first non-system anchor, not a large head.
- `hygiene_hard_message_limit: 800` prevents message-count compression from firing too early in Discord.
- `runtime_footer.enabled: true` gives visible context percent proof.

Do not set `compression.enabled: false`. That trades annoyance for context-limit failures.

### 2. Medium Code Changes

Use Cursor only after the config trial proves which trigger is firing.

Candidate changes:

- Add compression diagnostics to `/usage` or a gateway status line:
  - current `context_length`
  - `threshold_tokens`
  - `last_prompt_tokens`
  - estimated system prompt tokens
  - estimated tool schema tokens
  - history message count
  - last compression reason: agent threshold, gateway token hygiene, gateway hard message count, manual

- Add a one-shot debug command, for example `/context-budget`, that runs the same estimator used by preflight and reports budget buckets. This avoids guessing whether AGENTS.md, skills, or tools are the main offender.

- Add config for gateway hygiene threshold percent instead of hardcoded `0.85`, with default unchanged.

- Add per-platform toolset presets for Discord so routine chat does not expose the full core schema surface by default.

### 3. Bigger Architecture

Use only if compactions remain frequent after evidence-driven tuning.

- Make skills index lazy or narrower: keep the `skills_list` / `skill_view` tools, but reduce mandatory always-in-system skill text for gateway sessions.
- Split gateway modes: `discord-research`, `discord-builder`, `discord-home`, each with smaller toolsets.
- Add prompt-prefix budget accounting at startup and warn when fixed overhead exceeds a configurable percent of context.
- Consider a larger-context route only if the provider proves the model has a larger effective window in the actual route. Direct OpenAI API windows do not automatically apply to `openai-codex`.

## Cursor Execution Tasks

### Task 1: Config-Only Trial

Allowed files:
- User config only, outside this repo, if Dave approves Cursor touching it.

Blocked files:
- `~/.hermes/.env`
- auth files
- keychains
- repo source files

Work:
- Apply the quick config values above.
- Restart gateway.
- Run a normal Discord session until at least one prior compaction point.
- Capture whether compression is triggered by agent threshold, gateway token hygiene, or gateway hard message count.

Proof commands:

```bash
hermes config get model.context_length
hermes config get compression
hermes config get display.runtime_footer
hermes status
```

Expected proof:
- Context footer visible in Discord replies.
- Fewer compactions before the same workload point.
- No context-limit errors.
- No summary-loss warning.

### Task 2: Observability Patch

Allowed files:
- `gateway/run.py`
- `gateway/session.py`
- `gateway/runtime_footer.py`
- `agent/conversation_loop.py`
- `agent/conversation_compression.py`
- `tests/gateway/*`
- `tests/agent/*`
- `tests/run_agent/*`

Blocked files:
- `~/.hermes/.env`
- auth files
- keychains
- provider token stores
- unrelated formatting/refactor files

Work:
- Add structured compression-reason metadata where compression happens.
- Surface it in `/usage` or a new `/context-budget` command.
- Include token buckets: system prompt, tools, messages, context length, threshold, hard message limit.
- Keep output concise for Discord.

Proof commands:

```bash
./scripts/run_tests.sh tests/gateway/test_usage_command.py tests/gateway/test_compress_command.py tests/run_agent/test_context_compressor.py
python -m pytest tests/gateway/test_usage_command.py -q
```

Expected proof:
- Tests cover agent-threshold compression.
- Tests cover gateway hygiene token compression.
- Tests cover gateway hard-message compression.
- Command output identifies the trigger without reading secrets.

### Task 3: Toolset / Prompt Trim

Allowed files:
- `toolsets.py`
- `model_tools.py`
- `agent/prompt_builder.py`
- `hermes_cli/config.py`
- related tests

Blocked files:
- provider auth code unless directly needed
- secrets/auth files
- broad unrelated prompt rewrites

Work:
- Add a Discord-safe default or documented profile that disables rarely used schemas in ordinary Discord chat.
- Preserve `read_file`, `search_files`, `patch`, `terminal`, `web_search`, `skills_list`, `skill_view`, `memory`, `session_search`, and `clarify` unless Dave explicitly chooses a narrower mode.
- Measure schema-token reduction using `estimate_request_tokens_rough`.

Proof commands:

```bash
./scripts/run_tests.sh tests
python - <<'PY'
from model_tools import get_tool_definitions
from agent.model_metadata import estimate_request_tokens_rough
tools = get_tool_definitions(quiet_mode=True)
print(len(tools), estimate_request_tokens_rough([], tools=tools))
PY
```

Expected proof:
- Tool schema token count drops materially.
- No duplicate tool names.
- Gateway still handles normal Discord tasks.

## Risks

- Raising thresholds without reducing tail can delay compression but still preserve too much raw context after each compaction.
- Lowering `protect_last_n` too far can lose immediate operational detail in active tool loops.
- Pinning `model.context_length` above Codex OAuth reality risks provider context errors.
- OpenRouter model metadata may drift; verify `auxiliary.compression.model` context before depending on it.
- `abort_on_summary_failure: true` is safer for data integrity but can freeze long sessions if the aux model is flaky.

## Acceptance Criteria

- A comparable Discord workload reaches at least 2x more useful turns before compaction, or compaction count drops by at least 50%.
- No context-window provider errors.
- No silent dropped-summary path.
- Context footer or `/context-budget` proves current context percent and compression trigger.
- Plan is executed by Cursor with no secret reads and no unrelated repo edits.

## Execution notes (2026-05-25, Cursor)

Medium observability path implemented:

- `agent/conversation_compression.py`: diagnostics builders, `compression.hygiene_threshold` parser (default 0.85), pending trigger helpers, `/context-budget` line formatter.
- Triggers recorded: `agent_threshold`, `preflight_compression`, `gateway_token_hygiene`, `gateway_hard_message_count`, `manual_compression`.
- Gateway: configurable `compression.hygiene_threshold`; `/context-budget` command; `/usage` appends last compression block.
- `hermes_cli/config.py`: default `hygiene_threshold: 0.85`.
- Tests: `tests/agent/test_compression_diagnostics.py`, `tests/gateway/test_context_budget_command.py` (plus existing `test_usage_command.py` still green).
