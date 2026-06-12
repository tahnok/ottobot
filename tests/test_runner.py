from dataclasses import dataclass
from typing import Any

import pytest
from meshcore import EventType

from ottawa_meshbot import Context, MeshBot
from ottawa_meshbot.runner import MeshCoreRunner


@dataclass
class FakeEvent:
    type: EventType
    payload: Any = None


class FakeCommands:
    def __init__(self) -> None:
        self.sent_msgs: list[tuple[dict[str, Any], str]] = []
        self.sent_chan_msgs: list[tuple[int, str]] = []
        self.fail_sends = False

    async def send_msg(self, contact: dict[str, Any], text: str) -> FakeEvent:
        if self.fail_sends:
            return FakeEvent(EventType.ERROR, "no path")
        self.sent_msgs.append((contact, text))
        return FakeEvent(EventType.MSG_SENT, {"expected_ack": b"\x00"})

    async def send_chan_msg(self, channel_idx: int, text: str) -> FakeEvent:
        self.sent_chan_msgs.append((channel_idx, text))
        return FakeEvent(EventType.MSG_SENT, {"expected_ack": b"\x00"})


class FakeMeshCore:
    def __init__(self, contacts: dict[str, dict[str, Any]] | None = None) -> None:
        self.commands = FakeCommands()
        self.callbacks: dict[EventType, Any] = {}
        # keyed by pubkey prefix, like get_contact_by_key_prefix expects
        self._contacts = contacts or {}
        self.ensure_contacts_calls = 0
        self.fetching = False

    def subscribe(self, event_type: EventType, callback: Any) -> tuple[EventType, Any]:
        self.callbacks[event_type] = callback
        return (event_type, callback)

    def unsubscribe(self, subscription: tuple[EventType, Any]) -> None:
        self.callbacks.pop(subscription[0], None)

    async def ensure_contacts(self) -> bool:
        self.ensure_contacts_calls += 1
        return True

    def get_contact_by_key_prefix(self, prefix: str) -> dict[str, Any] | None:
        return self._contacts.get(prefix)

    async def start_auto_message_fetching(self) -> None:
        self.fetching = True

    async def stop_auto_message_fetching(self) -> None:
        self.fetching = False

    async def deliver_dm(
        self,
        text: str,
        pubkey_prefix: str = "abcdef123456",
        path_len: int | None = None,
        path: str | None = None,
        path_hash_mode: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "PRIV",
            "pubkey_prefix": pubkey_prefix,
            "text": text,
        }
        if path_len is not None:
            payload["path_len"] = path_len
        if path is not None:
            payload["path"] = path
        if path_hash_mode is not None:
            payload["path_hash_mode"] = path_hash_mode
        event = FakeEvent(EventType.CONTACT_MSG_RECV, payload)
        await self.callbacks[EventType.CONTACT_MSG_RECV](event)

    async def deliver_chan(
        self,
        text: str,
        channel_idx: int = 0,
        path_len: int | None = None,
        path: str | None = None,
        path_hash_mode: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "CHAN",
            "channel_idx": channel_idx,
            "text": text,
        }
        if path_len is not None:
            payload["path_len"] = path_len
        if path is not None:
            payload["path"] = path
        if path_hash_mode is not None:
            payload["path_hash_mode"] = path_hash_mode
        event = FakeEvent(EventType.CHANNEL_MSG_RECV, payload)
        await self.callbacks[EventType.CHANNEL_MSG_RECV](event)


ALICE = {"adv_name": "alice", "public_key": "abcdef123456" + "0" * 52}


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot()

    @bot.command("ping")
    async def ping(ctx: Context) -> str:
        return "pong"

    @bot.command("whoami")
    async def whoami(ctx: Context) -> str:
        return ctx.sender_name or "unknown"

    @bot.command("path")
    async def path(ctx: Context) -> str:
        return ctx.path_description

    return bot


@pytest.fixture
def mc() -> FakeMeshCore:
    return FakeMeshCore(contacts={"abcdef123456": ALICE})


@pytest.fixture
async def runner(bot: MeshBot, mc: FakeMeshCore) -> MeshCoreRunner:
    runner = MeshCoreRunner(bot, mc)
    await runner.start()
    return runner


class TestLifecycle:
    async def test_start_subscribes_and_begins_fetching(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        assert EventType.CONTACT_MSG_RECV in mc.callbacks
        assert EventType.CHANNEL_MSG_RECV in mc.callbacks
        assert mc.fetching

    async def test_stop_unsubscribes_and_stops_fetching(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await runner.stop()
        assert mc.callbacks == {}
        assert not mc.fetching


class TestDirectMessages:
    async def test_dm_command_replies_to_sender(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_dm("!ping")
        assert mc.commands.sent_msgs == [(ALICE, "pong")]

    async def test_dm_sender_name_comes_from_contact(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_dm("!whoami")
        assert mc.commands.sent_msgs == [(ALICE, "alice")]

    async def test_dm_from_unknown_contact_refreshes_then_ignores(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        before = mc.ensure_contacts_calls
        await mc.deliver_dm("!ping", pubkey_prefix="000000000000")
        assert mc.ensure_contacts_calls == before + 1
        assert mc.commands.sent_msgs == []

    async def test_non_command_dm_sends_nothing(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_dm("hello bot")
        assert mc.commands.sent_msgs == []

    async def test_failed_send_does_not_raise(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        mc.commands.fail_sends = True
        await mc.deliver_dm("!ping")
        assert mc.commands.sent_msgs == []

    async def test_dm_path_is_passed_through(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_dm("!path", path_len=2, path="a1b2", path_hash_mode=0)
        assert mc.commands.sent_msgs == [(ALICE, "2 hops via a1,b2")]

    async def test_dm_path_with_3_byte_hashes(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_dm("!path", path_len=2, path="a1b2c3d4e5f6", path_hash_mode=2)
        assert mc.commands.sent_msgs == [(ALICE, "2 hops via a1b2c3,d4e5f6")]

    async def test_dm_without_path_info_reports_unknown(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_dm("!path")
        assert mc.commands.sent_msgs == [(ALICE, "unknown path")]


class TestChannelMessages:
    async def test_channel_command_replies_on_same_channel(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: !ping", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "pong")]

    async def test_sender_name_parsed_from_text_convention(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: !whoami")
        assert mc.commands.sent_chan_msgs == [(0, "alice")]

    async def test_text_without_name_prefix_still_dispatches(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("!ping")
        assert mc.commands.sent_chan_msgs == [(0, "pong")]

    async def test_channel_path_is_passed_through(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: !path", path_len=255)
        assert mc.commands.sent_chan_msgs == [(0, "direct")]

    async def test_channel_messages_ignored_when_bot_disabled_for_channels(
        self, mc: FakeMeshCore
    ) -> None:
        bot = MeshBot(respond_in_channels=False)

        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await mc.deliver_chan("alice: !ping")
        assert mc.commands.sent_chan_msgs == []
