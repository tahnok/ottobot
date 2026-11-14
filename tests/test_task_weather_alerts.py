"""Tests for the weather_alerts scheduled task."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from ottobot.channels import OTT_ALERTS
from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.registry import module_tasks
from ottobot.tasks import weather_alerts as alerts_mod

# A real response from Environment Canada's modern weather-alerts API,
# trimmed to a few features (geometry replaced with a placeholder point).
# It carries one air quality warning that spans three polygons (three
# Features sharing a bulletin id) plus a separate heat warning.
FIXTURE_PAYLOAD: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "weather_alerts.json").read_text(
        encoding="utf-8"
    )
)

# A real response captured live from the bot's own Ottawa query (bbox +
# skipGeometry + properties) during a severe-weather event on 2026-07-21:
# a red tornado warning alongside severe thunderstorm warnings/watches. The
# 13 Features dedupe to 5 distinct bulletins — several alerts each span
# multiple polygons — carrying 3 distinct announcements.
TORNADO_PAYLOAD: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "weather_alerts_tornado.json").read_text(
        encoding="utf-8"
    )
)

# Another live capture (2026-07-28), this one carrying alert_text_en: one
# heavy-rain special weather statement issued as an Ontario-side and a
# Gatineau bulletin (6 Features, 2 bulletin ids, one headline between them)
# plus a severe thunderstorm watch over the Gatineau hills.
STATEMENTS_PAYLOAD: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "weather_alerts_statements.json").read_text(
        encoding="utf-8"
    )
)

# What that capture reads as on the channel: the statement carries the
# headline that gives it its meaning, while the watch's boilerplate headline
# ("Conditions are favourable for the development of severe thunderstorms
# that may be capable of...") overruns a packet and is left off.
STATEMENT_MSG = (
    "Special Weather Statement: Heavy rainfall possible through Wednesday morning."
)
WATCH_MSG = "Severe Thunderstorm Watch"

# A minimal air-quality warning as two polygons of one bulletin.
AQW = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "id": "20330325021_fea1-2112",
                "alert_code": "AQW",
                "alert_name_en": "air quality warning",
                "publication_datetime": "2026-07-16T05:01:00.000Z",
                "feature_id": "fea1-2112",
            },
        },
        {
            "type": "Feature",
            "properties": {
                "id": "20330325021_fea1-2115",
                "alert_code": "AQW",
                "alert_name_en": "air quality warning",
                "publication_datetime": "2026-07-16T05:01:00.000Z",
                "feature_id": "fea1-2115",
            },
        },
    ],
}

EMPTY: dict[str, Any] = {"type": "FeatureCollection", "features": []}


def ended(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy of *payload* with every alert in it marked as over.

    Environment Canada leaves an alert in the collection once it's over,
    flagged status_en "ended", until its expiration datetime hours later.
    The bulletin also picks up a closing line of its own (the text here is
    a real one), which changes the announced text and would otherwise read
    as a fresh alert.
    """
    closing = (
        "Severe thunderstorms associated with this alert have weakened or "
        "moved out of the area."
    )
    features = [
        {
            **feature,
            "properties": {
                **feature["properties"],
                "status_en": "ended",
                "alert_text_en": closing,
            },
        }
        for feature in payload["features"]
    ]
    return {**payload, "features": features}


def with_feature(
    payload: dict[str, Any],
    *,
    id: str,
    feature_id: str,
    name: str,
    published: str,
) -> dict[str, Any]:
    """A copy of *payload* with one more alert Feature appended."""
    feature = {
        "type": "Feature",
        "properties": {
            "id": id,
            "alert_name_en": name,
            "publication_datetime": published,
            "feature_id": feature_id,
        },
    }
    return {**payload, "features": [*payload["features"], feature]}


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alerts_mod, "_seen", set())
    monkeypatch.setattr(alerts_mod, "_primed", False)


