"""OpenTelemetry tracing.

Spans carry workflow codes, node names and departments only, never patient identity. Export is enabled when
OTEL_EXPORTER_OTLP_ENDPOINT is set and the OTLP exporter package is installed; otherwise the tracer is a no-op.
LangSmith tracing of LLM calls is enabled separately with LANGSMITH_TRACING=true and LANGSMITH_API_KEY; prompts
are already redacted before they reach the model client.
"""
import logging
import os

from opentelemetry import trace

log = logging.getLogger("careflow.telemetry")
tracer = trace.get_tracer("careflow")


def setup_tracing(app=None) -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry-exporter-otlp is not installed; tracing is off")
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "careflow-api"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    log.info("OpenTelemetry tracing to %s", endpoint)
