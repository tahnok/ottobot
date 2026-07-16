"""Tests for the weather_alerts scheduled task."""

from pathlib import Path
from typing import Any

import pytest

from ottobot.channels import OTT_ALERTS
from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.registry import module_tasks
from ottobot.tasks import weather_alerts as alerts_mod

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# A real, live response from Environment Canada's battleboard alerts feed,
# captured 2026-07-16: `onrm104_e.xml` is the body and `onrm104_e.headers`
# the HTTP headers the server actually sent for the app's real
# (gzip-accepting) request — the two are a matched pair.
FIXTURE_FEED = (FIXTURE_DIR / "onrm104_e.xml").read_text(encoding="utf-8")


def load_headers(name: str) -> dict[str, str]:
    """Parse a saved raw-HTTP-header fixture into a case-preserving dict."""
    headers: dict[str, str] = {}
    for line in (FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        headers[key.strip()] = value.strip()
    return headers


FIXTURE_HEADERS = load_headers("onrm104_e.headers")

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
    monkeypatch.setattr(alerts_mod, "_etag", None)
    monkeypatch.setattr(alerts_mod, "_last_modified", None)


class FakeResponse:
    def __init__(
        self,
        text: str = "",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        pass


class FakeClient:
    def __init__(
        self,
        text: str | None = None,
        error: Exception | None = None,
        status_code: int = 200,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.error = error
        self.status_code = status_code
        self.response_headers = response_headers
        self.request_headers: dict[str, str] | None = None

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> FakeResponse:
        self.request_headers = headers or {}
        if self.error is not None:
            raise self.error
        if self.status_code == 304:
            return FakeResponse(status_code=304)
        assert self.text is not None
        return FakeResponse(
            self.text, status_code=self.status_code, headers=self.response_headers
        )


def fake_httpx_client(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> FakeClient:
    client = FakeClient(**kwargs)
    monkeypatch.setattr(alerts_mod.httpx, "AsyncClient", lambda **_: client)
    return client


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
                "tag:weather.gc.ca,2013-04-16:20260715202402",
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


class TestAlertKey:
    def test_drops_slot_marker_keeping_region_and_timestamp(self) -> None:
        assert (
            alerts_mod.alert_key(
                "tag:weather.gc.ca,2013-04-16:onrm104_w2:20260714091437"
            )
            == "tag:weather.gc.ca,2013-04-16:onrm104:20260714091437"
        )

    def test_same_alert_in_different_slots_shares_a_key(self) -> None:
        assert alerts_mod.alert_key(
            "tag:weather.gc.ca,2013-04-16:onrm104_w1:20260714091437"
        ) == alerts_mod.alert_key(
            "tag:weather.gc.ca,2013-04-16:onrm104_w2:20260714091437"
        )

    def test_all_clear_id_without_slot_is_unchanged(self) -> None:
        no_slot = "tag:weather.gc.ca,2013-04-16:20260704092826"
        assert alerts_mod.alert_key(no_slot) == no_slot


class TestWeatherAlertsTask:
    async def test_first_run_primes_without_announcing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        # Stored slot-independent (the `_w1` marker is dropped).
        assert alerts_mod._seen == {
            "tag:weather.gc.ca,2013-04-16:45.403-75.687:20260630204242"
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

    async def test_reshuffled_alert_is_not_reannounced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A new alert pushes existing ones into higher-numbered slots,
        # changing their <id>. The unchanged, merely-shifted alert must not
        # be announced again — only the genuinely new one is.
        heat = """<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-ca">
<updated>2026-07-14T09:14:37Z</updated>
<id>tag:weather.gc.ca,2013-04-16:20260714091437</id>
<entry>
  <title>YELLOW WARNING - HEAT, Ottawa</title>
  <id>tag:weather.gc.ca,2013-04-16:onrm104_w1:20260714091437</id>
</entry>
</feed>"""
        fake_httpx_client(monkeypatch, text=heat)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run: heat in w1

        # Thunderstorm issued: it takes w1, heat shifts to w2 (new <id>).
        both = """<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-ca">
<updated>2026-07-14T16:48:06Z</updated>
<id>tag:weather.gc.ca,2013-04-16:20260714164806</id>
<entry>
  <title>YELLOW WATCH - SEVERE THUNDERSTORM, Ottawa</title>
  <id>tag:weather.gc.ca,2013-04-16:onrm104_w1:20260714164806</id>
</entry><entry>
  <title>YELLOW WARNING - HEAT, Ottawa</title>
  <id>tag:weather.gc.ca,2013-04-16:onrm104_w2:20260714091437</id>
</entry>
</feed>"""
        fake_httpx_client(monkeypatch, text=both)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["YELLOW WATCH - SEVERE THUNDERSTORM, Ottawa"]

    async def test_multiple_new_alerts_are_each_announced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two alerts published in the same fetch: each gets its own message,
        # oldest-first (the feed lists newest first).
        empty_feed = '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
        fake_httpx_client(monkeypatch, text=empty_feed)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        two_alerts = """<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-ca">
<updated>2026-07-14T16:48:06Z</updated>
<id>tag:weather.gc.ca,2013-04-16:20260714164806</id>
<entry>
  <title>YELLOW WATCH - SEVERE THUNDERSTORM, Ottawa</title>
  <id>tag:weather.gc.ca,2013-04-16:onrm104_w1:20260714164806</id>
</entry><entry>
  <title>YELLOW WARNING - HEAT, Ottawa</title>
  <id>tag:weather.gc.ca,2013-04-16:onrm104_w2:20260714091437</id>
</entry>
</feed>"""
        fake_httpx_client(monkeypatch, text=two_alerts)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == [
            "YELLOW WARNING - HEAT, Ottawa",
            "YELLOW WATCH - SEVERE THUNDERSTORM, Ottawa",
        ]

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
        assert alerts_mod._seen == {"tag:weather.gc.ca,2013-04-16:20260715202402"}

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


class TestConditionalFetch:
    # The real headers Environment Canada returned with the captured feed.
    HEADERS = FIXTURE_HEADERS
    # Apache serves the ETag with a `-gzip` suffix it then won't match, so
    # the validator the bot stores and echoes back is the stripped form.
    NORMALIZED_ETAG = 'W/"5dc-lmlFbrdpzI/znMNh1j/xi8VKRXc"'

    async def test_first_fetch_is_unconditional(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = fake_httpx_client(monkeypatch, text=FEED)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert client.request_headers == {}

    async def test_validators_from_response_are_sent_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FIXTURE_FEED, response_headers=self.HEADERS)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._etag == self.NORMALIZED_ETAG
        assert alerts_mod._last_modified == self.HEADERS["Last-Modified"]

        client = fake_httpx_client(monkeypatch, text=FIXTURE_FEED)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert client.request_headers == {
            "If-None-Match": self.NORMALIZED_ETAG,
            "If-Modified-Since": self.HEADERS["Last-Modified"],
        }

    async def test_not_modified_announces_nothing_and_keeps_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FIXTURE_FEED, response_headers=self.HEADERS)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run
        seen_before = set(alerts_mod._seen)

        fake_httpx_client(monkeypatch, status_code=304)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == seen_before
        assert alerts_mod._etag == self.NORMALIZED_ETAG

    async def test_apache_gzip_etag_suffix_is_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Apache's mod_deflate serves W/"...-gzip" ETags it then refuses to
        # match; the stored validator must be the stripped form. The real
        # captured response genuinely carries that suffix.
        assert self.HEADERS["ETag"].endswith('-gzip"')
        fake_httpx_client(
            monkeypatch,
            text=FIXTURE_FEED,
            response_headers={"ETag": self.HEADERS["ETag"]},
        )
        await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._etag == self.NORMALIZED_ETAG

    def test_normalize_etag_leaves_plain_etags_alone(self) -> None:
        assert alerts_mod.normalize_etag('W/"abc"') == 'W/"abc"'
        assert alerts_mod.normalize_etag('"abc"') == '"abc"'

    async def test_missing_validators_send_no_conditional_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)  # response has no cache headers
        await alerts_mod.weather_alerts(make_ctx([]))

        client = fake_httpx_client(monkeypatch, text=FEED)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert client.request_headers == {}
