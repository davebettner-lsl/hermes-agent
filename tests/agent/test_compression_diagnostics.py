"""Tests for compression diagnostics and hygiene_threshold config parsing."""

from unittest.mock import MagicMock

from agent.context_compressor import ContextCompressor
from agent.conversation_compression import (
    build_compression_diagnostics,
    build_context_budget_lines,
    compress_context,
    consume_pending_compression_trigger,
    format_compression_diagnostics_lines,
    parse_hygiene_threshold_pct,
    set_pending_compression_trigger,
    store_compression_diagnostics,
)


class TestParseHygieneThreshold:
    def test_default_when_missing(self):
        assert parse_hygiene_threshold_pct({}) == 0.85
        assert parse_hygiene_threshold_pct(None) == 0.85

    def test_reads_fraction(self):
        assert parse_hygiene_threshold_pct({"hygiene_threshold": 0.9}) == 0.9

    def test_reads_percent_style(self):
        assert parse_hygiene_threshold_pct({"hygiene_threshold": 85}) == 0.85

    def test_invalid_falls_back(self):
        assert parse_hygiene_threshold_pct({"hygiene_threshold": "nope"}) == 0.85


class TestBuildCompressionDiagnostics:
    def test_includes_buckets_and_trigger(self):
        diag = build_compression_diagnostics(
            trigger="gateway_token_hygiene",
            context_length=272_000,
            threshold_tokens=231_200,
            last_prompt_tokens=240_000,
            message_count=120,
            hard_message_limit=400,
            hygiene_threshold_pct=0.85,
            system_prompt="x" * 4000,
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"name": "read_file"}],
        )
        assert diag["trigger"] == "gateway_token_hygiene"
        assert diag["context_length"] == 272_000
        assert diag["estimated_system_prompt_tokens"] > 0
        assert diag["estimated_tool_schema_tokens"] > 0
        assert diag["estimated_message_tokens"] > 0


class TestFormatDiagnosticsLines:
    def test_concise_operational_output(self):
        lines = format_compression_diagnostics_lines({
            "trigger": "gateway_hard_message_count",
            "context_length": 200_000,
            "threshold_tokens": 170_000,
            "last_prompt_tokens": 50_000,
            "message_count": 450,
            "hard_message_limit": 400,
            "estimated_system_prompt_tokens": 12_000,
            "estimated_message_tokens": 30_000,
            "estimated_tool_schema_tokens": 8_000,
        })
        text = "\n".join(lines)
        assert "gateway hard message count" in text
        assert "Budget buckets" in text
        assert "system 12,000" in text
        assert "tools 8,000" in text


class TestPendingTrigger:
    def test_set_and_consume(self):
        agent = MagicMock()
        set_pending_compression_trigger(agent, "manual_compression")
        assert consume_pending_compression_trigger(agent) == "manual_compression"
        assert consume_pending_compression_trigger(agent) == "unknown"


class TestCompressContextRecordsDiagnostics:
    def test_records_trigger_on_compressor(self, monkeypatch):
        comp = ContextCompressor(
            model="test/model",
            threshold_percent=0.5,
            quiet_mode=True,
            config_context_length=100_000,
        )
        agent = MagicMock()
        agent.compression_enabled = True
        agent.context_compressor = comp
        agent.tools = []
        agent._cached_system_prompt = "system"
        agent._compression_feasibility_checked = True
        agent._memory_manager = None
        agent._session_db = None
        agent._todo_store = MagicMock()
        agent._todo_store.format_for_injection.return_value = ""
        agent._build_system_prompt = MagicMock(return_value="system")
        agent._invalidate_system_prompt = MagicMock()
        agent._emit_status = MagicMock()
        agent._emit_warning = MagicMock()
        agent._vprint = MagicMock()
        agent.session_id = "sess1"
        agent.model = "test/model"

        set_pending_compression_trigger(agent, "agent_threshold")

        messages = [
            {"role": "user", "content": f"msg {i}"} for i in range(30)
        ]

        monkeypatch.setattr(
            comp,
            "compress",
            lambda msgs, **kw: msgs[:10],
        )
        monkeypatch.setattr(
            "agent.conversation_compression.check_compression_model_feasibility",
            lambda a: None,
        )

        out, _ = compress_context(agent, messages, "", approx_tokens=60_000)
        assert len(out) == 10
        assert comp.last_compression_diagnostics is not None
        assert comp.last_compression_diagnostics["trigger"] == "agent_threshold"
        assert comp.last_compression_diagnostics["last_prompt_tokens"] == 60_000


class TestContextBudgetLines:
    def test_includes_thresholds_and_buckets(self):
        comp = ContextCompressor(
            model="test/model",
            threshold_percent=0.5,
            quiet_mode=True,
            config_context_length=100_000,
        )
        comp.last_compression_diagnostics = build_compression_diagnostics(
            trigger="preflight_compression",
            context_length=100_000,
            threshold_tokens=50_000,
            last_prompt_tokens=55_000,
            message_count=40,
        )
        agent = MagicMock()
        agent.context_compressor = comp
        lines = build_context_budget_lines(
            agent,
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
            tools=[{"type": "function", "name": "t"}],
            compression_config={"hygiene_threshold": 0.85, "hygiene_hard_message_limit": 400},
        )
        text = "\n".join(lines)
        assert "Context budget" in text
        assert "Gateway hygiene at" in text
        assert "preflight" in text


class TestHermesConfigDefault:
    def test_hygiene_threshold_default_in_schema(self):
        from hermes_cli.config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["compression"]["hygiene_threshold"] == 0.85
