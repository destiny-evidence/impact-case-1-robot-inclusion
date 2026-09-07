"""OpenTelemetry traces, exported to Honeycomb over OTLP/HTTP."""

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from .config import OTelConfig

logger = logging.getLogger("inclusion-robot.telemetry")

_tracer_provider: TracerProvider | None = None


def configure_telemetry(config: "OTelConfig", task: str, environment: str, version: str) -> None:
    """Install the global tracer provider. Idempotent."""
    global _tracer_provider  # noqa: PLW0603

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return

    service_name = f"destiny-{task}-robot-{environment}"
    headers = {"x-honeycomb-team": config.api_key} if config.api_key else {}
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "destiny",
            "service.version": version,
            "service.instance.id": str(uuid4()),
            "deployment.environment": environment,
            "robot.task": task,
        },
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=config.trace_endpoint, headers=headers, timeout=config.timeout)),
    )
    trace.set_tracer_provider(provider)
    _tracer_provider = provider

    logger.info(f"OpenTelemetry configured (service={service_name}, env={environment})")


def instrument(capture_llm_content: bool = False) -> None:
    """
    Apply auto-instrumentation.

    httpx is instrumented globally, so the repository's sync and async clients and
    LiteLLM's provider calls all get a client span. LiteLLM's `otel` callback reuses
    the provider set above, so its spans carry our resource and reach our exporter.

    LiteLLM would put the full prompt and completion on every span, which
    for these robots means every reference abstract.
    """
    import litellm  # noqa: PLC0415
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # noqa: PLC0415

    HTTPXClientInstrumentor().instrument()
    litellm.turn_off_message_logging = not capture_llm_content
    litellm.callbacks = ["otel"]


def shutdown_telemetry() -> None:
    """Flush and shut down the span processor, so buffered spans survive SIGTERM."""
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