def serve(
    api: respx.MockRouter,
    *,
    payload: dict[str, Any] | None = None,
    response: httpx.Response | None = None,
    error: Exception | None = None,
) -> None:
    """Make the alerts endpoint answer with exactly this from now on."""
    api.clear()
    api.reset()
    route = api.get(alerts_mod.ALERTS_URL)
    if error is not None:
        route.mock(side_effect=error)
    else:
        route.mock(return_value=response or httpx.Response(200, json=payload))


def make_ctx(replies: list[str]) -> TaskContext:
    async def reply(text: str) -> None:
        replies.append(text)

    return TaskContext(_reply=reply, config=BotConfig())


def test_task_announces_on_the_ott_alerts_channel() -> None:
    (scheduled,) = module_tasks(alerts_mod)
    assert scheduled.channel is OTT_ALERTS


class TestAlertKey:
    def test_strips_feature_suffix_leaving_bulletin_id(self) -> None:
        assert (
            alerts_mod.alert_key("20330325021202607160501_fea1-2112", "fea1-2112")
            == "20330325021202607160501"
        )

    def test_polygons_of_one_alert_share_a_key(self) -> None:
        assert alerts_mod.alert_key(
            "20330325021_fea1-2112", "fea1-2112"
        ) == alerts_mod.alert_key("20330325021_fea1-2115", "fea1-2115")

    def test_missing_feature_id_keeps_id_unchanged(self) -> None:
        assert alerts_mod.alert_key("bulletin-only", None) == "bulletin-only"


class TestHeadline:
    def test_takes_the_opening_summary_paragraph(self) -> None:
        text = "Heavy rainfall possible through Wednesday morning.\n\nWhat:\n50 mm"
        assert (
            alerts_mod.headline(text)
            == "Heavy rainfall possible through Wednesday morning."
        )

    def test_falls_back_to_the_first_labelled_line(self) -> None:
        # Some bulletins open straight at a section label.
        text = "What:\nRainfall amounts of 50 millimetres or more locally.\n\nWhen:"
        assert (
            alerts_mod.headline(text)
            == "Rainfall amounts of 50 millimetres or more locally."
        )

    def test_no_text_is_no_headline(self) -> None:
        assert alerts_mod.headline("") == ""
        assert alerts_mod.headline("What:") == ""


class TestTitle:
    @staticmethod
    def titled(name: str, alert_text: str) -> str:
        payload = {
            "features": [
                {
                    "properties": {
                        "id": "123_fea1",
                        "feature_id": "fea1",
                        "alert_name_en": name,
                        "alert_text_en": alert_text,
                        "publication_datetime": "2026-07-28T09:59:16.860Z",
                    }
                }
            ]
        }
        (alert,) = alerts_mod.parse_alerts(payload)
        return alert.title

    def test_appends_the_headline_when_the_bulletin_has_one(self) -> None:
        assert (
            self.titled("special weather statement", "Heavy rain.\n\nWhat:\n50 mm")
            == "Special Weather Statement: Heavy rain."
        )

    def test_name_alone_when_the_bulletin_carries_no_text(self) -> None:
        (alert,) = alerts_mod.parse_alerts(AQW)
        assert alert.title == "Air Quality Warning"

    def test_headline_too_long_for_a_packet_is_left_off(self) -> None:
        # Clipping boilerplate mid-sentence reads worse than the bare name.
        assert self.titled("heat warning", "It is hot. " * 20) == "Heat Warning"

    def test_a_headline_that_just_fits_is_kept(self) -> None:
        summary = "x" * (alerts_mod.MAX_MESSAGE_LEN - len("Heat Warning: "))
        assert self.titled("heat warning", summary) == f"Heat Warning: {summary}"

    def test_fit_is_measured_in_utf8_bytes(self) -> None:
        # "é" is two bytes: this headline fits by character count but not on
        # the wire, and MeshCore counts bytes.
        summary = "é" * (alerts_mod.MAX_MESSAGE_LEN - len("Heat Warning: "))
        assert self.titled("heat warning", summary) == "Heat Warning"


