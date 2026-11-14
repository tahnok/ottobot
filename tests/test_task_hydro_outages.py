"""Tests for the hydro_outages scheduled task."""

import json
from typing import Any

import httpx
import pytest
import respx

from ottobot.channels import OTT_ALERTS
from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.registry import module_tasks
from ottobot.tasks import hydro_outages as outages_mod


def current_state(updated_at: int = 1000, interval: str = "data/guid-1") -> dict:
    return {
        "stormcenterDeploymentId": "fa5c4be8-143c-42fb-8ed2-de34b28e0dea",
        "updatedAt": updated_at,
        "data": {"interval_generation_data": interval},
    }


def summary(customers: int = 0, outages: int = 0, mode: str = "BLUESKY") -> dict:
    return {
        "summaryFileData": {
            "totals": [
                {
                    "total_outages": outages,
                    "total_cust_a": {"val": customers},
                    "total_cust_s": 378693,
                }
            ],
            "date_generated": "2026-06-30T21:34:27Z",
            "page_mode": {"mode": mode},
        }
    }


def ward_report(*areas: tuple[str, int, int]) -> dict:
    """Build a report from (name, n_out, cust_a) tuples, padded with quiet wards."""
    quiet = [
        {"name": name, "n_out": 0, "cust_a": {"val": 0}, "cust_s": 20000, "etr": None}
        for name in ("Alta Vista", "Barrhaven East")
    ]
    return {
        "file_data": {
            "areas": quiet
            + [
                {
                    "name": name,
                    "n_out": n_out,
                    "cust_a": {"val": cust_a},
                    "cust_s": 24803,
                    "etr": "2026-06-30T23:00:00Z",
                }
                for name, n_out, cust_a in areas
            ]
        }
    }


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outages_mod, "_last_updated_at", None)
    monkeypatch.setattr(outages_mod, "_state_last_modified", None)
    monkeypatch.setattr(outages_mod, "_announced_customers", None)


# Sentinel response value: reply with a bodyless 304 Not Modified.
NOT_MODIFIED = object()


def serve(
    api: respx.MockRouter,
    responses: dict[str, Any],
    response_headers: dict[str, str] | None = None,
) -> None:
    """Serve exactly these URLs from now on; any other fetch fails the test.

    A value is JSON to serve, or NOT_MODIFIED, or an httpx.Response to send
    as is, or an Exception to raise from the transport.
    """
    api.clear()
    api.reset()
    for url, value in responses.items():
        route = api.get(url)
        if isinstance(value, Exception):
            route.mock(side_effect=value)
        elif isinstance(value, httpx.Response):
            route.mock(return_value=value)
        elif value is NOT_MODIFIED:
            route.mock(return_value=httpx.Response(304))
        else:
            route.mock(
                return_value=httpx.Response(200, json=value, headers=response_headers)
            )


def requested(api: respx.MockRouter) -> list[str]:
    return [str(call.request.url) for call in api.calls]


def snapshot_responses(
    updated_at: int = 1000,
    interval: str = "data/guid-1",
    customers: int = 0,
    outages: int = 0,
    mode: str = "BLUESKY",
    areas: tuple[tuple[str, int, int], ...] = (),
) -> dict[str, Any]:
    return {
        outages_mod.CURRENT_STATE_URL: current_state(updated_at, interval),
        outages_mod.summary_url(interval): summary(customers, outages, mode),
        outages_mod.ward_report_url(interval): ward_report(*areas),
    }


def make_ctx() -> TaskContext:
    async def reply(text: str) -> None:
        raise AssertionError("task should return its announcement, not reply()")

    return TaskContext(_reply=reply, config=BotConfig())


def test_task_announces_on_the_ott_alerts_channel() -> None:
    (scheduled,) = module_tasks(outages_mod)
    assert scheduled.channel is OTT_ALERTS


