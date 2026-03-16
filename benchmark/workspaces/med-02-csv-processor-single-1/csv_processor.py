"""CSV Data Processor module."""

import csv


def read_csv(filepath: str) -> list[dict[str, str]]:
    """Read a CSV file and return a list of dictionaries using the first row as headers."""
    try:
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            return [dict(row) for row in reader]
    except FileNotFoundError:
        raise


def filter_rows(data: list[dict], column: str, value: str) -> list[dict]:
    """Filter rows where the specified column equals the given value."""
    if not data:
        raise KeyError(column)
    if column not in data[0]:
        raise KeyError(column)
    return [row for row in data if row[column] == value]


def sort_rows(data: list[dict], column: str, reverse: bool = False) -> list[dict]:
    """Sort rows by column value using string comparison."""
    if data and column not in data[0]:
        raise KeyError(column)
    return sorted(data, key=lambda row: row[column], reverse=reverse)


def add_column(data: list[dict], column: str, default: str = "") -> list[dict]:
    """Add a new column with a default value to all rows."""
    if data and column in data[0]:
        raise ValueError(f"Column '{column}' already exists")
    return [{**row, column: default} for row in data]


def write_csv(filepath: str, data: list[dict]) -> None:
    """Write a list of dictionaries to a CSV file."""
    if not data:
        raise ValueError("Data is empty")
    fieldnames = list(data[0].keys())
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def _try_numeric(values: list[str]):
    """Try to convert all values to float. Return list of floats or None."""
    try:
        return [float(v) for v in values]
    except (ValueError, TypeError):
        return None


def aggregate(data: list[dict], column: str, func: str) -> str:
    """Aggregate a column using the specified function."""
    supported = {"count", "sum", "avg", "min", "max"}
    if func not in supported:
        raise ValueError(f"Unknown function: {func}")
    if not data or column not in data[0]:
        raise KeyError(column)
    values = [row[column] for row in data]
    if func == "count":
        return str(len(values))
    if func == "sum":
        total = sum(float(v) for v in values)
        if total == int(total):
            return str(int(total))
        return str(total)
    if func == "avg":
        total = sum(float(v) for v in values)
        return str(total / len(values))
    if func == "min":
        numeric = _try_numeric(values)
        if numeric is not None:
            m = min(numeric)
            return str(int(m)) if m == int(m) else str(m)
        return min(values)
    if func == "max":
        numeric = _try_numeric(values)
        if numeric is not None:
            m = max(numeric)
            return str(int(m)) if m == int(m) else str(m)
        return max(values)
