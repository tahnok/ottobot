import tomllib
from pathlib import Path

import pytest

from ottobot.channels import CHANNELS
from ottobot.config import BotConfig, load_config, parse_config


def parse(text: str) -> BotConfig:
    return parse_config(tomllib.loads(text))


def test_full_config_round_trips() -> None:
    config = parse("""
        name = "ottobot"
        private_key = "%s"

        [radio]
        freq = 910.525
        bw = 250.0
        sf = 11
        cr = 5
        """ % ("ab" * 64))
    assert config.name == "ottobot"
    assert config.private_key == bytes.fromhex("ab" * 64)
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
    # Channels aren't config; they default to the shared code-defined set.
    assert config.channels == CHANNELS
    assert config.radio is None


def test_name_only() -> None:
    config = parse('name = "bot"')
    assert config.name == "bot"
    assert config.channels == CHANNELS
    assert config.radio is None


def test_database_parses_to_a_path() -> None:
    config = parse('database = "/data/ottobot.db"')
    assert config.database == Path("/data/ottobot.db")


def test_database_defaults_to_none() -> None:
    assert parse("").database is None


def test_bad_private_key_hex() -> None:
    with pytest.raises(ValueError, match="private_key is not valid hex"):
        parse('private_key = "nothex"')


def test_private_key_wrong_length() -> None:
    with pytest.raises(ValueError, match="private_key must be 64 bytes"):
        parse('private_key = "abcd"')


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


def test_load_config_reads_file(tmp_path) -> None:
    path = tmp_path / "ottobot.toml"
    path.write_text('name = "fromfile"\n')
    assert load_config(path).name == "fromfile"