class TestVal:
    def test_unwraps_kubra_val_dict(self) -> None:
        assert outages_mod._val({"val": 202}) == 202

    def test_plain_number_passes_through(self) -> None:
        assert outages_mod._val(4) == 4

    def test_missing_or_null_values_raise(self) -> None:
        # A zero invented from absent data could announce a bogus
        # all-clear; missing fields must fail the poll instead.
        with pytest.raises(ValueError):
            outages_mod._val(None)
        with pytest.raises(ValueError):
            outages_mod._val({})
        with pytest.raises(ValueError):
            outages_mod._val({"val": None})


class TestAffectedAreas:
    def test_filters_quiet_wards_and_sorts_worst_first(self) -> None:
        report = ward_report(("Somerset", 1, 162), ("Stittsville", 2, 400))
        assert outages_mod.affected_areas(report) == [
            ("Stittsville", 400),
            ("Somerset", 162),
        ]

    def test_drops_french_half_of_bilingual_names(self) -> None:
        report = ward_report(("Kanata South / Kanata-Sud", 1, 14))
        assert outages_mod.affected_areas(report) == [("Kanata South", 14)]


class TestFormatAnnouncement:
    def test_lists_areas(self) -> None:
        text = outages_mod.format_announcement(
            700, 2, [("Somerset", 500), ("Stittsville", 200)], storm=False
        )
        assert text == (
            "Hydro Ottawa: 700 customers out (2 outages): "
            "Somerset 500, Stittsville 200"
        )

    def test_singular_outage(self) -> None:
        text = outages_mod.format_announcement(600, 1, [("Somerset", 600)], storm=False)
        assert "(1 outage)" in text

    def test_storm_mode_is_called_out(self) -> None:
        text = outages_mod.format_announcement(
            21500, 80, [("Somerset", 5000)], storm=True
        )
        assert text.startswith("Hydro Ottawa: STORM mode, 21,500 customers out")

    def test_overflow_areas_fold_into_more(self) -> None:
        areas = [(f"Ward Number {i}", 1000 - i) for i in range(12)]
        text = outages_mod.format_announcement(9000, 12, areas, storm=False)
        assert len(text.encode("utf-8")) <= outages_mod.MAX_MESSAGE_LEN
        assert "Ward Number 0 1,000" in text
        assert "more" in text

    def test_fits_budget_measured_in_utf8_bytes(self) -> None:
        # "Orléans" is 8 bytes for 7 characters; the packet limit is bytes.
        areas = [
            ("Orléans East-Cumberland / Orléans-Est-Cumberland", 100 + i)
            for i in range(6)
        ]
        text = outages_mod.format_announcement(600, 6, areas, storm=False)
        assert len(text.encode("utf-8")) <= outages_mod.MAX_MESSAGE_LEN

    def test_no_areas_still_announces_totals(self) -> None:
        text = outages_mod.format_announcement(600, 3, [], storm=False)
        assert text == "Hydro Ottawa: 600 customers out (3 outages)"


