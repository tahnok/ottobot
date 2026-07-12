"""Tests for the weather_alerts scheduled task."""

from pathlib import Path
from typing import Any

import pytest

from ottobot.channels import OTT_ALERTS
from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.registry import module_tasks
from ottobot.tasks import weather_alerts as alerts_mod

# A real response from Environment Canada's battleboard alerts feed,
# captured 2026-07-04.
FIXTURE_FEED = (Path(__file__).parent / "fixtures" / "onrm104_e.xml").read_text(
    encoding="utf-8"
)

# A trimmed real response from Environment Canada's alerts feed.
FEED = """<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-ca">
<title>Ottawa - Weather Alert - Environment Canada</title>
<updated>2026-06-30T20:42:42Z</updated>
<id>tag:weather.gc.ca,2013-04-16:20260630204242</id>
<entry>
  <title>ORANGE WARNING - HEAT, Ottawa</title>
  <link type="text/html" href="https://weather.gc.ca/warnings/report_e.html?onrm104"/>
  <updated>2026-06-30T20:42:42Z</updated>
  <published>2026-06-30T20:42:42Z</published>
  <category term="Warnings and Watches"/>
  <summary type="html">Issued: 4:42 PM EDT Tuesday 30 June 2026</summary>
  <id>tag:weather.gc.ca,2013-04-16:45.403-75.687_w1:20260630204242</id>
</entry>
</feed>"""


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alerts_mod, "_seen", set())
    monkeypatch.setattr(alerts_mod, "_primed", False)


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class FakeClient:
    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str) -> FakeResponse:
        if self.error is not None:
            raise self.error
        assert self.text is not None
        return FakeResponse(self.text)


def fake_httpx_client(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> None:
    monkeypatch.setattr(
        alerts_mod.httpx, "AsyncClient", lambda **_: FakeClient(**kwargs)
    )


def make_ctx(replies: list[str]) -> TaskContext:
    async def reply(text: str) -> None:
        replies.append(text)

    return TaskContext(_reply=reply, config=BotConfig())


def test_task_announces_on_the_ott_alerts_channel() -> None:
    (scheduled,) = module_tasks(alerts_mod)
    assert scheduled.channel is OTT_ALERTS


class TestParseAlerts:
    def test_parses_id_and_title(self) -> None:
        assert alerts_mod.parse_alerts(FEED) == [
            (
                "tag:weather.gc.ca,2013-04-16:45.403-75.687_w1:20260630204242",
                "ORANGE WARNING - HEAT, Ottawa",
            )
        ]

    def test_parses_real_battleboard_feed(self) -> None:
        assert alerts_mod.parse_alerts(FIXTURE_FEED) == [
            (
                "tag:weather.gc.ca,2013-04-16:20260704092826",
                "No alerts in effect",
            )
        ]

    def test_strips_region_suffix_from_title(self) -> None:
        xml = FEED.replace(
            "ORANGE WARNING - HEAT, Ottawa",
            "ORANGE WARNING - HEAT, Ottawa North - Kanata - Orléans",
        )
        assert alerts_mod.parse_alerts(xml) == [
            (
                "tag:weather.gc.ca,2013-04-16:45.403-75.687_w1:20260630204242",
                "ORANGE WARNING - HEAT",
            )
        ]

    def test_no_active_alerts_returns_empty(self) -> None:
        empty_feed = '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
        assert alerts_mod.parse_alerts(empty_feed) == []

    def test_entry_without_id_is_skipped(self) -> None:
        xml = (
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>no id here</title></entry></feed>"
        )
        assert alerts_mod.parse_alerts(xml) == []


class TestWeatherAlertsTask:
    async def test_first_run_primes_without_announcing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == {
            "tag:weather.gc.ca,2013-04-16:45.403-75.687_w1:20260630204242"
        }

    async def test_second_run_announces_new_alert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        updated = FEED.replace(
            "<entry>",
            "<entry>\n"
            "  <title>YELLOW WATCH - SEVERE THUNDERSTORM, Ottawa</title>\n"
            "  <id>tag:weather.gc.ca,2013-04-16:45.403-75.687_w2:20260630221350</id>\n"
            "</entry><entry>",
            1,
        )
        fake_httpx_client(monkeypatch, text=updated)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["YELLOW WATCH - SEVERE THUNDERSTORM, Ottawa"]

    async def test_unchanged_feed_announces_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []

    async def test_fetch_failure_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, error=RuntimeError("network is down"))
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._primed is False

    async def test_seen_ids_are_pruned_when_alerts_leave_the_feed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _seen must track the live feed, not grow forever on a
        # long-running bot: once an alert's entry is gone, its id goes too.
        fake_httpx_client(monkeypatch, text=FEED)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        fake_httpx_client(monkeypatch, text=FIXTURE_FEED)  # only the all-clear
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["No alerts in effect"]
        assert alerts_mod._seen == {"tag:weather.gc.ca,2013-04-16:20260704092826"}

    async def test_no_active_alerts_primes_to_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_feed = '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
        fake_httpx_client(monkeypatch, text=empty_feed)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == set()
        assert alerts_mod._primed is True
