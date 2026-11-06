import asyncio
import io
import logging
import subprocess
import sys
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import respx
from meshcore import EventType

from helpers import FakeMeshCore
from ottobot import cli
from ottobot.channels import BOTS
from ottobot.cli import parse_args, quiet_http_request_logs, run
from ottobot.radio import RADIO
from ottobot.tasks import weather_alerts as alerts_mod
from test_task_weather_alerts import AQW, serve

HTTP_LOGGERS = ("httpx", "httpcore")


@pytest.fixture
def clean_http_logging() -> Generator[None]:
    """Snapshot and restore the shared logger levels this test mutates."""
    root = logging.getLogger()
    saved = {name: logging.getLogger(name).level for name in HTTP_LOGGERS}
    saved_root = root.level

    for name in HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)

    try:
        yield
    finally:
        root.setLevel(saved_root)
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


def test_quiets_http_request_logs_below_debug(clean_http_logging: None) -> None:
    logging.getLogger().setLevel(logging.INFO)

    quiet_http_request_logs()

    for name in HTTP_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_leaves_http_request_logs_alone_at_debug(clean_http_logging: None) -> None:
    logging.getLogger().setLevel(logging.DEBUG)

    quiet_http_request_logs()

    for name in HTTP_LOGGERS:
        assert logging.getLogger(name).level == logging.NOTSET


class _StopRun(Exception):
    """Raised in place of building the bot, to stop run() once logging is set."""


def _stop_run(**_: object) -> None:
    raise _StopRun


