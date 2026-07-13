import pytest

from ottobot import MAX_MESSAGE_LEN, chunk_message


class TestChunkMessage:
    def test_short_text_is_a_single_chunk(self) -> None:
        assert chunk_message("hello") == ["hello"]

    def test_empty_and_whitespace_yield_no_chunks(self) -> None:
        assert chunk_message("") == []
        assert chunk_message("   \n  ") == []

    def test_every_chunk_is_within_the_limit(self) -> None:
        text = "\n".join(f"!cmd{i} - does thing number {i}" for i in range(40))
        chunks = chunk_message(text)
        assert len(chunks) > 1
        assert all(len(chunk) <= MAX_MESSAGE_LEN for chunk in chunks)

    def test_whole_lines_are_kept_together(self) -> None:
        # Two short lines that fit together stay in one chunk...
        assert chunk_message("!ping - alive\n!echo - repeat", limit=40) == [
            "!ping - alive\n!echo - repeat"
        ]

    def test_breaks_between_lines_when_over_limit(self) -> None:
        chunks = chunk_message("aaaa\nbbbb\ncccc", limit=9)
        # Two lines (4 + newline + 4 = 9) fit; the third starts a new chunk.
        assert chunks == ["aaaa\nbbbb", "cccc"]

    def test_no_content_is_lost(self) -> None:
        lines = [f"line {i} with some words" for i in range(30)]
        text = "\n".join(lines)
        rejoined = "\n".join(chunk_message(text, limit=30))
        assert rejoined == text

    def test_long_line_is_split_on_words(self) -> None:
        chunks = chunk_message("one two three four five", limit=9)
        assert all(len(chunk) <= 9 for chunk in chunks)
        assert " ".join(chunks) == "one two three four five"

    def test_word_longer_than_limit_is_hard_split(self) -> None:
        chunks = chunk_message("abcdefghij", limit=4)
        assert chunks == ["abcd", "efgh", "ij"]

    def test_zero_limit_rejected(self) -> None:
        with pytest.raises(ValueError):
            chunk_message("hello", limit=0)
