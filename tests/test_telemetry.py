"""Tests for the OpenTelemetry wiring and the spans handlers produce."""

import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, NamedTuple

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import StatusCode

from helpers import ReplyRecorder, addressed, channel_msg
from ottobot import Context, Ottobot, TaskContext
from ottobot.channels import PUBLIC, ChannelConfig
from ottobot.config import BotConfig, TelemetryConfig, parse_config
from ottobot import telemetry as telemetry_module
from ottobot.runner import MeshCoreRunner
from ottobot.sinks.discord import post_to_discord
from ottobot.telemetry import (
    API_KEY_ENV,
    EXPORT_TIMEOUT_SECONDS,
    REDACTED,
    TEAM_HEADER,
    URL_ATTRIBUTES,
    api_key,
    build_exporter,
    build_tracer_provider,
    instrument_httpx,
    redact,
    secret_values,
    start_telemetry,
    traces_endpoint,
)

# The fake device from the runner tests, reused here to trace real sends.
from test_runner import FakeMeshCore

# The tracer provider can only be installed once per process, so it is
# built lazily and shared; each test clears the exporter first.
_EXPORTER: InMemorySpanExporter | None = None
_PROVIDER: TracerProvider | None = None


@pytest.fixture
def spans() -> Generator[InMemorySpanExporter]:
    global _EXPORTER, _PROVIDER
    if _EXPORTER is None:
        _EXPORTER = InMemorySpanExporter()
        _PROVIDER = TracerProvider()
        _PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(_PROVIDER)
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


def installed_provider() -> TracerProvider:
    """The provider the spans fixture installed."""
    assert _PROVIDER is not None
    return _PROVIDER


class Received(NamedTuple):
    path: str
    headers: dict[str, str]
    body: bytes