class TestHydroOutagesTask:
    async def test_small_outage_is_not_announced(self, api: respx.MockRouter) -> None:
        serve(api, snapshot_responses(customers=200, outages=4))
        assert await outages_mod.hydro_outages(make_ctx()) is None
        # The ward report is only fetched when something will be announced.
        assert requested(api) == [
            outages_mod.CURRENT_STATE_URL,
            outages_mod.summary_url("data/guid-1"),
        ]

    async def test_significant_outage_is_announced_with_wards(
        self, api: respx.MockRouter
    ) -> None:
        serve(
            api,
            snapshot_responses(
                customers=800,
                outages=2,
                areas=(("Somerset", 1, 600), ("Stittsville", 1, 200)),
            ),
        )
        result = await outages_mod.hydro_outages(make_ctx())
        assert result == (
            "Hydro Ottawa: 800 customers out (2 outages): "
            "Somerset 600, Stittsville 200"
        )
        assert outages_mod._announced_customers == 800

    async def test_unchanged_snapshot_fetches_nothing_downstream(
        self, api: respx.MockRouter
    ) -> None:
        serve(api, snapshot_responses(customers=200))
        await outages_mod.hydro_outages(make_ctx())

        serve(api, {outages_mod.CURRENT_STATE_URL: current_state()})
        assert await outages_mod.hydro_outages(make_ctx()) is None
        assert requested(api) == [outages_mod.CURRENT_STATE_URL]

    async def test_no_repeat_announcement_while_outage_persists(
        self, api: respx.MockRouter
    ) -> None:
        serve(
            api,
            snapshot_responses(customers=800, outages=2, areas=(("Somerset", 2, 800),)),
        )
        assert await outages_mod.hydro_outages(make_ctx()) is not None

        # Next snapshot, same-ish outage: stay quiet.
        serve(
            api,
            snapshot_responses(
                updated_at=2000, customers=900, outages=2, areas=(("Somerset", 2, 900),)
            ),
        )
        assert await outages_mod.hydro_outages(make_ctx()) is None

    async def test_doubling_outage_is_reannounced(self, api: respx.MockRouter) -> None:
        serve(
            api,
            snapshot_responses(customers=800, outages=2, areas=(("Somerset", 2, 800),)),
        )
        await outages_mod.hydro_outages(make_ctx())

        serve(
            api,
            snapshot_responses(
                updated_at=2000,
                customers=1700,
                outages=5,
                areas=(("Somerset", 5, 1700),),
            ),
        )
        result = await outages_mod.hydro_outages(make_ctx())
        assert result is not None and "1,700 customers out" in result
        assert outages_mod._announced_customers == 1700

    async def test_all_clear_is_announced_once(self, api: respx.MockRouter) -> None:
        serve(
            api,
            snapshot_responses(customers=800, outages=2, areas=(("Somerset", 2, 800),)),
        )
        await outages_mod.hydro_outages(make_ctx())

        serve(api, snapshot_responses(updated_at=2000, customers=12, outages=1))
        result = await outages_mod.hydro_outages(make_ctx())
        assert result == ("Hydro Ottawa: power mostly restored, 12 customers still out")
        assert outages_mod._announced_customers is None

        # Staying quiet afterwards: no all-clear repeats, no new announcement.
        serve(api, snapshot_responses(updated_at=3000, customers=0, outages=0))
        assert await outages_mod.hydro_outages(make_ctx()) is None

    async def test_partial_restoration_is_not_announced(
        self, api: respx.MockRouter
    ) -> None:
        serve(
            api,
            snapshot_responses(customers=800, outages=2, areas=(("Somerset", 2, 800),)),
        )
        await outages_mod.hydro_outages(make_ctx())

        # Down to 300: below the announce threshold but not yet resolved —
        # neither a new announcement nor an all-clear.
        serve(
            api,
            snapshot_responses(updated_at=2000, customers=300, outages=1),
        )
        assert await outages_mod.hydro_outages(make_ctx()) is None
        assert outages_mod._announced_customers == 800

    async def test_full_restoration_message(self, api: respx.MockRouter) -> None:
        serve(
            api,
            snapshot_responses(customers=800, outages=2, areas=(("Somerset", 2, 800),)),
        )
        await outages_mod.hydro_outages(make_ctx())

        serve(api, snapshot_responses(updated_at=2000, customers=0, outages=0))
        result = await outages_mod.hydro_outages(make_ctx())
        assert result == "Hydro Ottawa: power restored"

    async def test_storm_mode_announces_below_threshold(
        self, api: respx.MockRouter
    ) -> None:
        serve(
            api,
            snapshot_responses(
                customers=100, outages=3, mode="STORM", areas=(("Somerset", 3, 100),)
            ),
        )
        result = await outages_mod.hydro_outages(make_ctx())
        assert result is not None and "STORM mode" in result

    async def test_no_all_clear_while_storm_mode_persists(
        self, api: respx.MockRouter
    ) -> None:
        serve(
            api,
            snapshot_responses(
                customers=100, outages=3, mode="STORM", areas=(("Somerset", 3, 100),)
            ),
        )
        await outages_mod.hydro_outages(make_ctx())

        # Still STORM with few customers out: don't flap into an all-clear.
        serve(
            api,
            snapshot_responses(updated_at=2000, customers=10, outages=1, mode="STORM"),
        )
        assert await outages_mod.hydro_outages(make_ctx()) is None
        assert outages_mod._announced_customers == 100

    async def test_fetch_failure_propagates_without_touching_state(
        self, api: respx.MockRouter
    ) -> None:
        # No local catch-all: the runner logs a raising task and carries on.
        serve(
            api,
            {outages_mod.CURRENT_STATE_URL: httpx.ConnectError("network is down")},
        )
        with pytest.raises(httpx.ConnectError):
            await outages_mod.hydro_outages(make_ctx())
        assert outages_mod._last_updated_at is None

    async def test_failed_summary_fetch_is_retried_next_poll(
        self, api: respx.MockRouter
    ) -> None:
        # currentState succeeds but the snapshot files error: the snapshot
        # must not be marked processed, so the next poll retries it.
        serve(
            api,
            {
                outages_mod.CURRENT_STATE_URL: current_state(),
                outages_mod.summary_url("data/guid-1"): httpx.Response(500),
            },
        )
        with pytest.raises(httpx.HTTPStatusError):
            await outages_mod.hydro_outages(make_ctx())
        assert outages_mod._last_updated_at is None

        serve(
            api,
            snapshot_responses(customers=800, outages=1, areas=(("Somerset", 1, 800),)),
        )
        assert await outages_mod.hydro_outages(make_ctx()) is not None

    async def test_summary_missing_customer_count_fails_the_poll(
        self, api: respx.MockRouter
    ) -> None:
        # An API change that drops total_cust_a must not read as "0
        # customers out" (which could fire a bogus all-clear); the poll
        # fails and the snapshot stays unprocessed.
        responses = snapshot_responses(customers=800, outages=2)
        broken = summary(customers=800, outages=2)
        del broken["summaryFileData"]["totals"][0]["total_cust_a"]
        responses[outages_mod.summary_url("data/guid-1")] = broken
        serve(api, responses)
        with pytest.raises(ValueError):
            await outages_mod.hydro_outages(make_ctx())
        assert outages_mod._last_updated_at is None
        assert outages_mod._announced_customers is None


