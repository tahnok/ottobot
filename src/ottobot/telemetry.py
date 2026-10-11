"""OpenTelemetry tracing for Ottobot, exported to Honeycomb.

Tracing is off unless the config (or the environment) supplies a Honeycomb
API key::

    [telemetry]
    honeycomb_api_key = "hcaik_..."   # or the HONEYCOMB_API_KEY env var
    service_name = "ottobot"          # defaults to the bot's name
    endpoint = "https://api.honeycomb.io"   # EU: https://api.eu1.honeycomb.io

With no key configured ``start_telemetry`` does nothing and the spans the
rest of the code records fall through to the API's no-op tracer, so the
bot behaves exactly as it did before.

Spans are emitted for each incoming message (``dispatch``), each sink and
command handler it runs, each scheduled task run, each transmission to
the device, and each packet the radio hears (``rx packet``, with its
Beacon packet hash, route, SNR and RSSI); outgoing HTTP calls made with httpx (weather, hydro, Discord)
are instrumented automatically, so a command's API calls show up as
children of the message that triggered them.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Tracer

from .config import BotConfig, TelemetryConfig

logger = logging.getLogger(__name__)

# Honeycomb's OTLP/HTTP ingest. The EU instance is api.eu1.honeycomb.io.
HONEYCOMB_ENDPOINT = "https://api.honeycomb.io"
TRACES_PATH = "/v1/traces"

# Honeycomb authenticates with the team key in this header; the dataset a
# span lands in comes from its service name.
TEAM_HEADER = "x-honeycomb-team"

# Read when the config leaves honeycomb_api_key unset, so a deployment can
# pass the key as a secret instead of writing it into ottobot.toml.
API_KEY_ENV = "HONEYCOMB_API_KEY"

# Instrumentation scope name; shows up on every span as library.name.
INSTRUMENTATION_NAME = "ottobot"

# How long one export attempt (retries included) may take. This also caps
# how long the flush in shutdown_telemetry() can hold up exiting: the SDK
# takes no timeout for shutdown and ignores the one force_flush is given,
# so the exporter's own deadline is what keeps a Ctrl-C from hanging on an
# unreachable collector.
EXPORT_TIMEOUT_SECONDS = 3

# Span attribute keys the httpx instrumentation records the request URL
# under — which one it uses depends on the semantic-convention mode it runs
# in, so a redacted URL is written to both (see _redact_url_hook).
URL_ATTRIBUTES = ("http.url", "url.full")

REDACTED = "<redacted>"

# The provider installed by start_telemetry, kept so shutdown_telemetry can
# flush it. None when tracing was never started.
_provider: TracerProvider | None = None

# Set once a provider has been installed globally. OpenTelemetry refuses to
# replace an installed provider, so tracing cannot be restarted after a
# shutdown: a second provider would never be reached and every span would
# be dropped silently. start_telemetry declines instead.
_started = False


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
    """Full OTLP/HTTP traces URL to POST spans to.

    Takes either the bare ingest host (Honeycomb's documented form) or an
    endpoint that already names the traces path, so the standard OTLP
    spelling doesn't end up posting to "/v1/traces/v1/traces" and 404ing
    every batch.
    """
    base = (config.endpoint or HONEYCOMB_ENDPOINT).rstrip("/")
    if base.endswith(TRACES_PATH):
        return base
    return f"{base}{TRACES_PATH}"


def secret_values(config: BotConfig) -> tuple[str, ...]:
    """Config values that must never reach a span.

    The httpx instrumentation records the full request URL, and a Discord
    webhook URL *is* a credential — its path carries a token that lets
    anyone holding it post to the channel. Any future secret in the config
    belongs in this list.
    """
    return tuple(
        value
        for value in (config.discord_webhook_url, api_key(config.telemetry))
        if value
    )


def _stand_in_for(secret: str) -> str:
    """What to show in place of *secret*: its origin, if it is a URL."""
    parts = urlsplit(secret)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}/{REDACTED}"
    return REDACTED


def redact(url: str, secrets: Iterable[str]) -> str:
    """*url* with any secret it contains replaced by a stand-in."""
    for secret in secrets:
        if secret in url:
            url = url.replace(secret, _stand_in_for(secret))
    return url


def _redact_url_hook(secrets: tuple[str, ...]) -> Callable[[Span, Any], None]:
    """An httpx request hook that keeps secrets out of the URL attribute.

    The instrumentation redacts only an URL's userinfo, so a secret in the
    path (the Discord webhook token) would otherwise be exported verbatim.
    Both attribute names are overwritten, rather than only the one already
    on the span, so a secret can't survive a change of semconv mode.
    """

    def hook(span: Span, request: Any) -> None:
        url = str(request.url)
        redacted = redact(url, secrets)
        if redacted == url:
            return
        for key in URL_ATTRIBUTES:
            span.set_attribute(key, redacted)

    return hook


def instrument_httpx(provider: TracerProvider, config: BotConfig) -> None:
    """Trace outgoing httpx calls on *provider*, with secrets redacted."""
    hook = _redact_url_hook(secret_values(config))

    async def async_hook(span: Span, request: Any) -> None:
        hook(span, request)

    HTTPXClientInstrumentor().instrument(
        tracer_provider=provider,
        request_hook=hook,
        async_request_hook=async_hook,
    )


def build_exporter(config: TelemetryConfig, key: str) -> OTLPSpanExporter:
    """The OTLP/HTTP exporter that posts spans to Honeycomb as *key*'s team."""
    return OTLPSpanExporter(
        endpoint=traces_endpoint(config),
        headers={TEAM_HEADER: key},
        timeout=EXPORT_TIMEOUT_SECONDS,
    )


def build_tracer_provider(config: BotConfig, bot_name: str) -> TracerProvider | None:
    """A provider exporting spans to Honeycomb, or None if no key is set.

    *bot_name* is the name the bot is actually running under, which is the
    service name (and so the Honeycomb dataset) unless the config names one
    explicitly.
    """
    telemetry = config.telemetry
    key = api_key(telemetry)
    if not key:
        return None
    resource = Resource.create({"service.name": telemetry.service_name or bot_name})
    provider = TracerProvider(resource=resource)
    # Batched: exporting is an HTTP round trip and handlers run on the
    # bot's single event loop, so spans are shipped from a background
    # thread rather than inline with message handling.
    provider.add_span_processor(BatchSpanProcessor(build_exporter(telemetry, key)))
    return provider


def start_telemetry(config: BotConfig, bot_name: str) -> bool:
    """Install the tracer provider and instrument httpx.

    Call this once the bot's name is known, since that name is the service
    the spans are attributed to. Returns whether tracing was started;
    False (and nothing changed) when no Honeycomb key is configured.
    """
    global _provider, _started
    if _provider is not None:
        return True
    if _started:
        logger.warning(
            "telemetry was already shut down; not restarting it, because the "
            "tracer provider cannot be replaced and the spans would be lost"
        )
        return False
    provider = build_tracer_provider(config, bot_name)
    if provider is None:
        logger.debug("telemetry disabled: no Honeycomb API key configured")
        return False
    trace.set_tracer_provider(provider)
    instrument_httpx(provider, config)
    _provider = provider
    _started = True
    logger.info(
        "telemetry enabled as %r: sending traces to %s",
        config.telemetry.service_name or bot_name,
        traces_endpoint(config.telemetry),
    )
    return True


def shutdown_telemetry() -> None:
    """Flush and stop the exporter, so spans aren't lost when the bot exits.

    Bounded by EXPORT_TIMEOUT_SECONDS per batch: a collector that has gone
    away delays the exit by that much rather than by the SDK's 30 seconds.
    """
    global _provider
    if _provider is None:
        return
    provider, _provider = _provider, None
    HTTPXClientInstrumentor().uninstrument()
    provider.shutdown()
