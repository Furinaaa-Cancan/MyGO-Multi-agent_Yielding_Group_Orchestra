# Task: FizzBuzz Extended

Implement a Python module `fizzbuzz.py` with the following functions:

## Requirements

1. `fizzbuzz(n: int) -> str`
   - Returns "Fizz" if n is divisible by 3
   - Returns "Buzz" if n is divisible by 5
   - Returns "FizzBuzz" if n is divisible by both 3 and 5
   - Returns str(n) otherwise
   - Raises ValueError if n <= 0

2. `fizzbuzz_range(start: int, end: int) -> list[str]`
   - Returns a list of fizzbuzz results for range [start, end] inclusive
   - Raises ValueError if start > end or start <= 0

3. `fizzbuzz_sum(n: int) -> int`
   - Returns the sum of all numbers from 1 to n that are NOT divisible by 3 or 5
   - Raises ValueError if n <= 0

Also create `test_fizzbuzz.py` with tests for all functions.
