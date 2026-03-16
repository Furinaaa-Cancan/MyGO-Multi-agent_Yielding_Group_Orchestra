import pytest
from fizzbuzz import fizzbuzz, fizzbuzz_range, fizzbuzz_sum


class TestFizzbuzz:
    def test_returns_number_as_string(self):
        assert fizzbuzz(1) == "1"
        assert fizzbuzz(2) == "2"
        assert fizzbuzz(4) == "4"

    def test_fizz_divisible_by_3(self):
        assert fizzbuzz(3) == "Fizz"
        assert fizzbuzz(6) == "Fizz"
        assert fizzbuzz(9) == "Fizz"

    def test_buzz_divisible_by_5(self):
        assert fizzbuzz(5) == "Buzz"
        assert fizzbuzz(10) == "Buzz"
        assert fizzbuzz(20) == "Buzz"

    def test_fizzbuzz_divisible_by_15(self):
        assert fizzbuzz(15) == "FizzBuzz"
        assert fizzbuzz(30) == "FizzBuzz"
        assert fizzbuzz(45) == "FizzBuzz"

    def test_raises_for_zero(self):
        with pytest.raises(ValueError):
            fizzbuzz(0)

    def test_raises_for_negative(self):
        with pytest.raises(ValueError):
            fizzbuzz(-1)

    def test_raises_for_non_integer(self):
        with pytest.raises(TypeError):
            fizzbuzz(3.5)
        with pytest.raises(TypeError):
            fizzbuzz("3")
        with pytest.raises(TypeError):
            fizzbuzz(None)
        with pytest.raises(TypeError):
            fizzbuzz(True)


class TestFizzbuzzRange:
    def test_single_element_range(self):
        assert fizzbuzz_range(1, 1) == ["1"]

    def test_range_1_to_5(self):
        assert fizzbuzz_range(1, 5) == ["1", "2", "Fizz", "4", "Buzz"]

    def test_range_14_to_16(self):
        assert fizzbuzz_range(14, 16) == ["14", "FizzBuzz", "16"]

    def test_raises_start_greater_than_end(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(5, 3)

    def test_raises_start_zero(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(0, 5)

    def test_raises_start_negative(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(-1, 5)

    def test_single_element_fizzbuzz_boundary(self):
        assert fizzbuzz_range(15, 15) == ["FizzBuzz"]

    def test_raises_positive_start_negative_end(self):
        with pytest.raises(ValueError):
            fizzbuzz_range(1, -1)

    def test_raises_for_non_integer(self):
        with pytest.raises(TypeError):
            fizzbuzz_range(1.5, 5)
        with pytest.raises(TypeError):
            fizzbuzz_range(1, 5.5)


class TestFizzbuzzSum:
    def test_sum_1(self):
        assert fizzbuzz_sum(1) == 1

    def test_sum_5(self):
        # 1 + 2 + 4 = 7  (3 and 5 excluded)
        assert fizzbuzz_sum(5) == 7

    def test_sum_15(self):
        # Exclude: 3,5,6,9,10,12,15 -> sum of excluded = 60
        # Total 1..15 = 120, so result = 120 - 60 = 60
        assert fizzbuzz_sum(15) == 60

    def test_sum_10(self):
        # 1 + 2 + 4 + 7 + 8 = 22  (exclude 3,5,6,9,10)
        assert fizzbuzz_sum(10) == 22

    def test_raises_for_zero(self):
        with pytest.raises(ValueError):
            fizzbuzz_sum(0)

    def test_raises_for_negative(self):
        with pytest.raises(ValueError):
            fizzbuzz_sum(-1)

    def test_raises_for_non_integer(self):
        with pytest.raises(TypeError):
            fizzbuzz_sum(3.0)
        with pytest.raises(TypeError):
            fizzbuzz_sum("5")
