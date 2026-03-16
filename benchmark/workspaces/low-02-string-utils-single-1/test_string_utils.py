import pytest
from string_utils import reverse_words, is_palindrome, truncate, snake_to_camel, count_vowels


# --- reverse_words ---

def test_reverse_words_basic():
    assert reverse_words("hello world") == "world hello"

def test_reverse_words_single_word():
    assert reverse_words("hello") == "hello"

def test_reverse_words_empty():
    assert reverse_words("") == ""

def test_reverse_words_multiple():
    assert reverse_words("a b c d") == "d c b a"


# --- is_palindrome ---

def test_is_palindrome_basic():
    assert is_palindrome("racecar") is True

def test_is_palindrome_mixed_case():
    assert is_palindrome("RaceCar") is True

def test_is_palindrome_with_punctuation():
    assert is_palindrome("A man, a plan, a canal: Panama") is True

def test_is_palindrome_false():
    assert is_palindrome("hello") is False

def test_is_palindrome_empty():
    assert is_palindrome("") is True


# --- truncate ---

def test_truncate_no_truncation_needed():
    assert truncate("hi", 10) == "hi"

def test_truncate_exact_fit():
    assert truncate("hello", 5) == "hello"

def test_truncate_basic():
    assert truncate("hello world", 8) == "hello..."

def test_truncate_custom_suffix():
    assert truncate("hello world", 7, suffix="--") == "hello--"

def test_truncate_raises_on_short_max_len():
    with pytest.raises(ValueError):
        truncate("hello", 2, suffix="...")


# --- snake_to_camel ---

def test_snake_to_camel_basic():
    assert snake_to_camel("hello_world") == "HelloWorld"

def test_snake_to_camel_single():
    assert snake_to_camel("hello") == "Hello"

def test_snake_to_camel_empty():
    assert snake_to_camel("") == ""

def test_snake_to_camel_multiple():
    assert snake_to_camel("one_two_three_four") == "OneTwoThreeFour"


# --- count_vowels ---

def test_count_vowels_basic():
    assert count_vowels("hello") == 2

def test_count_vowels_upper():
    assert count_vowels("AEIOU") == 5

def test_count_vowels_empty():
    assert count_vowels("") == 0

def test_count_vowels_no_vowels():
    assert count_vowels("bcdfg") == 0
