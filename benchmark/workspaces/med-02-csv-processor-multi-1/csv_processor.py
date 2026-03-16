"""CSV Data Processor module."""

import csv
import os


def read_csv(filepath: str) -> list[dict[str, str]]:
    """Read a CSV file and return a list of dictionaries using the first row as headers.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    with open(filepath, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def filter_rows(data: list[dict], column: str, value: str) -> list[dict]:
    """Filter rows where the specified column equals the given value.

    Raises:
        KeyError: If the column is not present in any row.
    """
    if data and not any(column in row for row in data):
        raise KeyError(f"Column not found: {column}")
    return [row for row in data if row.get(column) == value]


def sort_rows(data: list[dict], column: str, reverse: bool = False) -> list[dict]:
    """Sort rows by column value using string comparison.

    Raises:
        KeyError: If the column is missing from any row.
    """
    for row in data:
        if column not in row:
            raise KeyError(f"Column not found: {column}")
    return sorted(data, key=lambda row: row[column], reverse=reverse)


def add_column(data: list[dict], column: str, default: str = "") -> list[dict]:
    """Add a new column with a default value to all rows.

    Raises:
        ValueError: If the column already exists in any row.
    """
    for row in data:
        if column in row:
            raise ValueError(f"Column already exists: {column}")
    result = []
    for row in data:
        new_row = dict(row)
        new_row[column] = default
        result.append(new_row)
    return result


def write_csv(filepath: str, data: list[dict]) -> None:
    """Write a list of dictionaries to a CSV file.

    Raises:
        ValueError: If data is empty.
    """
    if not data:
        raise ValueError("Data is empty")
    headers = list(data[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)


def aggregate(data: list[dict], column: str, func: str) -> str:
    """Aggregate a column using the specified function.

    Supported functions: "count", "sum", "avg", "min", "max".

    Raises:
        ValueError: For an unknown function.
        KeyError: If the column is missing.
    """
    supported = {"count", "sum", "avg", "min", "max"}
    if func not in supported:
        raise ValueError(f"Unknown function: {func}")
    if not data:
        raise KeyError(f"Column not found: {column}")
    for row in data:
        if column not in row:
            raise KeyError(f"Column not found: {column}")

    values = [row[column] for row in data]

    if func == "count":
        return str(len(values))
    elif func == "sum":
        total = sum(float(v) for v in values)
        return str(total)
    elif func == "avg":
        total = sum(float(v) for v in values)
        return str(total / len(values))
    elif func == "min":
        return str(min(values))
    elif func == "max":
        return str(max(values))