@contextmanager
def recording_server() -> Iterator[tuple[str, list[Received]]]:
    """A loopback HTTP server that answers 200 and records what it got."""
    received: list[Received] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            received.append(
                Received(self.path, dict(self.headers), self.rfile.read(length))
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass  # keep the test output quiet

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", received
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def named(spans: InMemorySpanExporter, name: str) -> ReadableSpan:
    """The single finished span called *name*."""
    matches = [s for s in spans.get_finished_spans() if s.name == name]
    assert len(matches) == 1, [s.name for s in spans.get_finished_spans()]
    return matches[0]


def attributes(span: ReadableSpan) -> dict:
    return dict(span.attributes or {})


class TestConfig:
    def test_telemetry_section_is_parsed(self) -> None:
        config = parse_config(
            {
                "telemetry": {
                    "honeycomb_api_key": "key",
                    "service_name": "ottobot-test",
                    "endpoint": "https://api.eu1.honeycomb.io",
                }
            }
        )
        assert config.telemetry == TelemetryConfig(
            honeycomb_api_key="key",
            service_name="ottobot-test",
            endpoint="https://api.eu1.honeycomb.io",
        )

    def test_missing_section_leaves_telemetry_empty(self) -> None:
        assert parse_config({"name": "ottobot"}).telemetry == TelemetryConfig()


class TestHoneycombSettings:
    def test_api_key_comes_from_the_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(API_KEY_ENV, "from-env")
        assert (
            api_key(TelemetryConfig(honeycomb_api_key="from-config")) == "from-config"
        )

    def test_api_key_falls_back_to_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(API_KEY_ENV, "from-env")
        assert api_key(TelemetryConfig()) == "from-env"

    def test_no_key_anywhere_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        assert api_key(TelemetryConfig()) is None

    def test_default_endpoint_is_honeycomb(self) -> None:
        assert (
            traces_endpoint(TelemetryConfig()) == "https://api.honeycomb.io/v1/traces"
        )

    def test_endpoint_override_gets_the_traces_path(self) -> None:
        config = TelemetryConfig(endpoint="https://api.eu1.honeycomb.io/")
        assert traces_endpoint(config) == "https://api.eu1.honeycomb.io/v1/traces"

    def test_endpoint_that_already_names_the_path_is_left_alone(self) -> None:
        config = TelemetryConfig(endpoint="https://api.honeycomb.io/v1/traces")
        assert traces_endpoint(config) == "https://api.honeycomb.io/v1/traces"


class TestTracerProvider:
    def test_no_key_means_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        assert build_tracer_provider(BotConfig(name="ottobot"), "ottobot") is None

    def test_service_name_is_the_name_the_bot_runs_under(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        # The running name wins over the config's: it is what --name or the
        # device reported, which is the node the spans actually came from.
        config = BotConfig(
            name="ottobot", telemetry=TelemetryConfig(honeycomb_api_key="key")
        )
        provider = build_tracer_provider(config, "ottobot-dev")
        assert provider is not None
        assert provider.resource.attributes["service.name"] == "ottobot-dev"

    def test_service_name_can_be_overridden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        config = BotConfig(
            name="ottobot",
            telemetry=TelemetryConfig(honeycomb_api_key="key", service_name="staging"),
        )
        provider = build_tracer_provider(config, "ottobot-dev")
        assert provider is not None
        assert provider.resource.attributes["service.name"] == "staging"


class TestDispatchSpans:
    async def test_command_is_traced(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(addressed("!ping now"), reply)

        dispatch = named(spans, "dispatch")
        assert attributes(dispatch)["ottobot.command"] == "ping"
        assert attributes(dispatch)["ottobot.outcome"] == "replied"
        assert attributes(dispatch)["ottobot.replies"] == 1
        assert attributes(dispatch)["ottobot.addressed"] is True
        assert attributes(dispatch)["ottobot.sender_name"] == "alice"
        command = named(spans, "command ping")
        assert attributes(command)["ottobot.args"] == "now"
        # The command span belongs to the message's trace.
        assert command.parent is not None
        assert command.context is not None
        assert command.context.trace_id == dispatch.context.trace_id

    async def test_a_command_that_answers_nothing_is_not_recorded_as_replied(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        @bot.command("quiet")
        async def quiet(ctx: Context) -> None:
            return None  # e.g. a command that only answers some channels

        await bot.dispatch(addressed("!quiet"), reply)

        dispatch = named(spans, "dispatch")
        assert attributes(dispatch)["ottobot.outcome"] == "no_reply"
        assert attributes(dispatch)["ottobot.replies"] == 0
        assert reply.replies == []

    async def test_replies_the_handler_sends_itself_are_counted(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        @bot.command("chatty")
        async def chatty(ctx: Context) -> None:
            await ctx.reply("one")
            await ctx.reply("two")

        await bot.dispatch(addressed("!chatty"), reply)

        dispatch = named(spans, "dispatch")
        assert attributes(dispatch)["ottobot.outcome"] == "replied"
        assert attributes(dispatch)["ottobot.replies"] == 2
        assert reply.replies == ["one", "two"]

    async def test_plain_message_records_why_nothing_ran(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        await bot.dispatch(channel_msg("just chatting"), reply)
        assert (
            attributes(named(spans, "dispatch"))["ottobot.outcome"] == "not_a_command"
        )

    async def test_unknown_command_is_recorded(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        await bot.dispatch(addressed("!nope"), reply)
        outcome = attributes(named(spans, "dispatch"))["ottobot.outcome"]
        assert outcome == "unknown_command"

    async def test_command_off_a_command_channel_is_recorded(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(addressed("!ping", idx=PUBLIC.index), reply)
        outcome = attributes(named(spans, "dispatch"))["ottobot.outcome"]
        assert outcome == "not_a_command_channel"

    async def test_unaddressed_command_is_recorded(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(channel_msg("!ping", idx=1), reply)
        assert (
            attributes(named(spans, "dispatch"))["ottobot.outcome"] == "not_addressed"
        )

    async def test_failing_command_marks_both_spans(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        @bot.command("boom")
        async def boom(ctx: Context) -> str:
            raise RuntimeError("kaboom")

        await bot.dispatch(addressed("!boom"), reply)

        command = named(spans, "command boom")
        assert command.status.status_code is StatusCode.ERROR
        assert [e.name for e in command.events] == ["exception"]
        assert attributes(named(spans, "dispatch"))["ottobot.outcome"] == "error"
        # The bot still apologises rather than dropping the message.
        assert reply.replies == ["Sorry, !boom hit an error."]

    async def test_sinks_are_traced(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        async def watcher(ctx: Context) -> None:
            return None

        bot.add_sink(watcher)
        await bot.dispatch(channel_msg("hello"), reply)
        assert named(spans, "sink watcher").parent is not None

    async def test_failing_sink_is_marked(
        self, bot: Ottobot, reply: ReplyRecorder, spans: InMemorySpanExporter
    ) -> None:
        async def watcher(ctx: Context) -> None:
            raise RuntimeError("nope")

        bot.add_sink(watcher)
        await bot.dispatch(channel_msg("hello"), reply)
        assert named(spans, "sink watcher").status.status_code is StatusCode.ERROR


class TestRunnerSpans:
    async def test_scheduled_task_and_send_are_traced(
        self, spans: InMemorySpanExporter
    ) -> None:
        news = ChannelConfig(index=3, name="#news")
        bot = Ottobot(name="ottobot", config=BotConfig(channels=(news,)))

        @bot.task("greet", interval=timedelta(hours=1), channel=news)
        async def greet(ctx: TaskContext) -> str:
            return "hello mesh"

        runner = MeshCoreRunner(bot, FakeMeshCore())
        await runner._run_task_once(bot.tasks[0])

        task = named(spans, "task greet")
        assert attributes(task)["ottobot.task"] == "greet"
        assert attributes(task)["ottobot.channel"] == "#news"
        send = named(spans, "send")
        assert attributes(send)["ottobot.channel_idx"] == 3
        assert attributes(send)["ottobot.text"] == "hello mesh"
        # The broadcast happens inside the task's trace.
        assert send.context is not None and task.context is not None
        assert send.context.trace_id == task.context.trace_id

    async def test_failing_task_is_marked(self, spans: InMemorySpanExporter) -> None:
        news = ChannelConfig(index=3, name="#news")
        bot = Ottobot(name="ottobot", config=BotConfig(channels=(news,)))

        @bot.task("boom", interval=timedelta(hours=1), channel=news)
        async def boom(ctx: TaskContext) -> str:
            raise RuntimeError("kaboom")

        runner = MeshCoreRunner(bot, FakeMeshCore())
        await runner._run_task_once(bot.tasks[0])

        span = named(spans, "task boom")
        assert span.status.status_code is StatusCode.ERROR
        assert [e.name for e in span.events] == ["exception"]


class TestExport:
    """The exporter really speaks OTLP/HTTP to the configured endpoint."""

    def test_spans_are_posted_with_the_honeycomb_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A proxy in the environment must not swallow the loopback request.
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        with recording_server() as (base_url, received):
            config = BotConfig(
                name="ottobot",
                telemetry=TelemetryConfig(honeycomb_api_key="key", endpoint=base_url),
            )
            provider = build_tracer_provider(config, "ottobot")
            assert provider is not None
            provider.get_tracer("ottobot").start_span("smoke").end()
            provider.shutdown()  # flushes the batch processor

        assert len(received) == 1
        assert received[0].path == "/v1/traces"
        assert received[0].headers[TEAM_HEADER] == "key"
        # The payload is protobuf; the span and service names are legible in it.
        assert b"smoke" in received[0].body
        assert b"ottobot" in received[0].body

    def test_the_export_deadline_is_short(self) -> None:
        # shutdown_telemetry()'s flush is only as bounded as the exporter's
        # own deadline — the SDK takes no timeout for shutdown and ignores
        # the one force_flush is given — so a collector that has gone away
        # must not hold the bot's exit open for the OTLP default of 10s.
        exporter = build_exporter(TelemetryConfig(), "key")
        assert exporter._timeout == EXPORT_TIMEOUT_SECONDS
        assert EXPORT_TIMEOUT_SECONDS <= 5


class TestStartTelemetry:
    @pytest.fixture(autouse=True)
    def _fresh_module_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # start_telemetry keeps process-wide state; monkeypatch puts it back.
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        monkeypatch.setattr(telemetry_module, "_provider", None)
        monkeypatch.setattr(telemetry_module, "_started", False)

    def test_no_key_does_not_start_it(self) -> None:
        assert start_telemetry(BotConfig(name="ottobot"), "ottobot") is False
        # Nothing was installed, so a later start is still possible.
        assert telemetry_module._started is False

    def test_it_declines_to_restart_after_a_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(telemetry_module, "_started", True)
        config = BotConfig(telemetry=TelemetryConfig(honeycomb_api_key="key"))
        # OpenTelemetry keeps the first provider installed, so a restart
        # would report success while dropping every span.
        assert start_telemetry(config, "ottobot") is False


class TestSecretRedaction:
    def test_config_secrets_are_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        config = BotConfig(
            discord_webhook_url="https://discord.com/api/webhooks/123/TOKEN",
            telemetry=TelemetryConfig(honeycomb_api_key="hcaik_secret"),
        )
        assert secret_values(config) == (
            "https://discord.com/api/webhooks/123/TOKEN",
            "hcaik_secret",
        )

    def test_nothing_configured_means_nothing_to_redact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        assert secret_values(BotConfig()) == ()

    def test_a_secret_url_keeps_only_its_origin(self) -> None:
        webhook = "https://discord.com/api/webhooks/123456789/SUPERSECRET"
        assert redact(webhook, (webhook,)) == f"https://discord.com/{REDACTED}"

    def test_a_bare_secret_is_replaced_in_place(self) -> None:
        url = "https://example.test/alerts?key=s3cret"
        assert redact(url, ("s3cret",)) == f"https://example.test/alerts?key={REDACTED}"

    def test_urls_without_a_secret_are_untouched(self) -> None:
        url = "https://api.weather.gc.ca/collections/alerts/items?bbox=1"
        assert redact(url, ("https://discord.com/api/webhooks/1/x",)) == url

    async def test_the_webhook_token_never_reaches_a_span(
        self, spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        token = "SUPERSECRETTOKEN-abcdef"
        with recording_server() as (base_url, _):
            webhook = f"{base_url}/api/webhooks/123456789/{token}"
            instrument_httpx(
                installed_provider(), BotConfig(discord_webhook_url=webhook)
            )
            try:
                await post_to_discord(webhook, {"content": "hello mesh"})
            finally:
                HTTPXClientInstrumentor().uninstrument()

        urls = [
            value
            for key, value in attributes(named(spans, "POST")).items()
            if key in URL_ATTRIBUTES
        ]
        assert urls, attributes(named(spans, "POST"))
        assert all(token not in url for url in urls)
        assert all(url.endswith(REDACTED) for url in urls)
