import tomllib

import pytest

from ottobot.config import BotConfig, load_config, parse_config


def parse(text: str) -> BotConfig:
    return parse_config(tomllib.loads(text))


def test_full_config_round_trips() -> None:
    config = parse("""
        name = "ottobot"
        private_key = "%s"

        [[channels]]
        index = 0
        name = "public"

        [[channels]]
        index = 1
        name = "private"
        secret = "%s"

        [radio]
        freq = 910.525
        bw = 250.0
        sf = 11
        cr = 5
        """ % ("ab" * 64, "cd" * 16))
    assert config.name == "ottobot"
    assert config.private_key == bytes.fromhex("ab" * 64)
    assert [(c.index, c.name, c.secret) for c in config.channels] == [
        (0, "public", None),
        (1, "private", bytes.fromhex("cd" * 16)),
    ]
    assert config.radio is not None
    assert (config.radio.freq, config.radio.bw, config.radio.sf, config.radio.cr) == (
        910.525,
        250.0,
        11,
        5,
    )


def test_empty_config_defaults_to_none() -> None:
    config = parse("")
    assert config == BotConfig()
    assert config.name is None
    assert config.private_key is None
    assert config.channels == ()
    assert config.radio is None


def test_name_only() -> None:
    config = parse('name = "bot"')
    assert config.name == "bot"
    assert config.channels == ()
    assert config.radio is None


def test_bad_private_key_hex() -> None:
    with pytest.raises(ValueError, match="private_key is not valid hex"):
        parse('private_key = "nothex"')


def test_private_key_wrong_length() -> None:
    with pytest.raises(ValueError, match="private_key must be 64 bytes"):
        parse('private_key = "abcd"')


def test_channel_secret_wrong_length() -> None:
    with pytest.raises(ValueError, match="channel secret must be 16 bytes"):
        parse("""
            [[channels]]
            index = 0
            name = "public"
            secret = "abcd"
            """)


def test_channel_requires_index_and_name() -> None:
    with pytest.raises(ValueError, match="index and a name"):
        parse("""
            [[channels]]
            name = "public"
            """)


def test_radio_missing_keys() -> None:
    with pytest.raises(ValueError, match="missing required keys"):
        parse("""
            [radio]
            freq = 910.525
            """)


def test_log_level_defaults_to_none() -> None:
    assert parse('name = "bot"').log_level is None


def test_log_level_is_parsed_and_uppercased() -> None:
    assert parse('log_level = "debug"').log_level == "DEBUG"


def test_bad_log_level_raises() -> None:
    with pytest.raises(ValueError, match="log_level 'loud' is not a known level"):
        parse('log_level = "loud"')


def test_discord_webhook_url_is_parsed() -> None:
    config = parse("""
        [discord]
        webhook_url = "https://discord.com/api/webhooks/1/abc"
        """)
    assert config.discord_webhook_url == "https://discord.com/api/webhooks/1/abc"


def test_discord_webhook_url_defaults_to_none() -> None:
    assert parse('name = "bot"').discord_webhook_url is None


def test_public_channel_idx_defaults_to_zero() -> None:
    assert BotConfig().public_channel_idx() == 0


def test_public_channel_idx_finds_named_channel() -> None:
    config = parse("""
        [[channels]]
        index = 2
        name = "public"
        """)
    assert config.public_channel_idx() == 2


def test_public_channel_idx_is_case_insensitive() -> None:
    config = parse("""
        [[channels]]
        index = 3
        name = "Public"
        """)
    assert config.public_channel_idx() == 3


def test_load_config_reads_file(tmp_path) -> None:
    path = tmp_path / "ottobot.toml"
    path.write_text('name = "fromfile"\n')
    assert load_config(path).name == "fromfile"
