"""Gold-standard tests for string_utils module."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from string_utils import reverse_words, is_palindrome, truncate, snake_to_camel, count_vowels


# ── reverse_words ──────────────────────────────────────────────────────────

class TestReverseWords:
    def test_two_words(self):
        assert reverse_words("hello world") == "world hello"

    def test_three_words(self):
        assert reverse_words("one two three") == "three two one"

    def test_single_word(self):
        assert reverse_words("hello") == "hello"

    def test_empty_string(self):
        assert reverse_words("") == ""

    def test_multiple_spaces_between_words(self):
        # split() collapses whitespace; result uses single spaces
        result = reverse_words("hello   world")
        assert result == "world hello"

    def test_leading_trailing_spaces(self):
        result = reverse_words("  hello world  ")
        assert result == "world hello"


# ── is_palindrome ─────────────────────────────────────────────────────────

class TestIsPalindrome:
    def test_simple_palindrome(self):
        assert is_palindrome("racecar") is True

    def test_mixed_case_palindrome(self):
        assert is_palindrome("RaceCar") is True

    def test_with_punctuation(self):
        assert is_palindrome("A man, a plan, a canal: Panama") is True

    def test_not_palindrome(self):
        assert is_palindrome("hello") is False

    def test_empty_string(self):
        assert is_palindrome("") is True

    def test_single_character(self):
        assert is_palindrome("a") is True

    def test_spaces_only(self):
        assert is_palindrome("   ") is True

    def test_numeric_palindrome(self):
        assert is_palindrome("12321") is True

    def test_numeric_not_palindrome(self):
        assert is_palindrome("12345") is False


# ── truncate ──────────────────────────────────────────────────────────────

class TestTruncate:
    def test_truncates_long_string(self):
        assert truncate("hello world", 8) == "hello..."

    def test_string_fits(self):
        assert truncate("hi", 10) == "hi"

    def test_exact_fit(self):
        assert truncate("hello", 5) == "hello"

    def test_custom_suffix(self):
        assert truncate("hello world", 8, suffix="--") == "hello --"

    def test_raises_when_max_len_less_than_suffix(self):
        with pytest.raises(ValueError):
            truncate("hello", 2, suffix="...")

    def test_max_len_equals_suffix_length(self):
        assert truncate("hello world", 3, suffix="...") == "..."

    def test_empty_suffix(self):
        assert truncate("hello world", 5, suffix="") == "hello"


# ── snake_to_camel ────────────────────────────────────────────────────────

class TestSnakeToCamel:
    def test_basic_conversion(self):
        assert snake_to_camel("hello_world") == "HelloWorld"

    def test_single_word(self):
        assert snake_to_camel("hello") == "Hello"

    def test_empty_string(self):
        assert snake_to_camel("") == ""

    def test_three_words(self):
        assert snake_to_camel("one_two_three") == "OneTwoThree"

    def test_already_capitalised_segments(self):
        assert snake_to_camel("HELLO_WORLD") == "HelloWorld"

    def test_leading_underscore(self):
        # Leading underscore produces an empty first segment
        result = snake_to_camel("_private_var")
        assert result == "PrivateVar"

    def test_multiple_underscores(self):
        result = snake_to_camel("a__b")
        assert result == "AB"


# ── count_vowels ──────────────────────────────────────────────────────────

class TestCountVowels:
    def test_normal_string(self):
        assert count_vowels("hello") == 2

    def test_all_vowels(self):
        assert count_vowels("aeiou") == 5

    def test_no_vowels(self):
        assert count_vowels("bcdfg") == 0

    def test_empty_string(self):
        assert count_vowels("") == 0

    def test_mixed_case(self):
        assert count_vowels("AeIoU") == 5

    def test_with_spaces_and_punctuation(self):
        assert count_vowels("hello, world!") == 3