class TestConditionalFetch:
    LAST_MODIFIED = {"Last-Modified": "Thu, 16 Jul 2026 11:19:47 GMT"}

    async def test_first_fetch_is_unconditional(self, api: respx.MockRouter) -> None:
        serve(api, snapshot_responses(customers=200))
        await outages_mod.hydro_outages(make_ctx())
        assert "If-Modified-Since" not in api.calls[0].request.headers

    async def test_last_modified_is_sent_back(self, api: respx.MockRouter) -> None:
        serve(
            api,
            snapshot_responses(customers=200),
            response_headers=self.LAST_MODIFIED,
        )
        await outages_mod.hydro_outages(make_ctx())
        assert outages_mod._state_last_modified == self.LAST_MODIFIED["Last-Modified"]

        serve(api, snapshot_responses(customers=200))
        await outages_mod.hydro_outages(make_ctx())
        assert (
            api.calls[0].request.headers["If-Modified-Since"]
            == self.LAST_MODIFIED["Last-Modified"]
        )

    async def test_not_modified_fetches_nothing_downstream(
        self, api: respx.MockRouter
    ) -> None:
        serve(
            api,
            snapshot_responses(customers=200),
            response_headers=self.LAST_MODIFIED,
        )
        await outages_mod.hydro_outages(make_ctx())

        serve(api, {outages_mod.CURRENT_STATE_URL: NOT_MODIFIED})
        assert await outages_mod.hydro_outages(make_ctx()) is None
        assert requested(api) == [outages_mod.CURRENT_STATE_URL]
        assert outages_mod._state_last_modified == self.LAST_MODIFIED["Last-Modified"]

    async def test_validator_is_only_kept_after_a_successful_poll(
        self, api: respx.MockRouter
    ) -> None:
        # A failed downstream fetch must leave the next poll unconditional,
        # so the snapshot can't get stuck behind 304s.
        serve(
            api,
            {
                outages_mod.CURRENT_STATE_URL: current_state(),
                outages_mod.summary_url("data/guid-1"): httpx.Response(500),
            },
            response_headers=self.LAST_MODIFIED,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await outages_mod.hydro_outages(make_ctx())
        assert outages_mod._state_last_modified is None


class TestFailedFetch:
    """Failures propagate for the runner to log, leaving the poll retryable."""

    @staticmethod
    def assert_retryable() -> None:
        assert outages_mod._last_updated_at is None
        assert outages_mod._state_last_modified is None
        assert outages_mod._announced_customers is None

    # 301: httpx doesn't follow redirects unless told to, so a moved endpoint
    # fails here rather than being chased.
    @pytest.mark.parametrize("status", [301, 404, 429, 500, 503])
    @pytest.mark.parametrize(
        "failing",
        [
            outages_mod.CURRENT_STATE_URL,
            outages_mod.summary_url("data/guid-1"),
            outages_mod.ward_report_url("data/guid-1"),
        ],
    )
    async def test_error_status(
        self, api: respx.MockRouter, failing: str, status: int
    ) -> None:
        responses = snapshot_responses(customers=800, outages=1)
        serve(api, {**responses, failing: httpx.Response(status)})
        with pytest.raises(httpx.HTTPStatusError):
            await outages_mod.hydro_outages(make_ctx())
        self.assert_retryable()

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectTimeout("connect timed out"),
            httpx.ReadTimeout("read timed out"),
        ],
    )
    async def test_timeout(self, api: respx.MockRouter, error: Exception) -> None:
        serve(api, {outages_mod.CURRENT_STATE_URL: error})
        with pytest.raises(httpx.TimeoutException):
            await outages_mod.hydro_outages(make_ctx())
        self.assert_retryable()

    @pytest.mark.parametrize(
        "body", [b"<html>Service Unavailable</html>", b"", b'{"updatedAt": ']
    )
    @pytest.mark.parametrize(
        "failing",
        [
            outages_mod.CURRENT_STATE_URL,
            outages_mod.summary_url("data/guid-1"),
            outages_mod.ward_report_url("data/guid-1"),
        ],
    )
    async def test_malformed_json(
        self, api: respx.MockRouter, failing: str, body: bytes
    ) -> None:
        responses = snapshot_responses(customers=800, outages=1)
        serve(api, {**responses, failing: httpx.Response(200, content=body)})
        with pytest.raises(json.JSONDecodeError):
            await outages_mod.hydro_outages(make_ctx())
        self.assert_retryable()

    async def test_retried_after_a_transient_error(self, api: respx.MockRouter) -> None:
        serve(
            api,
            {outages_mod.CURRENT_STATE_URL: httpx.Response(503)},
        )
        with pytest.raises(httpx.HTTPStatusError):
            await outages_mod.hydro_outages(make_ctx())

        serve(
            api,
            snapshot_responses(customers=800, outages=1, areas=(("Somerset", 1, 800),)),
        )
        result = await outages_mod.hydro_outages(make_ctx())
        assert result == "Hydro Ottawa: 800 customers out (1 outage): Somerset 800"


async def test_request_carries_a_timeout(api: respx.MockRouter) -> None:
    serve(api, snapshot_responses(customers=200))
    await outages_mod.hydro_outages(make_ctx())
    assert {
        value
        for call in api.calls
        for value in call.request.extensions["timeout"].values()
    } == {20}
