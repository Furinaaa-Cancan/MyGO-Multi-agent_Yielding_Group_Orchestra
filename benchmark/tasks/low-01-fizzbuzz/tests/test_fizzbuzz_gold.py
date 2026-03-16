"""Gold-standard tests for FizzBuzz Extended task."""

import sys
from pathlib import Path

import pytest

# Add workspace to path so we can import fizzbuzz
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fizzbuzz import fizzbuzz, fizzbuzz_range, fizzbuzz_sum


class TestFizzbuzz:
    def test_fizz(self):
        assert fizzbuzz(3) == "Fizz"
        assert fizzbuzz(9) == "Fizz"

    def test_buzz(self):
        assert fizzbuzz(5) == "Buzz"
        assert fizzbuzz(10) == "Buzz"

    def test_fizzbuzz(self):
        assert fizzbuzz(15) == "FizzBuzz"
        assert fizzbuzz(30) == "FizzBuzz"

    def test_number(self):
        assert fizzbuzz(1) == "1"
        assert fizzbuzz(7) == "7"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            fizzbuzz(0)
        with pytest.raises(ValueError):
            fizzbuzz(-1)


class TestFizzbuzzRange:
    def test_basic_range(self):
        result = fizzbuzz_range(1, 5)
        assert result == ["1", "2", "Fizz", "4", "Buzz"]

    def test_single_element(self):
        assert fizzbuzz_range(3, 3) == ["Fizz"]

    def test_includes_fizzbuzz(self):
        result = fizzbuzz_range(14, 16)
        assert result == ["14", "FizzBuzz", "16"]

    def test_invalid_range(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(5, 3)

    def test_invalid_start(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(0, 5)


class TestFizzbuzzSum:
    def test_basic(self):
        # 1+2+4+7+8+11+13+14 = 60 for n=15 (exclude 3,5,6,9,10,12,15)
        # Actually: 1,2,4,7,8,11,13,14 = 60
        assert fizzbuzz_sum(15) == 1 + 2 + 4 + 7 + 8 + 11 + 13 + 14

    def test_small(self):
        assert fizzbuzz_sum(1) == 1
        assert fizzbuzz_sum(2) == 1 + 2

    def test_three(self):
        # 1+2 (3 excluded)
        assert fizzbuzz_sum(3) == 3

    def test_invalid(self):
        with pytest.raises(ValueError):
            fizzbuzz_sum(0)
