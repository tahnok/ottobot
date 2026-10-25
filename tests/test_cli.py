import logging
from collections.abc import Generator
from pathlib import Path

import pytest

from ottobot import cli
from ottobot.cli import parse_args, quiet_http_request_logs, run

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


async def test_run_reads_settings_from_env_file(
    clean_http_logging: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Recorded so monkeypatch removes whatever load_dotenv sets.
    for name in ("OTTOBOT_NAME", "OTTOBOT_LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTTOBOT_LOG_LEVEL", "WARNING")
    (tmp_path / ".env").write_text("OTTOBOT_NAME=fromenv\nOTTOBOT_LOG_LEVEL=DEBUG\n")
    monkeypatch.chdir(tmp_path)
    built: dict[str, object] = {}

    def capture(**kwargs: object) -> None:
        built.update(kwargs)
        raise _StopRun

    monkeypatch.setattr(cli, "build_bot", capture)

    with pytest.raises(_StopRun):
        await run(parse_args(["--simulate"]))

    assert built["name"] == "fromenv"
    # The real environment beats the .env file.
    assert logging.getLogger().level == logging.WARNING
