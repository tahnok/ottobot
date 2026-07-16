import asyncio
from datetime import timedelta
from typing import Any

import pytest
from meshcore import EventType
from meshcore.events import Event

from ottobot import Context, Ottobot, TaskContext
from ottobot.channels import PUBLIC, ChannelConfig
from ottobot.config import BotConfig
from ottobot.radio import RADIO
from ottobot.runner import (
    PUBLIC_CHANNEL_KEY,
    MeshCoreRunner,
    apply_settings,
    fetch_channels,
)


class FakeEvent(Event):
    def __init__(self, type: EventType, payload: Any = None) -> None:
        super().__init__(type, payload)


class FakeCommands:
    def __init__(self) -> None:
        self.sent_chan_msgs: list[tuple[int, str]] = []
        # Records device-setting calls for apply_settings tests.
        self.names: list[str] = []
        self.private_keys: list[bytes] = []
        self.channels: list[tuple[int, str, bytes | None]] = []
        self.radios: list[tuple[float, float, int, int]] = []
        self.path_hash_modes: list[int] = []
        # Channels the fake device reports back via get_channel, keyed by
        # index; each value is the CHANNEL_INFO-style name. Empty by default.
        self.device_channels: dict[int, str] = {}

    async def get_channel(self, channel_idx: int) -> FakeEvent:
        name = self.device_channels.get(channel_idx, "")
        return FakeEvent(
            EventType.CHANNEL_INFO,
            {
                "channel_idx": channel_idx,
                "channel_name": name,
                "channel_secret": b"\x00" * 16,
                "channel_hash": f"{channel_idx:02x}",
            },
        )

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

    async def set_path_hash_mode(self, mode: int) -> FakeEvent:
        self.path_hash_modes.append(mode)
        return FakeEvent(EventType.OK)


class FakeMeshCore:
    def __init__(
        self,
        self_info: dict[str, Any] | None = None,
    ) -> None:
        self.commands = FakeCommands()
        self.callbacks: dict[EventType, Any] = {}
        self.self_info = (
            self_info
            if self_info is not None
            else {"name": "ottobot", "max_channels": 4}
        )
        self.ensure_contacts_calls = 0
        self.fetching = False
        self.decrypt_channel_logs = False

    def subscribe(self, event_type: EventType, callback: Any) -> tuple[EventType, Any]:
        self.callbacks[event_type] = callback
        return (event_type, callback)

    def set_decrypt_channel_logs(self, v: bool) -> None:
        self.decrypt_channel_logs = v

    def unsubscribe(self, subscription: tuple[EventType, Any]) -> None:
        self.callbacks.pop(subscription[0], None)

    async def ensure_contacts(self) -> bool:
        self.ensure_contacts_calls += 1
        return True

    async def start_auto_message_fetching(self) -> None:
        self.fetching = True

    async def stop_auto_message_fetching(self) -> None:
        self.fetching = False

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


@pytest.fixture
def bot() -> Ottobot:
    bot = Ottobot(name="ottobot")

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
    return FakeMeshCore()


@pytest.fixture
async def runner(bot: Ottobot, mc: FakeMeshCore) -> MeshCoreRunner:
    runner = MeshCoreRunner(bot, mc)
    await runner.start()
    return runner


