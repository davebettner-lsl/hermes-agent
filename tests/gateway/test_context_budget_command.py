"""Tests for gateway /context-budget and diagnostics on /usage."""

import threading
from unittest.mock import MagicMock, patch

import pytest


SK = "agent:main:discord:channel:99"


def _make_compressor_agent():
    agent = MagicMock()
    agent.model = "anthropic/claude-sonnet-4.6"
    ctx = MagicMock()
    ctx.last_prompt_tokens = 180_000
    ctx.context_length = 272_000
    ctx.threshold_tokens = 136_000
    ctx.threshold_percent = 0.5
    ctx.compression_count = 2
    ctx.last_compression_diagnostics = {
        "trigger": "gateway_token_hygiene",
        "context_length": 272_000,
        "threshold_tokens": 231_200,
        "last_prompt_tokens": 240_000,
        "message_count": 300,
        "hard_message_limit": 400,
        "hygiene_threshold_pct": 0.85,
        "estimated_system_prompt_tokens": 15_000,
        "estimated_message_tokens": 200_000,
        "estimated_tool_schema_tokens": 25_000,
    }
    agent.context_compressor = ctx
    agent._cached_system_prompt = "system prompt"
    agent.tools = [{"name": "read_file"}]
    return agent


def _make_runner(session_key, cached_agent=None):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner.session_store = MagicMock()
    runner._session_key_for_source = MagicMock(return_value=session_key)

    session_entry = MagicMock()
    session_entry.session_id = "sess-1"
    session_entry.last_prompt_tokens = 0
    session_entry.last_compression_diagnostics = None
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = [
        {"role": "user", "content": "hello"},
    ]

    if cached_agent is not None:
        runner._agent_cache[session_key] = (cached_agent, "sig")

    return runner, session_entry


class TestContextBudgetCommand:
    @pytest.mark.asyncio
    async def test_context_budget_shows_live_and_last_compression(self):
        agent = _make_compressor_agent()
        runner, _ = _make_runner(SK, cached_agent=agent)
        event = MagicMock()

        with patch("gateway.run._load_gateway_config", return_value={"compression": {"hygiene_threshold": 0.85, "threshold": 0.5, "hygiene_hard_message_limit": 400}}):
            result = await runner._handle_context_budget_command(event)

        assert "Context budget" in result
        assert "Gateway hygiene at" in result
        assert "gateway token hygiene" in result
        assert "Budget buckets" in result

    @pytest.mark.asyncio
    async def test_usage_includes_compression_diagnostics(self):
        agent = _make_compressor_agent()
        agent.session_total_tokens = 50_000
        agent.session_api_calls = 3
        agent.session_input_tokens = 40_000
        agent.session_output_tokens = 10_000
        agent.session_cache_read_tokens = 0
        agent.session_cache_write_tokens = 0
        agent.get_rate_limit_state.return_value = MagicMock(has_data=False)

        runner, _ = _make_runner(SK, cached_agent=agent)
        event = MagicMock()

        with patch("agent.usage_pricing.estimate_usage_cost") as mock_cost:
            mock_cost.return_value = MagicMock(amount_usd=None, status="unknown")
            result = await runner._handle_usage_command(event)

        assert "gateway token hygiene" in result
        assert "Budget buckets" in result


class TestHygieneThresholdConfig:
    def test_gateway_hygiene_reads_config_threshold(self):
        from agent.conversation_compression import parse_hygiene_threshold_pct

        cfg = {"hygiene_threshold": 0.9, "hygiene_hard_message_limit": 500}
        assert parse_hygiene_threshold_pct(cfg) == 0.9
