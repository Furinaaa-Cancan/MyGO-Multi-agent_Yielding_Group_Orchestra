def fizzbuzz(n: int) -> str:
    """Return FizzBuzz result for a single number.

    Returns "Fizz" if n is divisible by 3, "Buzz" if divisible by 5,
    "FizzBuzz" if divisible by both, or str(n) otherwise.

    Raises ValueError if n <= 0.
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError("n must be an integer")
    if n <= 0:
        raise ValueError("n must be a positive integer")
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)


def fizzbuzz_range(start: int, end: int) -> list[str]:
    """Return a list of fizzbuzz results for range [start, end] inclusive.

    Raises ValueError if start > end or start <= 0.
    """
    if not isinstance(start, int) or isinstance(start, bool):
        raise TypeError("start must be an integer")
    if not isinstance(end, int) or isinstance(end, bool):
        raise TypeError("end must be an integer")
    if start <= 0:
        raise ValueError("start must be a positive integer")
    if start > end:
        raise ValueError("start must not be greater than end")
    return [fizzbuzz(n) for n in range(start, end + 1)]


def fizzbuzz_sum(n: int) -> int:
    """Return the sum of all numbers from 1 to n that are NOT divisible by 3 or 5.

    Raises ValueError if n <= 0.
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError("n must be an integer")
    if n <= 0:
        raise ValueError("n must be a positive integer")
    return sum(i for i in range(1, n + 1) if i % 3 != 0 and i % 5 != 0)