class TestParseAlerts:
    def test_dedupes_polygons_of_one_alert(self) -> None:
        # AQW appears as two polygons of one bulletin -> one Alert.
        assert alerts_mod.parse_alerts(AQW) == [
            alerts_mod.Alert(
                "20330325021", "Air Quality Warning", "2026-07-16T05:01:00.000Z"
            )
        ]

    def test_parses_real_api_collection(self) -> None:
        alerts = alerts_mod.parse_alerts(FIXTURE_PAYLOAD)
        # Three AQW polygons collapse to one; heat warning is the other.
        assert [(a.title, a.published) for a in alerts] == [
            ("Air Quality Warning", "2026-07-21T09:33:02.573Z"),
            ("Heat Warning", "2026-07-21T10:53:45.081Z"),
        ]

    def test_orders_alerts_oldest_first(self) -> None:
        payload = with_feature(
            AQW,
            id="99999_fea9",
            feature_id="fea9",
            name="heat warning",
            published="2026-07-17T12:00:00.000Z",
        )
        assert [a.title for a in alerts_mod.parse_alerts(payload)] == [
            "Air Quality Warning",
            "Heat Warning",
        ]

    def test_title_cases_the_alert_name(self) -> None:
        (alert,) = alerts_mod.parse_alerts(AQW)
        assert alert.title == "Air Quality Warning"

    def test_parses_real_severe_weather_event(self) -> None:
        # 13 polygons across 5 bulletins (incl. a tornado warning), deduped
        # and ordered oldest-first by publication time. The two watches and
        # the two warnings are separate bulletins that would read identically
        # on the channel, so each is announced once.
        alerts = alerts_mod.parse_alerts(TORNADO_PAYLOAD)
        assert [a.title for a in alerts] == [
            "Severe Thunderstorm Watch",
            "Severe Thunderstorm Warning",
            "Tornado Warning",
        ]

    def test_parses_real_statements_with_headlines(self) -> None:
        # The statement spans two bulletins (Ontario side + Gatineau) with
        # one headline between them, so the channel sees it once.
        alerts = alerts_mod.parse_alerts(STATEMENTS_PAYLOAD)
        assert [a.title for a in alerts] == [WATCH_MSG, STATEMENT_MSG]

    def test_dedupes_bulletins_that_read_the_same(self) -> None:
        # Two bulletins, different ids, same announcement -> one message.
        payload = with_feature(
            AQW,
            id="77777_fea7",
            feature_id="fea7",
            name="air quality warning",
            published="2026-07-16T06:00:00.000Z",
        )
        assert [a.key for a in alerts_mod.parse_alerts(payload)] == ["20330325021"]

    def test_no_active_alerts_returns_empty(self) -> None:
        assert alerts_mod.parse_alerts(EMPTY) == []

    def test_alerts_that_have_ended_are_dropped(self) -> None:
        assert alerts_mod.parse_alerts(ended(AQW)) == []

    def test_an_alert_that_ended_leaves_the_live_ones_alone(self) -> None:
        payload = {
            **AQW,
            "features": [
                *ended(AQW)["features"],
                *with_feature(
                    EMPTY,
                    id="88888_fea1",
                    feature_id="fea1",
                    name="tornado warning",
                    published="2026-07-16T22:13:50.000Z",
                )["features"],
            ],
        }
        assert [a.title for a in alerts_mod.parse_alerts(payload)] == [
            "Tornado Warning"
        ]

    def test_feature_without_id_is_skipped(self) -> None:
        payload = {"features": [{"properties": {"alert_name_en": "x"}}]}
        assert alerts_mod.parse_alerts(payload) == []


