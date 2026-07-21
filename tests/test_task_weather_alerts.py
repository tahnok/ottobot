"""Tests for the weather_alerts scheduled task."""

import json
from pathlib import Path
from typing import Any

import pytest

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
    monkeypatch.setattr(alerts_mod, "_etag", None)
    monkeypatch.setattr(alerts_mod, "_last_modified", None)


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        assert self._payload is not None
        return self._payload

    def raise_for_status(self) -> None:
        pass


class FakeClient:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
        status_code: int = 200,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.status_code = status_code
        self.response_headers = response_headers
        self.request_headers: dict[str, str] | None = None
        self.request_params: dict[str, Any] | None = None

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.request_headers = headers or {}
        self.request_params = params
        if self.error is not None:
            raise self.error
        if self.status_code == 304:
            return FakeResponse(status_code=304)
        return FakeResponse(
            self.payload,
            status_code=self.status_code,
            headers=self.response_headers,
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

    def test_no_active_alerts_returns_empty(self) -> None:
        assert alerts_mod.parse_alerts(EMPTY) == []

    def test_feature_without_id_is_skipped(self) -> None:
        payload = {"features": [{"properties": {"alert_name_en": "x"}}]}
        assert alerts_mod.parse_alerts(payload) == []


class TestWeatherAlertsTask:
    async def test_first_run_primes_without_announcing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=AQW)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == {"20330325021"}

    async def test_query_is_scoped_to_ottawa(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = fake_httpx_client(monkeypatch, payload=EMPTY)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert client.request_params == alerts_mod._PARAMS
        assert alerts_mod._PARAMS["bbox"] == "-76.1,45.15,-75.4,45.55"

    async def test_second_run_announces_new_alert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        updated = with_feature(
            AQW,
            id="88888_fea1",
            feature_id="fea1",
            name="severe thunderstorm watch",
            published="2026-07-16T22:13:50.000Z",
        )
        fake_httpx_client(monkeypatch, payload=updated)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["Severe Thunderstorm Watch"]

    async def test_ongoing_multi_polygon_alert_is_not_reannounced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The AQW is primed as two polygons; on the next fetch its polygons
        # differ (EC returns a different polygon set) but the bulletin id is
        # unchanged, so it must not be announced again.
        fake_httpx_client(monkeypatch, payload=AQW)
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
        fake_httpx_client(monkeypatch, payload=reshaped)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []

    async def test_two_alerts_in_one_bulletin_are_both_announced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The old battleboard keying collapsed two alerts issued in one
        # bulletin (same region:timestamp) to one; distinct bulletin ids fix
        # that. Each new alert gets its own message, oldest-first.
        fake_httpx_client(monkeypatch, payload=EMPTY)
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
        fake_httpx_client(monkeypatch, payload=two)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["Heat Warning", "Severe Thunderstorm Watch"]

    async def test_unchanged_collection_announces_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=AQW)
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

    async def test_all_clear_announced_once_when_alerts_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run

        fake_httpx_client(monkeypatch, payload=EMPTY)  # alerts cleared
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == ["No alerts in effect"]
        assert alerts_mod._seen == set()

        # A subsequent empty fetch must not repeat the all-clear.
        fake_httpx_client(monkeypatch, payload=EMPTY)
        replies = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []

    async def test_seen_keys_are_pruned_when_alerts_leave(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _seen must track the live collection, not grow forever on a
        # long-running bot: once an alert is gone, its key goes too.
        fake_httpx_client(monkeypatch, payload=FIXTURE_PAYLOAD)
        await alerts_mod.weather_alerts(make_ctx([]))  # primes AQW + heat
        assert len(alerts_mod._seen) == 2

        fake_httpx_client(monkeypatch, payload=AQW)  # only the AQW remains
        await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._seen == {"20330325021"}

    async def test_no_active_alerts_primes_to_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=EMPTY)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == set()
        assert alerts_mod._primed is True


class TestConditionalFetch:
    HEADERS = {
        "ETag": 'W/"5dc-s++oJBiKg16nEaqG1KtMPI5AYfk"',
        "Last-Modified": "Fri, 10 Jul 2026 13:30:19 GMT",
    }

    async def test_first_fetch_is_unconditional(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = fake_httpx_client(monkeypatch, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert client.request_headers == {}

    async def test_validators_from_response_are_sent_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=AQW, response_headers=self.HEADERS)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._etag == self.HEADERS["ETag"]
        assert alerts_mod._last_modified == self.HEADERS["Last-Modified"]

        client = fake_httpx_client(monkeypatch, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert client.request_headers == {
            "If-None-Match": self.HEADERS["ETag"],
            "If-Modified-Since": self.HEADERS["Last-Modified"],
        }

    async def test_not_modified_announces_nothing_and_keeps_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=AQW, response_headers=self.HEADERS)
        await alerts_mod.weather_alerts(make_ctx([]))  # priming run
        seen_before = set(alerts_mod._seen)

        fake_httpx_client(monkeypatch, status_code=304)
        replies: list[str] = []
        await alerts_mod.weather_alerts(make_ctx(replies))
        assert replies == []
        assert alerts_mod._seen == seen_before
        assert alerts_mod._etag == self.HEADERS["ETag"]

    async def test_apache_gzip_etag_suffix_is_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Apache's mod_deflate serves W/"...-gzip" ETags it then refuses to
        # match; the stored validator must be the stripped form.
        fake_httpx_client(
            monkeypatch,
            payload=AQW,
            response_headers={"ETag": 'W/"5dc-s++oJBiKg16nEaqG1KtMPI5AYfk-gzip"'},
        )
        await alerts_mod.weather_alerts(make_ctx([]))
        assert alerts_mod._etag == 'W/"5dc-s++oJBiKg16nEaqG1KtMPI5AYfk"'

    def test_normalize_etag_leaves_plain_etags_alone(self) -> None:
        assert alerts_mod.normalize_etag('W/"abc"') == 'W/"abc"'
        assert alerts_mod.normalize_etag('"abc"') == '"abc"'

    async def test_missing_validators_send_no_conditional_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, payload=AQW)  # response has no cache headers
        await alerts_mod.weather_alerts(make_ctx([]))

        client = fake_httpx_client(monkeypatch, payload=AQW)
        await alerts_mod.weather_alerts(make_ctx([]))
        assert client.request_headers == {}
