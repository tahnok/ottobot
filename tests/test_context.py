from ottawa_meshbot.context import IncomingMessage


class TestHopCount:
    def test_unknown_when_path_len_missing(self) -> None:
        assert IncomingMessage(text="hi").hop_count is None

    def test_direct_message_is_zero_hops(self) -> None:
        assert IncomingMessage(text="hi", path_len=255).hop_count == 0

    def test_routed_message_reports_hops(self) -> None:
        assert IncomingMessage(text="hi", path_len=3).hop_count == 3


class TestPathDescription:
    def test_unknown_path(self) -> None:
        assert IncomingMessage(text="hi").path_description == "unknown path"

    def test_direct(self) -> None:
        assert IncomingMessage(text="hi", path_len=255).path_description == "direct"

    def test_hops_without_path(self) -> None:
        assert IncomingMessage(text="hi", path_len=1).path_description == "1 hop"
        assert IncomingMessage(text="hi", path_len=2).path_description == "2 hops"

    def test_hops_with_path(self) -> None:
        message = IncomingMessage(text="hi", path_len=2, path="a1b2")
        assert message.path_description == "2 hops via a1,b2"

    def test_hash_mode_sets_bytes_per_hop(self) -> None:
        message = IncomingMessage(
            text="hi", path_len=2, path="a1b2c3d4e5f6", path_hash_mode=2
        )
        assert message.path_hash_size == 3
        assert message.path_description == "2 hops via a1b2c3,d4e5f6"

    def test_hash_mode_defaults_to_single_byte(self) -> None:
        assert IncomingMessage(text="hi").path_hash_size == 1
        direct = IncomingMessage(text="hi", path_len=255, path_hash_mode=-1)
        assert direct.path_hash_size == 1
        assert direct.path_description == "direct"
