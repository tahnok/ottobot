import logging
from collections.abc import Generator

import pytest

from ottobot.cli import quiet_http_request_logs

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
