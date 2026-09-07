"""Tests for the OpenTelemetry wiring and the spans handlers produce."""

import threading
from collections.abc import Generator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from helpers import ReplyRecorder, addressed, channel_msg
from ottobot import Context, Ottobot, TaskContext
from ottobot.channels import PUBLIC, ChannelConfig
from ottobot.config import BotConfig, TelemetryConfig, parse_config
from ottobot.runner import MeshCoreRunner
from ottobot.telemetry import (
    API_KEY_ENV,
    DATASET_HEADER,
    TEAM_HEADER,
    api_key,
    build_tracer_provider,
    headers,
    traces_endpoint,
)

# The fake device from the runner tests, reused here to trace real sends.
from test_runner import FakeMeshCore

# The tracer provider can only be installed once per process, so it is
# built lazily and shared; each test clears the exporter first.
_EXPORTER: InMemorySpanExporter | None = None


@pytest.fixture
def spans() -> Generator[InMemorySpanExporter]:
    global _EXPORTER
    if _EXPORTER is None:
        _EXPORTER = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(provider)
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


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
                    "dataset": "mesh",
                    "service_name": "ottobot-test",
                    "endpoint": "https://api.eu1.honeycomb.io",
                }
            }
        )
        assert config.telemetry == TelemetryConfig(
            honeycomb_api_key="key",
            dataset="mesh",
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

    def test_headers_carry_the_key(self) -> None:
        assert headers(TelemetryConfig(), "key") == {TEAM_HEADER: "key"}

    def test_dataset_header_is_sent_when_configured(self) -> None:
        sent = headers(TelemetryConfig(dataset="mesh"), "key")
        assert sent == {TEAM_HEADER: "key", DATASET_HEADER: "mesh"}


class TestTracerProvider:
    def test_no_key_means_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        assert build_tracer_provider(BotConfig(name="ottobot")) is None

    def test_service_name_defaults_to_the_bot_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        config = BotConfig(
            name="ottobot-2", telemetry=TelemetryConfig(honeycomb_api_key="key")
        )
        provider = build_tracer_provider(config)
        assert provider is not None
        assert provider.resource.attributes["service.name"] == "ottobot-2"

    def test_service_name_can_be_overridden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        config = BotConfig(
            name="ottobot",
            telemetry=TelemetryConfig(honeycomb_api_key="key", service_name="staging"),
        )
        provider = build_tracer_provider(config)
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
        assert attributes(dispatch)["ottobot.addressed"] is True
        assert attributes(dispatch)["ottobot.sender_name"] == "alice"
        command = named(spans, "command ping")
        assert attributes(command)["ottobot.args"] == "now"
        # The command span belongs to the message's trace.
        assert command.parent is not None
        assert command.context is not None
        assert command.context.trace_id == dispatch.context.trace_id

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
        received: list[tuple[str, dict[str, str], bytes]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                received.append(
                    (self.path, dict(self.headers), self.rfile.read(length))
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass  # keep the test output quiet

        # A proxy in the environment must not swallow the loopback request.
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            config = BotConfig(
                name="ottobot",
                telemetry=TelemetryConfig(
                    honeycomb_api_key="key",
                    dataset="mesh",
                    endpoint=f"http://127.0.0.1:{httpd.server_address[1]}",
                ),
            )
            provider = build_tracer_provider(config)
            assert provider is not None
            provider.get_tracer("ottobot").start_span("smoke").end()
            provider.shutdown()  # flushes the batch processor
        finally:
            httpd.shutdown()
            thread.join()
            httpd.server_close()

        assert len(received) == 1
        path, sent_headers, body = received[0]
        assert path == "/v1/traces"
        assert sent_headers[TEAM_HEADER] == "key"
        assert sent_headers[DATASET_HEADER] == "mesh"
        # The payload is protobuf; the span and service names are legible in it.
        assert b"smoke" in body
        assert b"ottobot" in body
