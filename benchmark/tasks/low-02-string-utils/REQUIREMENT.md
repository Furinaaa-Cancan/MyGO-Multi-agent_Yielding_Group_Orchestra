# Task: String Utility Functions

Implement `string_utils.py` with the following pure functions:

1. `reverse_words(s: str) -> str` - Reverse the order of words (not characters). `"hello world"` → `"world hello"`. Preserves single spaces between words. Empty string → `""`.

2. `is_palindrome(s: str) -> bool` - Case-insensitive palindrome check, ignoring non-alphanumeric characters. `"A man, a plan, a canal: Panama"` → `True`. Empty string → `True`.

3. `truncate(s: str, max_len: int, suffix: str = "...") -> str` - Truncate string to `max_len` characters including the suffix. If the string already fits within `max_len`, return it unchanged. Raises `ValueError` if `max_len < len(suffix)`.

4. `snake_to_camel(s: str) -> str` - Convert snake_case to CamelCase. `"hello_world"` → `"HelloWorld"`. Empty string → `""`.

5. `count_vowels(s: str) -> int` - Count vowels (aeiouAEIOU). Returns `0` for empty string.

Also create `test_string_utils.py` with tests for your implementation.
