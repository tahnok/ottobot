"""OpenTelemetry tracing for Ottobot, exported to Honeycomb.

Tracing is off unless the config (or the environment) supplies a Honeycomb
API key::

    [telemetry]
    honeycomb_api_key = "hcaik_..."   # or the HONEYCOMB_API_KEY env var
    dataset = "ottobot"               # only needed for Classic keys
    service_name = "ottobot"          # defaults to the bot's name
    endpoint = "https://api.honeycomb.io"   # EU: https://api.eu1.honeycomb.io

With no key configured ``start_telemetry`` does nothing and the spans the
rest of the code records fall through to the API's no-op tracer, so the
bot behaves exactly as it did before.

Spans are emitted for each incoming message (``dispatch``), each sink and
command handler it runs, each scheduled task run, and each transmission to
the device; outgoing HTTP calls made with httpx (weather, hydro, Discord)
are instrumented automatically, so a command's API calls show up as
children of the message that triggered them.
"""

from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError, version

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

from .config import BotConfig, TelemetryConfig

logger = logging.getLogger(__name__)

# Honeycomb's OTLP/HTTP ingest. The EU instance is api.eu1.honeycomb.io.
HONEYCOMB_ENDPOINT = "https://api.honeycomb.io"
TRACES_PATH = "/v1/traces"

# Honeycomb authenticates with the team key in this header. Classic keys
# (32 hex chars) additionally need the dataset named in x-honeycomb-dataset;
# newer keys carry the dataset in the service name instead.
TEAM_HEADER = "x-honeycomb-team"
DATASET_HEADER = "x-honeycomb-dataset"

# Read when the config leaves honeycomb_api_key unset, so a deployment can
# pass the key as a secret instead of writing it into ottobot.toml.
API_KEY_ENV = "HONEYCOMB_API_KEY"

# Instrumentation scope name; shows up on every span as library.name.
INSTRUMENTATION_NAME = "ottobot"

# The provider installed by start_telemetry, kept so shutdown_telemetry can
# flush it. None when tracing was never started.
_provider: TracerProvider | None = None


def tracer() -> Tracer:
    """The tracer handlers record spans on.

    Safe to call before (or without) start_telemetry: the API hands back a
    tracer that resolves the provider lazily, so spans recorded while
    tracing is off are no-ops.
    """
    return trace.get_tracer(INSTRUMENTATION_NAME)


def api_key(config: TelemetryConfig) -> str | None:
    """The Honeycomb key from the config, falling back to the environment."""
    return config.honeycomb_api_key or os.environ.get(API_KEY_ENV) or None


def traces_endpoint(config: TelemetryConfig) -> str:
    """Full OTLP/HTTP traces URL to POST spans to."""
    base = (config.endpoint or HONEYCOMB_ENDPOINT).rstrip("/")
    return f"{base}{TRACES_PATH}"


def headers(config: TelemetryConfig, key: str) -> dict[str, str]:
    """Honeycomb's auth headers; the dataset one only when it's configured."""
    sent = {TEAM_HEADER: key}
    if config.dataset:
        sent[DATASET_HEADER] = config.dataset
    return sent


def _service_version() -> str:
    try:
        return version("ottobot")
    except PackageNotFoundError:  # running from a source tree, not installed
        return "unknown"


def build_tracer_provider(config: BotConfig) -> TracerProvider | None:
    """A provider exporting spans to Honeycomb, or None if no key is set."""
    telemetry = config.telemetry
    key = api_key(telemetry)
    if not key:
        return None
    service_name = telemetry.service_name or config.name or "ottobot"
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": _service_version(),
        }
    )
    exporter = OTLPSpanExporter(
        endpoint=traces_endpoint(telemetry),
        headers=headers(telemetry, key),
    )
    provider = TracerProvider(resource=resource)
    # Batched: exporting is an HTTP round trip and handlers run on the
    # bot's single event loop, so spans are shipped from a background
    # thread rather than inline with message handling.
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def start_telemetry(config: BotConfig) -> bool:
    """Install the tracer provider and instrument httpx.

    Returns whether tracing was started; False (and nothing changed) when
    no Honeycomb key is configured.
    """
    global _provider
    if _provider is not None:
        return True
    provider = build_tracer_provider(config)
    if provider is None:
        logger.debug("telemetry disabled: no Honeycomb API key configured")
        return False
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    _provider = provider
    logger.info(
        "telemetry enabled: sending traces to %s",
        traces_endpoint(config.telemetry),
    )
    return True


def shutdown_telemetry() -> None:
    """Flush and stop the exporter, so spans aren't lost when the bot exits."""
    global _provider
    if _provider is None:
        return
    provider, _provider = _provider, None
    HTTPXClientInstrumentor().uninstrument()
    provider.shutdown()
