"""Tests for the rss scheduled task."""

from typing import Any

import pytest

from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.tasks import rss as rss_mod

FEED_URL = "https://example.com/feed.xml"

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>First post</title><link>https://example.com/1</link><guid>1</guid></item>
<item><title>Second post</title><link>https://example.com/2</link><guid>2</guid></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rss_mod, "_seen", set())
    monkeypatch.setattr(rss_mod, "_primed", False)


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
    monkeypatch.setattr(rss_mod.httpx, "AsyncClient", lambda **_: FakeClient(**kwargs))


def make_ctx(replies: list[str], url: str | None = FEED_URL) -> TaskContext:
    async def reply(text: str) -> None:
        replies.append(text)

    return TaskContext(_reply=reply, config=BotConfig(rss_feed_url=url))


class TestParseItems:
    def test_parses_title_link_guid(self) -> None:
        assert rss_mod.parse_items(FEED) == [
            ("1", "First post"),
            ("2", "Second post"),
        ]

    def test_falls_back_to_link_when_no_guid(self) -> None:
        xml = (
            "<rss><channel><item><title>T</title>"
            "<link>https://example.com/1</link></item></channel></rss>"
        )
        assert rss_mod.parse_items(xml) == [("https://example.com/1", "T")]

    def test_falls_back_to_link_when_no_title(self) -> None:
        xml = (
            "<rss><channel><item>"
            "<link>https://example.com/1</link><guid>1</guid></item></channel></rss>"
        )
        assert rss_mod.parse_items(xml) == [("1", "https://example.com/1")]

    def test_item_without_guid_or_link_is_skipped(self) -> None:
        xml = "<rss><channel><item><title>orphan</title></item></channel></rss>"
        assert rss_mod.parse_items(xml) == []


class TestRssTask:
    async def test_no_url_configured_does_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        replies: list[str] = []
        await rss_mod.rss(make_ctx(replies, url=None))
        assert replies == []

    async def test_first_run_primes_without_announcing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)
        replies: list[str] = []
        await rss_mod.rss(make_ctx(replies))
        assert replies == []
        assert rss_mod._seen == {"1", "2"}

    async def test_second_run_announces_only_new_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)
        await rss_mod.rss(make_ctx([]))  # priming run

        # Feeds list newest items first; two new items land at the top.
        updated = FEED.replace(
            "<channel>\n",
            "<channel>\n"
            "<item><title>Fourth post</title><link>https://example.com/4</link><guid>4</guid></item>\n"
            "<item><title>Third post</title><link>https://example.com/3</link><guid>3</guid></item>\n",
        )
        fake_httpx_client(monkeypatch, text=updated)
        replies: list[str] = []
        await rss_mod.rss(make_ctx(replies))
        # Announced oldest-of-the-new-batch first.
        assert replies == ["Third post", "Fourth post"]
        assert rss_mod._seen == {"1", "2", "3", "4"}

    async def test_fetch_failure_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, error=RuntimeError("network is down"))
        replies: list[str] = []
        await rss_mod.rss(make_ctx(replies))
        assert replies == []
        assert rss_mod._primed is False

    async def test_unchanged_feed_announces_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_httpx_client(monkeypatch, text=FEED)
        await rss_mod.rss(make_ctx([]))  # priming run
        replies: list[str] = []
        await rss_mod.rss(make_ctx(replies))
        assert replies == []