class TestWeatherAlertsTask:
    async def test_first_run_primes_without_announcing(
        self, api: respx.MockRouter
    ) -> None:
        serve(api, payload=AQW)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == {"Air Quality Warning"}

    async def test_query_is_scoped_to_ottawa(self, api: respx.MockRouter) -> None:
        serve(api, payload=EMPTY)
        await alerts_mod.weather_alerts(make_ctx([]))
        # What's on the wire, as httpx encoded it from _PARAMS.
        assert dict(api.calls.last.request.url.params) == {
            key: str(value) for key, value in alerts_mod._PARAMS.items()
        }
        assert api.calls.last.request.url.params["bbox"] == "-76.1,45.15,-75.4,45.55"

    async def test_second_run_announces_new_alert(self, api: respx.MockRouter) -> None:
        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        updated = with_feature(
            AQW,
            id="88888_fea1",
            feature_id="fea1",
            name="severe thunderstorm watch",
            published="2026-07-16T22:13:50.000Z",
        )
        serve(api, payload=updated)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["Severe Thunderstorm Watch"]

    async def test_ongoing_multi_polygon_alert_is_not_reannounced(
        self, api: respx.MockRouter
    ) -> None:
        # The AQW is primed as two polygons; on the next fetch its polygons
        # differ (EC returns a different polygon set) but the bulletin id is
        # unchanged, so it must not be announced again.
        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        reshaped = {
            "features": [
                {
                    "properties": {
                        "id": "20330325021_fea1-9999",
                        "alert_name_en": "air quality warning",
                        "publication_datetime": "2026-07-16T05:01:00.000Z",
                        "feature_id": "fea1-9999",
                    }
                }
            ]
        }
        serve(api, payload=reshaped)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []

    async def test_two_alerts_in_one_bulletin_are_both_announced(
        self, api: respx.MockRouter
    ) -> None:
        serve(api, payload=EMPTY)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        two = with_feature(
            with_feature(
                EMPTY,
                id="111_fea1",
                feature_id="fea1",
                name="severe thunderstorm watch",
                published="2026-07-16T22:13:50.000Z",
            ),
            id="222_fea1",
            feature_id="fea1",
            name="heat warning",
            published="2026-07-16T09:14:37.000Z",
        )
        serve(api, payload=two)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["Heat Warning", "Severe Thunderstorm Watch"]

    async def test_severe_weather_event_announces_each_alert_once(
        self, api: respx.MockRouter
    ) -> None:
        # Real capture: 13 polygons -> 3 announcements, each on its own
        # packet, oldest-first, with the tornado warning among them.
        serve(api, payload=EMPTY)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        serve(api, payload=TORNADO_PAYLOAD)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == [
            "Severe Thunderstorm Watch",
            "Severe Thunderstorm Warning",
            "Tornado Warning",
        ]

    async def test_one_event_issued_per_region_is_announced_once(
        self, api: respx.MockRouter
    ) -> None:
        # Real capture: the heavy-rain statement arrives as an Ontario-side
        # and a Gatineau bulletin, but says the same thing on both.
        serve(api, payload=EMPTY)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        serve(api, payload=STATEMENTS_PAYLOAD)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == [WATCH_MSG, STATEMENT_MSG]
        assert all(
            len(r.encode("utf-8")) <= alerts_mod.MAX_MESSAGE_LEN for r in replies
        )

    async def test_second_bulletin_of_a_live_event_is_not_reannounced(
        self, api: respx.MockRouter
    ) -> None:
        # The Ontario-side statement is live and announced; the Gatineau one
        # is issued a fetch later with the same headline, and must not repeat
        # the message on the channel.
        ontario = {
            **STATEMENTS_PAYLOAD,
            "features": STATEMENTS_PAYLOAD["features"][1:2],
        }
        serve(api, payload=EMPTY)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        serve(api, payload=ontario)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == [STATEMENT_MSG]

        serve(api, payload=STATEMENTS_PAYLOAD)
        replies = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == [WATCH_MSG]

    async def test_unchanged_collection_announces_nothing(
        self, api: respx.MockRouter
    ) -> None:
        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []

    async def test_fetch_failure_propagates_without_priming(
        self, api: respx.MockRouter
    ) -> None:
        # No local catch-all: the runner logs a raising task and carries on.
        serve(api, error=httpx.ConnectError("network is down"))
        replies: list[str] = []
        with pytest.raises(httpx.ConnectError):
            await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._primed is False

    async def test_all_clear_announced_once_when_alerts_end(
        self, api: respx.MockRouter
    ) -> None:
        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        serve(api, payload=EMPTY)  # alerts cleared
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["No alerts in effect"]
        assert alerts_mod._seen == set()

        # A subsequent empty fetch must not repeat the all-clear.
        serve(api, payload=EMPTY)
        replies = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []

    async def test_all_clear_fires_while_the_ended_alert_lingers(
        self, api: respx.MockRouter
    ) -> None:
        # The ended bulletin sits in the collection for hours after the
        # weather is over; the all-clear must not wait for it to age out.
        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        serve(api, payload=ended(AQW))
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["No alerts in effect"]

    async def test_an_alert_ending_is_not_announced(
        self, api: respx.MockRouter
    ) -> None:
        # The closing line an ended bulletin picks up would otherwise read
        # as a new alert, since it changes the announced text.
        serve(api, payload=EMPTY)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        serve(api, payload=AQW)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["Air Quality Warning"]

        serve(api, payload=ended(AQW))
        replies = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["No alerts in effect"]

    async def test_seen_alerts_are_pruned_when_alerts_leave(
        self, api: respx.MockRouter
    ) -> None:
        # _seen must track the live collection, not grow forever on a
        # long-running bot: once an alert is gone, its entry goes too.
        serve(api, payload=FIXTURE_PAYLOAD)
        await alerts_mod.weather_alerts(make_ctx([]))  # primes AQW + heat
        assert len(alerts_mod._seen) == 2

        serve(api, payload=AQW)  # only the AQW remains
        await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._seen == {"Air Quality Warning"}

    async def test_no_active_alerts_primes_to_empty(
        self, api: respx.MockRouter
    ) -> None:
        serve(api, payload=EMPTY)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == set()
        assert alerts_mod._primed is True


