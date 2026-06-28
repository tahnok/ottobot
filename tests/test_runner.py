from dataclasses import dataclass
from typing import Any

import pytest
from meshcore import EventType

from ottobot import Context, MeshBot
from ottobot.config import BotConfig, ChannelConfig, RadioConfig
from ottobot.runner import MeshCoreRunner, apply_settings


@dataclass
class FakeEvent:
    type: EventType
    payload: Any = None


class FakeCommands:
    def __init__(self) -> None:
        self.sent_msgs: list[tuple[dict[str, Any], str]] = []
        self.sent_chan_msgs: list[tuple[int, str]] = []
        self.fail_sends = False
        # Records device-setting calls for apply_settings tests.
        self.names: list[str] = []
        self.private_keys: list[bytes] = []
        self.channels: list[tuple[int, str, bytes | None]] = []
        self.radios: list[tuple[float, float, int, int]] = []

    async def send_msg(self, contact: dict[str, Any], text: str) -> FakeEvent:
        if self.fail_sends:
            return FakeEvent(EventType.ERROR, "no path")
        self.sent_msgs.append((contact, text))
        return FakeEvent(EventType.MSG_SENT, {"expected_ack": b"\x00"})

    async def send_chan_msg(self, channel_idx: int, text: str) -> FakeEvent:
        self.sent_chan_msgs.append((channel_idx, text))
        return FakeEvent(EventType.MSG_SENT, {"expected_ack": b"\x00"})

    async def set_name(self, name: str) -> FakeEvent:
        self.names.append(name)
        return FakeEvent(EventType.OK)

    async def import_private_key(self, key: bytes) -> FakeEvent:
        self.private_keys.append(key)
        return FakeEvent(EventType.OK)

    async def set_channel(
        self, channel_idx: int, channel_name: str, channel_secret: bytes | None = None
    ) -> FakeEvent:
        self.channels.append((channel_idx, channel_name, channel_secret))
        return FakeEvent(EventType.OK)

    async def set_radio(self, freq: float, bw: float, sf: int, cr: int) -> FakeEvent:
        self.radios.append((freq, bw, sf, cr))
        return FakeEvent(EventType.OK)


class FakeMeshCore:
    def __init__(
        self,
        contacts: dict[str, dict[str, Any]] | None = None,
        self_info: dict[str, Any] | None = None,
    ) -> None:
        self.commands = FakeCommands()
        self.callbacks: dict[EventType, Any] = {}
        # keyed by pubkey prefix, like get_contact_by_key_prefix expects
        self._contacts = contacts or {}
        self.self_info = self_info if self_info is not None else {"name": "ottobot"}
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
    bot = MeshBot(name="ottobot")

    @bot.command("ping")
    async def ping(ctx: Context) -> str:
        return "pong"

    @bot.command("whoami")
    async def whoami(ctx: Context) -> str:
        return ctx.sender_name or "unknown"

    @bot.command("path")
    async def path(ctx: Context) -> str:
        return ctx.path_description

    @bot.command("raw")
    async def raw(ctx: Context) -> str:
        assert ctx.raw is not None
        return f"{ctx.raw.get('pubkey_prefix')} {ctx.raw.get('channel_idx')}"

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

    async def test_dm_exposes_raw_payload(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_dm("!raw")
        assert mc.commands.sent_msgs == [(ALICE, "abcdef123456 None")]


class TestChannelMessages:
    async def test_channel_command_replies_on_same_channel(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !ping", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "ottobot: pong")]

    async def test_app_mention_addresses_the_bot(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        # As the MeshCore app sends it: "Sender: @[Bot] !command".
        await mc.deliver_chan("alice: @[ottobot] !ping", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "ottobot: pong")]

    async def test_sender_name_parsed_from_text_convention(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !whoami")
        assert mc.commands.sent_chan_msgs == [(0, "ottobot: alice")]

    async def test_text_without_name_prefix_is_ignored(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        # On a channel the bot only answers when addressed by name.
        await mc.deliver_chan("alice: !ping")
        assert mc.commands.sent_chan_msgs == []

    async def test_channel_path_is_passed_through(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !path", path_len=255)
        assert mc.commands.sent_chan_msgs == [(0, "ottobot: direct")]

    async def test_channel_exposes_raw_payload(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !raw", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "ottobot: None 2")]

    async def test_channel_reply_is_prefixed_so_a_colon_survives_round_trip(
        self, mc: FakeMeshCore
    ) -> None:
        # A reply whose body contains a colon must stay intact when another
        # node re-parses it with the "Name: message" convention.
        bot = MeshBot(name="ottobot")

        @bot.command("time")
        async def time(ctx: Context) -> str:
            return "10:30"

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await mc.deliver_chan("alice: ottobot !time")
        assert mc.commands.sent_chan_msgs == [(0, "ottobot: 10:30")]
        # Re-parsing it the way _on_channel_msg parses incoming text keeps
        # the body whole and attributes it to the bot.
        sent_text = mc.commands.sent_chan_msgs[0][1]
        name, _, body = sent_text.partition(":")
        assert name == "ottobot"
        assert body.strip() == "10:30"

    async def test_channel_messages_ignored_when_bot_disabled_for_channels(
        self, mc: FakeMeshCore
    ) -> None:
        bot = MeshBot(name="ottobot", respond_in_channels=False)

        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await mc.deliver_chan("alice: @[ottobot] !ping")
        assert mc.commands.sent_chan_msgs == []


class TestApplySettings:
    async def test_applies_every_field(self, mc: FakeMeshCore) -> None:
        config = BotConfig(
            name="ottobot",
            private_key=b"\x01" * 64,
            channels=(
                ChannelConfig(0, "public"),
                ChannelConfig(1, "private", b"\x02" * 16),
            ),
            radio=RadioConfig(freq=910.525, bw=250.0, sf=11, cr=5),
        )
        await apply_settings(mc, config)
        assert mc.commands.names == ["ottobot"]
        assert mc.commands.private_keys == [b"\x01" * 64]
        assert mc.commands.channels == [
            (0, "public", None),
            (1, "private", b"\x02" * 16),
        ]
        assert mc.commands.radios == [(910.525, 250.0, 11, 5)]

    async def test_skips_absent_fields(self, mc: FakeMeshCore) -> None:
        await apply_settings(mc, BotConfig(name="ottobot"))
        assert mc.commands.names == ["ottobot"]
        assert mc.commands.private_keys == []
        assert mc.commands.channels == []
        assert mc.commands.radios == []

    async def test_empty_config_applies_nothing(self, mc: FakeMeshCore) -> None:
        await apply_settings(mc, BotConfig())
        assert mc.commands.names == []
        assert mc.commands.private_keys == []
        assert mc.commands.channels == []
        assert mc.commands.radios == []
