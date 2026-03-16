import pytest
from string_utils import reverse_words, is_palindrome, truncate, snake_to_camel, count_vowels


# ---------- reverse_words ----------

class TestReverseWords:
    def test_basic(self):
        assert reverse_words("hello world") == "world hello"

    def test_single_word(self):
        assert reverse_words("hello") == "hello"

    def test_empty_string(self):
        assert reverse_words("") == ""

    def test_multiple_words(self):
        assert reverse_words("a b c d") == "d c b a"

    def test_leading_trailing_spaces(self):
        # split() handles extra whitespace; result has single spaces
        assert reverse_words("  hello  world  ") == "world hello"

    def test_single_character(self):
        assert reverse_words("x") == "x"

    def test_only_spaces(self):
        assert reverse_words("   ") == ""

    def test_tabs_and_newlines(self):
        # split() splits on all whitespace; result uses single spaces
        assert reverse_words("hello\tworld\nfoo") == "foo world hello"


# ---------- is_palindrome ----------

class TestIsPalindrome:
    def test_basic_true(self):
        assert is_palindrome("racecar") is True

    def test_basic_false(self):
        assert is_palindrome("hello") is False

    def test_case_insensitive(self):
        assert is_palindrome("RaceCar") is True

    def test_with_punctuation(self):
        assert is_palindrome("A man, a plan, a canal: Panama") is True

    def test_empty_string(self):
        assert is_palindrome("") is True

    def test_single_char(self):
        assert is_palindrome("a") is True

    def test_numbers(self):
        assert is_palindrome("12321") is True
        assert is_palindrome("12345") is False

    def test_spaces_only(self):
        assert is_palindrome("   ") is True

    def test_mixed_alphanumeric(self):
        assert is_palindrome("Was it a car or a cat I saw?") is True


# ---------- truncate ----------

class TestTruncate:
    def test_no_truncation_needed(self):
        assert truncate("hi", 10) == "hi"

    def test_exact_length(self):
        assert truncate("hello", 5) == "hello"

    def test_truncation_with_default_suffix(self):
        assert truncate("hello world", 8) == "hello..."

    def test_truncation_with_custom_suffix(self):
        assert truncate("hello world", 7, "..") == "hello.."

    def test_max_len_equals_suffix_len(self):
        assert truncate("hello world", 3) == "..."

    def test_value_error_when_max_len_lt_suffix(self):
        with pytest.raises(ValueError):
            truncate("hello", 2, "...")

    def test_empty_suffix(self):
        assert truncate("hello world", 5, "") == "hello"

    def test_empty_string(self):
        assert truncate("", 5) == ""

    def test_suffix_longer_than_default(self):
        assert truncate("abcdefghij", 8, "[...]") == "abc[...]"

    def test_negative_max_len(self):
        with pytest.raises(ValueError):
            truncate("hello", -1, "")

    def test_zero_max_len_empty_suffix(self):
        assert truncate("hello", 0, "") == ""

    def test_one_char_over(self):
        assert truncate("abcdef", 5) == "ab..."

    def test_max_len_zero_with_default_suffix_raises(self):
        with pytest.raises(ValueError):
            truncate("hello", 0)


# ---------- snake_to_camel ----------

class TestSnakeToCamel:
    def test_basic(self):
        assert snake_to_camel("hello_world") == "HelloWorld"

    def test_single_word(self):
        assert snake_to_camel("hello") == "Hello"

    def test_empty_string(self):
        assert snake_to_camel("") == ""

    def test_multiple_underscores(self):
        assert snake_to_camel("one_two_three_four") == "OneTwoThreeFour"

    def test_already_capitalized(self):
        assert snake_to_camel("Hello_World") == "HelloWorld"

    def test_single_characters(self):
        assert snake_to_camel("a_b_c") == "ABC"

    def test_leading_underscore(self):
        # Leading underscore produces an empty first segment
        assert snake_to_camel("_hello") == "Hello"

    def test_trailing_underscore(self):
        assert snake_to_camel("hello_") == "Hello"

    def test_double_underscore(self):
        # Double underscores produce empty segments
        assert snake_to_camel("hello__world") == "HelloWorld"

    def test_only_underscores(self):
        assert snake_to_camel("_") == ""
        assert snake_to_camel("__") == ""

    def test_all_uppercase_input(self):
        # capitalize() lowercases all but first char
        assert snake_to_camel("HELLO_WORLD") == "HelloWorld"


# ---------- count_vowels ----------

class TestCountVowels:
    def test_basic(self):
        assert count_vowels("hello") == 2

    def test_all_vowels(self):
        assert count_vowels("aeiou") == 5

    def test_no_vowels(self):
        assert count_vowels("xyz") == 0

    def test_empty_string(self):
        assert count_vowels("") == 0

    def test_uppercase_vowels(self):
        assert count_vowels("AEIOU") == 5

    def test_mixed_case(self):
        assert count_vowels("HeLLo WoRLd") == 3

    def test_numbers_and_special(self):
        assert count_vowels("h3ll0 w0rld!") == 0

    def test_y_is_not_a_vowel(self):
        assert count_vowels("yY") == 0