class TestFailedFetch:
    """Failures propagate for the runner to log, with nothing said or changed."""

    @staticmethod
    async def assert_fails(expected: type[Exception]) -> None:
        replies: list[str] = []
        with pytest.raises(expected):
            await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._primed is False

    # 301: httpx doesn't follow redirects unless told to, so a moved endpoint
    # fails here rather than being chased.
    @pytest.mark.parametrize("status", [301, 404, 429, 500, 503])
    async def test_error_status(self, api: respx.MockRouter, status: int) -> None:
        serve(api, response=httpx.Response(status, json=AQW))
        await self.assert_fails(httpx.HTTPStatusError)

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectTimeout("connect timed out"),
            httpx.ReadTimeout("read timed out"),
            httpx.ConnectError("network is down"),
        ],
    )
    async def test_transport_error(
        self, api: respx.MockRouter, error: Exception
    ) -> None:
        serve(api, error=error)
        await self.assert_fails(type(error))

    @pytest.mark.parametrize(
        "body", [b"<html>Service Unavailable</html>", b"", b'{"features": [']
    )
    async def test_malformed_json(self, api: respx.MockRouter, body: bytes) -> None:
        serve(api, response=httpx.Response(200, content=body))
        await self.assert_fails(json.JSONDecodeError)

    async def test_json_that_is_not_a_collection(self, api: respx.MockRouter) -> None:
        # A shape change fails the poll loudly instead of reading as "fetch
        # failed" (or as no alerts).
        serve(api, response=httpx.Response(200, json=["not", "a", "collection"]))
        await self.assert_fails(AttributeError)

    async def test_next_poll_recovers(self, api: respx.MockRouter) -> None:
        serve(api, response=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._primed is False

        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._seen == {"Air Quality Warning"}

    async def test_failure_mid_alert_keeps_what_was_seen(
        self, api: respx.MockRouter
    ) -> None:
        # An outage while an alert is live must neither sound the all-clear
        # nor make the alert read as new once the API is back.
        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        serve(api, response=httpx.Response(503))
        replies: list[str] = []
        with pytest.raises(httpx.HTTPStatusError):
            await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == {"Air Quality Warning"}

        serve(api, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []


async def test_request_carries_a_timeout(api: respx.MockRouter) -> None:
    serve(api, payload=EMPTY)
    await alerts_mod.weather_alerts(make_ctx([]))
    assert set(api.calls.last.request.extensions["timeout"].values()) == {10}