class TestLifecycle:
    async def test_start_subscribes_and_begins_fetching(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        assert EventType.CHANNEL_MSG_RECV in mc.callbacks
        assert mc.fetching
        assert mc.decrypt_channel_logs

    async def test_stop_unsubscribes_and_stops_fetching(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await runner.stop()
        assert mc.callbacks == {}
        assert not mc.fetching

    async def test_start_logs_device_channels_from_radio(
        self,
        bot: Ottobot,
        mc: FakeMeshCore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mc.commands.device_channels = {0: "public", 2: "private"}
        with caplog.at_level("INFO", logger="ottobot.runner"):
            await MeshCoreRunner(bot, mc).start()
        messages = "\n".join(r.message for r in caplog.records)
        # Configured slots are reported; the empty ones (1, 3) are skipped.
        assert "0:public" in messages
        assert "2:private" in messages
        assert "1:" not in messages

    async def test_start_notes_when_no_channels_configured(
        self,
        bot: Ottobot,
        mc: FakeMeshCore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("INFO", logger="ottobot.runner"):
            await MeshCoreRunner(bot, mc).start()
        assert any("no channels configured" in r.message for r in caplog.records)


class TestChannelMessages:
    async def test_channel_command_replies_on_same_channel(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !ping", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "pong")]

    async def test_app_mention_addresses_the_bot(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        # As the MeshCore app sends it: "Sender: @[Bot] !command".
        await mc.deliver_chan("alice: @[ottobot] !ping", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "pong")]

    async def test_sender_name_parsed_from_text_convention(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !whoami", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "alice")]

    async def test_text_without_name_prefix_is_ignored(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        # On a channel the bot only answers when addressed by name.
        await mc.deliver_chan("alice: !ping")
        assert mc.commands.sent_chan_msgs == []

    async def test_received_channel_msg_is_logged_even_when_ignored(
        self,
        runner: MeshCoreRunner,
        mc: FakeMeshCore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("INFO", logger="ottobot.runner"):
            await mc.deliver_chan("alice: just chatting", channel_idx=2)
        assert mc.commands.sent_chan_msgs == []
        # Channel 2 is #ottobot-testing; the log names it rather than the index.
        assert any(
            "#ottobot-testing msg from alice" in r.message
            and "just chatting" in r.message
            for r in caplog.records
        )

    async def test_channel_path_is_passed_through(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !path", channel_idx=2, path_len=255)
        assert mc.commands.sent_chan_msgs == [(2, "direct")]

    async def test_channel_exposes_raw_payload(
        self, runner: MeshCoreRunner, mc: FakeMeshCore
    ) -> None:
        await mc.deliver_chan("alice: ottobot !raw", channel_idx=2)
        assert mc.commands.sent_chan_msgs == [(2, "None 2")]


class TestFetchChannels:
    async def test_returns_only_populated_slots(self, mc: FakeMeshCore) -> None:
        mc.commands.device_channels = {0: "public", 2: "private"}
        channels = await fetch_channels(mc)
        assert [(c["channel_idx"], c["channel_name"]) for c in channels] == [
            (0, "public"),
            (2, "private"),
        ]

    async def test_honours_device_max_channels(self, mc: FakeMeshCore) -> None:
        # max_channels is 4, so a channel at index 5 is never probed.
        mc.commands.device_channels = {0: "public", 5: "unreachable"}
        channels = await fetch_channels(mc)
        assert [c["channel_name"] for c in channels] == ["public"]

    async def test_falls_back_when_device_omits_max_channels(self) -> None:
        mc = FakeMeshCore(self_info={"name": "ottobot"})
        mc.commands.device_channels = {0: "public"}
        channels = await fetch_channels(mc)
        assert [c["channel_name"] for c in channels] == ["public"]


class TestApplySettings:
    async def test_applies_every_field(self, mc: FakeMeshCore) -> None:
        config = BotConfig(
            name="ottobot",
            private_key=b"\x01" * 64,
            channels=(
                ChannelConfig(0, "public"),
                ChannelConfig(1, "private", b"\x02" * 16),
            ),
        )
        await apply_settings(mc, config)
        assert mc.commands.names == ["ottobot"]
        assert mc.commands.private_keys == [b"\x01" * 64]
        assert mc.commands.channels == [
            # A secret-less "public" channel gets MeshCore's fixed public key.
            (0, "public", PUBLIC_CHANNEL_KEY),
            (1, "private", b"\x02" * 16),
        ]
        # The radio preset is hardcoded (ottobot.radio.RADIO), not from config.
        assert mc.commands.radios == [(RADIO.freq, RADIO.bw, RADIO.sf, RADIO.cr)]
        assert mc.commands.path_hash_modes == [1]

    async def test_public_channel_gets_canonical_key_when_secret_omitted(
        self, mc: FakeMeshCore
    ) -> None:
        # "Public" (any case) with no secret must use MeshCore's fixed key,
        # not the sha256(name) the meshcore library would otherwise derive.
        config = BotConfig(channels=(ChannelConfig(0, "Public"),))
        await apply_settings(mc, config)
        assert mc.commands.channels == [(0, "Public", PUBLIC_CHANNEL_KEY)]

    async def test_explicit_public_secret_is_not_overridden(
        self, mc: FakeMeshCore
    ) -> None:
        config = BotConfig(channels=(ChannelConfig(0, "public", b"\x03" * 16),))
        await apply_settings(mc, config)
        assert mc.commands.channels == [(0, "public", b"\x03" * 16)]

    async def test_hashtag_channel_keeps_name_derivation(
        self, mc: FakeMeshCore
    ) -> None:
        # "#hashtag" channels are left to the meshcore library to derive
        # (passed through as None), which matches the rest of the mesh.
        config = BotConfig(channels=(ChannelConfig(0, "#testing"),))
        await apply_settings(mc, config)
        assert mc.commands.channels == [(0, "#testing", None)]

    async def test_skips_absent_config_fields(self, mc: FakeMeshCore) -> None:
        await apply_settings(mc, BotConfig(name="ottobot", channels=()))
        assert mc.commands.names == ["ottobot"]
        assert mc.commands.private_keys == []
        assert mc.commands.channels == []

    async def test_empty_config_still_applies_hardcoded_settings(
        self, mc: FakeMeshCore
    ) -> None:
        await apply_settings(mc, BotConfig(channels=()))
        # The config-driven fields (name, private key) are skipped when unset.
        assert mc.commands.names == []
        assert mc.commands.private_keys == []
        assert mc.commands.channels == []
        # The radio preset and path hash mode are hardcoded and always applied.
        assert mc.commands.radios == [(RADIO.freq, RADIO.bw, RADIO.sf, RADIO.cr)]
        assert mc.commands.path_hash_modes == [1]


def task_bot(*channels: ChannelConfig) -> Ottobot:
    """A bot whose config knows the given channels, for scheduled-task tests."""
    return Ottobot(name="ottobot", config=BotConfig(channels=channels))


class TestScheduledTasks:
    async def test_task_runs_on_start_and_broadcasts_return_value(
        self, mc: FakeMeshCore
    ) -> None:
        news = ChannelConfig(index=0, name="#news")
        bot = task_bot(news)

        @bot.task("greet", interval=timedelta(hours=1), channel=news)
        async def greet(ctx: TaskContext) -> str:
            return "hello mesh"

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await asyncio.sleep(0.05)
        await runner.stop()
        assert mc.commands.sent_chan_msgs == [(0, "hello mesh")]

    async def test_task_reply_is_also_broadcast(self, mc: FakeMeshCore) -> None:
        news = ChannelConfig(index=0, name="#news")
        bot = task_bot(news)

        @bot.task("greet", interval=timedelta(hours=1), channel=news)
        async def greet(ctx: TaskContext) -> None:
            await ctx.reply("first")
            await ctx.reply("second")

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await asyncio.sleep(0.05)
        await runner.stop()
        assert mc.commands.sent_chan_msgs == [(0, "first"), (0, "second")]

    async def test_task_broadcasts_on_its_declared_channel(
        self, mc: FakeMeshCore
    ) -> None:
        alerts = ChannelConfig(index=2, name="#ott-alerts")
        bot = task_bot(ChannelConfig(index=0, name="public"), alerts)

        @bot.task("greet", interval=timedelta(hours=1), channel=alerts)
        async def greet(ctx: TaskContext) -> str:
            return "hi"

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await asyncio.sleep(0.05)
        await runner.stop()
        assert mc.commands.sent_chan_msgs == [(2, "hi")]

    async def test_task_channel_not_joined_drops_output(
        self,
        mc: FakeMeshCore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bot = task_bot(ChannelConfig(index=0, name="public"))
        nowhere = ChannelConfig(index=5, name="#nowhere")

        @bot.task("greet", interval=timedelta(hours=1), channel=nowhere)
        async def greet(ctx: TaskContext) -> str:
            return "hi"

        runner = MeshCoreRunner(bot, mc)
        with caplog.at_level("ERROR", logger="ottobot.runner"):
            await runner.start()
            await asyncio.sleep(0.05)
        await runner.stop()
        assert mc.commands.sent_chan_msgs == []
        assert any("#nowhere is not joined" in r.message for r in caplog.records)

    async def test_task_sees_the_bot_config(self, mc: FakeMeshCore) -> None:
        config = BotConfig(discord_webhook_url="https://example.com/webhook")
        bot = Ottobot(name="ottobot", config=config)
        seen: list[str | None] = []

        @bot.task("watch", interval=timedelta(hours=1), channel=PUBLIC)
        async def watch(ctx: TaskContext) -> None:
            seen.append(ctx.config.discord_webhook_url)

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await asyncio.sleep(0.05)
        await runner.stop()
        assert seen == ["https://example.com/webhook"]

    async def test_raising_task_does_not_stop_other_tasks(
        self,
        mc: FakeMeshCore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        public = ChannelConfig(index=0, name="public")
        bot = task_bot(public)

        @bot.task("boom", interval=timedelta(hours=1), channel=public)
        async def boom(ctx: TaskContext) -> None:
            raise RuntimeError("kaboom")

        @bot.task("ok", interval=timedelta(hours=1), channel=public)
        async def ok(ctx: TaskContext) -> str:
            return "fine"

        runner = MeshCoreRunner(bot, mc)
        with caplog.at_level("ERROR", logger="ottobot.runner"):
            await runner.start()
            await asyncio.sleep(0.05)
        await runner.stop()
        assert mc.commands.sent_chan_msgs == [(0, "fine")]
        assert any("boom" in r.message for r in caplog.records)

    async def test_stop_cancels_scheduled_tasks(
        self, bot: Ottobot, mc: FakeMeshCore
    ) -> None:
        calls = 0

        @bot.task("counter", interval=timedelta(seconds=0), channel=PUBLIC)
        async def counter(ctx: TaskContext) -> None:
            nonlocal calls
            calls += 1

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await asyncio.sleep(0.05)
        await runner.stop()
        seen_after_stop = calls
        await asyncio.sleep(0.05)
        assert calls == seen_after_stop
