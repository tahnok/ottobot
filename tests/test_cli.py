import logging
import time

import pytest

from ottobot.cli import apply_timezone


@pytest.fixture(autouse=True)
def _restore_tz():
    # apply_timezone() mutates process-global C timezone state; put it back so
    # tests that set TZ don't leak their zone into the rest of the suite.
    yield
    if hasattr(time, "tzset"):
        time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset() not on this platform")
def test_apply_timezone_makes_localtime_follow_tz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fixed zone with no DST keeps the expected offset unambiguous.
    monkeypatch.setenv("TZ", "Etc/GMT-5")  # note: POSIX sign is inverted -> UTC+5
    apply_timezone()
    # 12:00 UTC on 2021-01-01; struct_time in local time should read the offset.
    utc_epoch = 1609502400  # 2021-01-01 12:00:00 UTC
    assert time.localtime(utc_epoch).tm_hour == 17


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset() not on this platform")
def test_log_timestamps_use_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Etc/GMT-5")
    apply_timezone()
    formatter = logging.Formatter("%(asctime)s", datefmt="%H")
    record = logging.LogRecord("test", logging.INFO, __file__, 0, "msg", None, None)
    record.created = 1609502400  # 2021-01-01 12:00:00 UTC
    assert formatter.format(record) == "17"


def test_apply_timezone_no_tzset_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    # On platforms without tzset (e.g. Windows) apply_timezone must not raise.
    monkeypatch.delattr(time, "tzset", raising=False)
    apply_timezone()
