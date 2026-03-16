def reverse_words(s: str) -> str:
    """Reverse the order of words in a string."""
    if not s:
        return ""
    words = s.split()
    return " ".join(reversed(words))


def is_palindrome(s: str) -> bool:
    """Case-insensitive palindrome check, ignoring non-alphanumeric characters."""
    filtered = [c.lower() for c in s if c.isalnum()]
    return filtered == filtered[::-1]


def truncate(s: str, max_len: int, suffix: str = "...") -> str:
    """Truncate string to max_len characters including the suffix."""
    if max_len < len(suffix):
        raise ValueError(
            f"max_len ({max_len}) must be >= len(suffix) ({len(suffix)})"
        )
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def snake_to_camel(s: str) -> str:
    """Convert snake_case to CamelCase."""
    if not s:
        return ""
    return "".join(word.capitalize() for word in s.split("_"))


def count_vowels(s: str) -> int:
    """Count vowels (aeiouAEIOU) in a string."""
    return sum(1 for c in s if c in "aeiouAEIOU")
