def fizzbuzz(n: int) -> str:
    if n <= 0:
        raise ValueError("n must be positive")
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)


def fizzbuzz_range(start: int, end: int) -> list[str]:
    if start <= 0:
        raise ValueError("start must be positive")
    if start > end:
        raise ValueError("start must not be greater than end")
    return [fizzbuzz(i) for i in range(start, end + 1)]


def fizzbuzz_sum(n: int) -> int:
    if n <= 0:
        raise ValueError("n must be positive")
    return sum(i for i in range(1, n + 1) if i % 3 != 0 and i % 5 != 0)
