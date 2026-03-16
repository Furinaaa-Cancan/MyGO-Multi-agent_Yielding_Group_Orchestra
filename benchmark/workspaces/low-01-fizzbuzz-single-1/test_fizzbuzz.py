import pytest
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

    def test_raises_on_zero_or_negative(self):
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

    def test_raises_start_greater_than_end(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(5, 3)

    def test_raises_start_non_positive(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(0, 5)


class TestFizzbuzzSum:
    def test_sum_to_5(self):
        # 1 + 2 + 4 = 7 (exclude 3, 5)
        assert fizzbuzz_sum(5) == 7

    def test_sum_to_1(self):
        assert fizzbuzz_sum(1) == 1

    def test_sum_to_15(self):
        # 1+2+4+7+8+11+13+14 = 60
        assert fizzbuzz_sum(15) == 60

    def test_raises_on_non_positive(self):
        with pytest.raises(ValueError):
            fizzbuzz_sum(0)
        with pytest.raises(ValueError):
            fizzbuzz_sum(-1)
