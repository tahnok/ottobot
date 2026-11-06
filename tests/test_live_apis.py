"""Check the real upstream APIs still look the way the scheduled tasks expect.

Everything else runs against fixtures, which keep passing if Environment
Canada or KUBRA changes a URL or response shape; the only sign of that would
be the bot going quiet on #ott-alerts. These hit the real endpoints, so they
only run on request (`uv run pytest -m live`), daily via
.github/workflows/live-apis.yml. They check shape only: the values change by
the minute, and most of the time there's little happening in them.
"""

from typing import Any

import httpx
import pytest

from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.tasks import hydro_outages as outages_mod
from ottobot.tasks import weather_alerts as alerts_mod

pytestmark = pytest.mark.live

# The values of status_en, one of which (ENDED) the task acts on. A new one
# isn't necessarily a problem, but an unrecognized "ended" would announce
# weather that's already over, so it's worth a look.
KNOWN_ALERT_STATUSES = {"issued", "continued", alerts_mod.ENDED}


async def get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params)
    response.raise_for_status()
    return response


async def get_interval_path() -> str:
    state = (await get(outages_mod.CURRENT_STATE_URL)).json()
    return state["data"]["interval_generation_data"]


class TestWeatherAlerts:
    async def test_the_bots_own_query_still_works(self) -> None:
        response = await get(alerts_mod.ALERTS_URL, params=alerts_mod._PARAMS)
        assert isinstance(response.json()["features"], list)
        assert isinstance(await alerts_mod.fetch_alerts(), list)

    async def test_alerts_carry_the_fields_the_task_reads(self) -> None:
        # Ottawa usually has no alerts in effect, which would leave nothing to
        # check, so this drops the bbox and looks at all of Canada instead.
        params = {
            key: value for key, value in alerts_mod._PARAMS.items() if key != "bbox"
        }
        features = (await get(alerts_mod.ALERTS_URL, params=params)).json()["features"]
        if not features:
            pytest.skip("no weather alerts in effect anywhere in Canada")

        fields = set(str(alerts_mod._PARAMS["properties"]).split(","))
        for feature in features:
            props = feature["properties"]
            assert fields <= props.keys()
            assert props["status_en"] in KNOWN_ALERT_STATUSES
            # Polygon dedup relies on the id being <bulletin>_<feature_id>.
            key = alerts_mod.alert_key(props["id"], props["feature_id"])
            assert key and key != props["id"]
        alerts_mod.parse_alerts({"features": features})


class TestHydroOutages:
    async def test_current_state(self) -> None:
        response = await get(outages_mod.CURRENT_STATE_URL)
        assert isinstance(response.json()["updatedAt"], int)
        assert isinstance(response.json()["data"]["interval_generation_data"], str)
        # The validator that makes polling cheap for KUBRA.
        assert "Last-Modified" in response.headers

    async def test_summary(self) -> None:
        url = outages_mod.summary_url(await get_interval_path())
        file_data = (await get(url)).json()["summaryFileData"]
        totals = file_data["totals"][0]
        assert outages_mod._val(totals["total_cust_a"]) >= 0
        assert outages_mod._val(totals["total_outages"]) >= 0
        assert isinstance(file_data["page_mode"]["mode"], str)

    async def test_ward_report(self) -> None:
        url = outages_mod.ward_report_url(await get_interval_path())
        report = (await get(url)).json()
        areas = report["file_data"]["areas"]
        # The report lists every ward, whether it has outages or not.
        assert areas
        for area in areas:
            assert area["name"]
            assert outages_mod._val(area["n_out"]) >= 0
            assert outages_mod._val(area["cust_a"]) >= 0
        outages_mod.affected_areas(report)

    async def test_the_task_runs_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outages_mod, "_last_updated_at", None)
        monkeypatch.setattr(outages_mod, "_state_last_modified", None)
        monkeypatch.setattr(outages_mod, "_announced_customers", None)

        async def reply(text: str) -> None:
            raise AssertionError("task should return its announcement, not reply()")

        ctx = TaskContext(_reply=reply, config=BotConfig())
        result = await outages_mod.hydro_outages(ctx)
        assert result is None or result.startswith("Hydro Ottawa: ")
        assert outages_mod._last_updated_at is not None
