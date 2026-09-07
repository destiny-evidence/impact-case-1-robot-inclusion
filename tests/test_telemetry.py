"""The OTEL_CONFIG env contract, and the span the runner loop emits."""

import asyncio
from typing import TYPE_CHECKING

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.util.config import Settings
from app.util.runner import Runner

if TYPE_CHECKING:
    from destiny_sdk.robots import RobotAutomationIn


def test_otel_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_CONFIG", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.otel_enabled is False
    assert settings.otel_config is None
    # Prompts carry reference abstracts, so span content capture stays opt-in.
    assert settings.otel_capture_llm_content is False


def test_otel_config_parsed_from_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_CONFIG", '{"api_key": "k"}')

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.otel_enabled is True
    assert settings.otel_config is not None
    assert settings.otel_config.api_key == "k"
    # Unspecified endpoint falls back to Honeycomb's US OTLP ingest.
    assert settings.otel_config.trace_endpoint.endswith("/v1/traces")


class _FailingRunner(Runner):
    """Runner whose unit of work always fails, to exercise the error path."""

    def _automation_query(self) -> "RobotAutomationIn":
        raise NotImplementedError

    async def _loop_task(self) -> None:
        raise RuntimeError("boom")


def test_loop_task_span_records_failure() -> None:
    """`_traced_loop_task` emits one `robot.loop` span and marks it errored."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    runner = _FailingRunner(name="query")
    runner.tracer = provider.get_tracer(__name__)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(runner._traced_loop_task())  # noqa: SLF001

    (span,) = exporter.get_finished_spans()
    assert span.name == "robot.loop"
    assert span.attributes is not None
    assert span.attributes["robot.task"] == "query"
    assert span.status.status_code == StatusCode.ERROR


def test_instrument_suppresses_llm_message_content() -> None:
    """LiteLLM's per-request kill switch is set, so prompts stay out of spans."""
    import litellm  # noqa: PLC0415

    from app.util.telemetry import instrument  # noqa: PLC0415

    instrument(capture_llm_content=False)
    assert litellm.turn_off_message_logging is True

    instrument(capture_llm_content=True)
    assert litellm.turn_off_message_logging is False

    # Leave the process in the safe state for any later test.
    instrument(capture_llm_content=False)


def test_configure_telemetry_names_the_service() -> None:
    """service.name is `destiny-<task>-robot-<env>`, and a second call is a no-op."""
    from app.util.config import OTelConfig  # noqa: PLC0415
    from app.util.telemetry import configure_telemetry  # noqa: PLC0415

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        pytest.skip("a tracer provider is already installed globally; configure_telemetry is set-once")

    configure_telemetry(OTelConfig(), task="llm", environment="local", version="0.1.0")

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes["service.name"] == "destiny-llm-robot-local"
    assert provider.resource.attributes["robot.task"] == "llm"

    # Set-once: a second call leaves the first provider in place.
    configure_telemetry(OTelConfig(), task="query", environment="local", version="0.2.0")
    assert trace.get_tracer_provider() is provider