async def test_run_applies_config_log_level_before_quieting(
    clean_http_logging: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging.getLogger().setLevel(logging.INFO)
    config = tmp_path / "config.toml"
    config.write_text('log_level = "DEBUG"\n')
    monkeypatch.setattr(cli, "build_bot", _stop_run)

    with pytest.raises(_StopRun):
        await run(parse_args(["--simulate", "--config", str(config)]))

    # The config's DEBUG level is applied first, so httpx is left at DEBUG.
    assert logging.getLogger().level == logging.DEBUG
    for name in HTTP_LOGGERS:
        assert logging.getLogger(name).level == logging.NOTSET


@pytest.fixture
def clean_root_logging(clean_http_logging: None) -> Generator[None]:
    """main() swaps the root logger's handlers (force=True); put them back."""
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        yield
    finally:
        root.handlers[:] = saved


class TestSimulate:
    """`ottobot --simulate` from main(), with real commands and tasks."""

    @pytest.fixture
    def run_main(
        self,
        clean_root_logging: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> Callable[..., str]:
        def run_main(*lines: str, argv: Sequence[str] = ("--simulate",)) -> str:
            """Type *lines* into main(), then EOF, and return what it printed."""
            monkeypatch.setattr(sys, "argv", ["ottobot", *argv])
            monkeypatch.setattr(
                sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines))
            )
            cli.main()
            return capsys.readouterr().out

        return run_main

    def test_answers_commands_and_exits_cleanly_on_eof(
        self, run_main: Callable[..., str]
    ) -> None:
        out = run_main("@[ottobot] !ping", "just chatting", "/bogus")

        assert "Mention the bot to address it, e.g. '@[ottobot] !help'" in out
        assert "bot> @[you] pong (direct)" in out
        assert "(no response)" in out
        assert "unknown simulator control /bogus" in out

    def test_quit_ignores_the_rest_of_the_input(
        self, run_main: Callable[..., str]
    ) -> None:
        out = run_main("/quit", "@[ottobot] !ping")

        assert "bye" in out
        assert "pong" not in out

    def test_commands_are_silent_off_the_command_channels(
        self, run_main: Callable[..., str]
    ) -> None:
        out = run_main("/channel 0", "@[ottobot] !ping")

        assert "not answered on channel 0" in out
        assert "pong" not in out

    def test_runs_a_real_task(
        self,
        run_main: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
        api: respx.MockRouter,
    ) -> None:
        serve(api, payload=AQW)
        monkeypatch.setattr(alerts_mod, "_seen", set())
        monkeypatch.setattr(alerts_mod, "_primed", True)

        out = run_main("/task", "/task weather_alerts", "/task nosuchtask")

        assert "available: " in out
        assert "weather_alerts" in out
        assert "task> Air Quality Warning" in out
        assert "no such task 'nosuchtask'" in out

    def test_name_comes_from_the_flag(self, run_main: Callable[..., str]) -> None:
        out = run_main("@[zed] !ping", argv=("--simulate", "--name", "zed"))

        assert "bot> @[you] pong" in out

    def test_name_comes_from_the_config(
        self, run_main: Callable[..., str], tmp_path: Path
    ) -> None:
        config = tmp_path / "config.toml"
        config.write_text('name = "zed"\n')

        out = run_main("@[zed] !ping", argv=("--simulate", "--config", str(config)))

        assert "bot> @[you] pong" in out

    def test_runs_as_a_real_process(self, tmp_path: Path) -> None:
        # The rest of this class stays in-process to fake stdin and HTTP; this
        # one covers what that can't, that `python -m ottobot` starts at all.
        result = subprocess.run(
            [sys.executable, "-m", "ottobot", "--simulate"],
            input="@[ottobot] !ping\n/quit\n",
            capture_output=True,
            text=True,
            timeout=60,
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        assert "bot> @[you] pong (direct)" in result.stdout
        assert "bye" in result.stdout


@pytest.fixture
def use_device(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[FakeMeshCore], list[dict[str, Any]]]:
    """Make connect() return a fake, recording the arguments it was called with.

    The real tasks are left out too, because each one polls a live API as soon
    as the runner starts.
    """
    monkeypatch.setattr(cli, "load_tasks", lambda bot: [])

    def use(mc: FakeMeshCore) -> list[dict[str, Any]]:
        connections: list[dict[str, Any]] = []

        async def fake_connect(**kwargs: Any) -> FakeMeshCore:
            connections.append(kwargs)
            return mc

        monkeypatch.setattr(cli, "connect", fake_connect)
        return connections

    return use


@asynccontextmanager
async def running(argv: list[str], mc: FakeMeshCore) -> AsyncGenerator[None]:
    """Run the CLI's device path until the block ends, then cancel it."""
    task = asyncio.create_task(run(parse_args(argv)))

    async def listening() -> None:
        # Nothing the fake device does really suspends, so by the time this
        # gets a turn with fetching on, start() has run to the end.
        while not mc.fetching:
            if task.done():
                task.result()
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(listening(), timeout=5)
        yield
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def startup_failure(argv: list[str]) -> None:
    """Run the device path where startup should raise.

    Bounded because a startup that wrongly succeeds runs forever, which should
    fail the test rather than hang the suite.
    """
    await asyncio.wait_for(run(parse_args(argv)), timeout=5)


class TestDevicePath:
    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            (
                ["--serial", "/dev/ttyUSB0"],
                {
                    "serial": "/dev/ttyUSB0",
                    "baudrate": 115200,
                    "ble": None,
                    "tcp": None,
                },
            ),
            (
                ["--serial", "/dev/ttyUSB0", "--baudrate", "9600"],
                {"serial": "/dev/ttyUSB0", "baudrate": 9600, "ble": None, "tcp": None},
            ),
            (
                ["--ble", "AA:BB"],
                {"serial": None, "baudrate": 115200, "ble": "AA:BB", "tcp": None},
            ),
            (
                ["--tcp", "10.0.0.1:4000"],
                {
                    "serial": None,
                    "baudrate": 115200,
                    "ble": None,
                    "tcp": "10.0.0.1:4000",
                },
            ),
        ],
    )
    async def test_flags_pick_the_transport(
        self,
        flags: list[str],
        expected: dict[str, Any],
        use_device: Callable[[FakeMeshCore], list[dict[str, Any]]],
    ) -> None:
        mc = FakeMeshCore()
        connections = use_device(mc)

        async with running(flags, mc):
            pass

        assert connections == [expected]

    @pytest.mark.parametrize(
        ("flag_name", "config_name", "expected", "ignored"),
        [
            pytest.param("flag", "conf", "flag", ["conf", "device"], id="flag-wins"),
            pytest.param(None, "conf", "conf", ["device"], id="config-over-device"),
            pytest.param(None, None, "device", ["flag", "conf"], id="device-last"),
        ],
    )
    async def test_name_resolution(
        self,
        flag_name: str | None,
        config_name: str | None,
        expected: str,
        ignored: list[str],
        use_device: Callable[[FakeMeshCore], list[dict[str, Any]]],
        tmp_path: Path,
    ) -> None:
        mc = FakeMeshCore(self_info={"name": "device"})
        use_device(mc)
        argv = ["--serial", "/dev/ttyUSB0"]
        if flag_name:
            argv += ["--name", flag_name]
        if config_name:
            config = tmp_path / "config.toml"
            config.write_text(f'name = "{config_name}"\n')
            argv += ["--config", str(config)]

        async with running(argv, mc):
            for name in ignored:
                await mc.deliver_chan(f"alice: @[{name}] !ping", channel_idx=BOTS.index)
            assert mc.commands.sent_chan_msgs == []

            await mc.deliver_chan(f"alice: @[{expected}] !ping", channel_idx=BOTS.index)
            assert mc.commands.sent_chan_msgs == [
                (BOTS.index, "@[alice] pong (unknown path)")
            ]

    @pytest.mark.parametrize("self_info", [{}, {"name": ""}, None])
    async def test_no_name_exits_and_disconnects(
        self,
        self_info: dict[str, Any] | None,
        use_device: Callable[[FakeMeshCore], list[dict[str, Any]]],
    ) -> None:
        mc = FakeMeshCore()
        mc.self_info = self_info
        use_device(mc)

        with pytest.raises(SystemExit, match="could not determine the bot's name"):
            await startup_failure(["--serial", "/dev/ttyUSB0"])

        assert mc.disconnect_calls == 1

    async def test_failed_startup_disconnects_and_flushes_telemetry(
        self,
        use_device: Callable[[FakeMeshCore], list[dict[str, Any]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mc = FakeMeshCore()
        use_device(mc)
        flushes: list[None] = []
        monkeypatch.setattr(cli, "shutdown_telemetry", lambda: flushes.append(None))

        async def broken_radio(*_: object) -> None:
            raise RuntimeError("radio on fire")

        monkeypatch.setattr(mc.commands, "set_radio", broken_radio)

        with pytest.raises(RuntimeError, match="radio on fire"):
            await startup_failure(["--serial", "/dev/ttyUSB0"])

        assert mc.disconnect_calls == 1
        assert flushes == [None]

    async def test_startup_applies_settings_and_starts_listening(
        self,
        use_device: Callable[[FakeMeshCore], list[dict[str, Any]]],
    ) -> None:
        mc = FakeMeshCore()
        use_device(mc)

        async with running(["--serial", "/dev/ttyUSB0"], mc):
            assert mc.commands.radios == [(RADIO.freq, RADIO.bw, RADIO.sf, RADIO.cr)]
            assert EventType.CHANNEL_MSG_RECV in mc.callbacks
            assert mc.disconnect_calls == 0

    async def test_cancelling_stops_the_runner_and_disconnects(
        self,
        use_device: Callable[[FakeMeshCore], list[dict[str, Any]]],
    ) -> None:
        mc = FakeMeshCore()
        use_device(mc)

        async with running(["--serial", "/dev/ttyUSB0"], mc):
            assert mc.fetching

        assert not mc.fetching
        assert mc.callbacks == {}
        assert mc.disconnect_calls == 1


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no-transport"),
        pytest.param(["--serial", "/dev/ttyUSB0", "--tcp", "h:1"], id="two-transports"),
    ],
)
def test_cli_requires_exactly_one_transport(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv)

    assert exit_info.value.code == 2
