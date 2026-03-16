# Task: CSV Data Processor

Implement `csv_processor.py` with the following functions:

1. `read_csv(filepath: str) -> list[dict[str, str]]` - Read a CSV file and return a list of dictionaries using the first row as headers. Raises `FileNotFoundError` if the file does not exist.

2. `filter_rows(data: list[dict], column: str, value: str) -> list[dict]` - Filter rows where the specified column equals the given value. Raises `KeyError` if the column is not present in any row.

3. `sort_rows(data: list[dict], column: str, reverse: bool = False) -> list[dict]` - Sort rows by column value using string comparison. Raises `KeyError` if the column is missing from any row.

4. `add_column(data: list[dict], column: str, default: str = "") -> list[dict]` - Add a new column with a default value to all rows. Raises `ValueError` if the column already exists in any row.

5. `write_csv(filepath: str, data: list[dict]) -> None` - Write a list of dictionaries to a CSV file. The first row should be the headers derived from the first dictionary's keys. Raises `ValueError` if data is empty.

6. `aggregate(data: list[dict], column: str, func: str) -> str` - Aggregate a column using the specified function. Supported functions: `"count"`, `"sum"`, `"avg"`, `"min"`, `"max"`. For `"sum"` and `"avg"`, values must be numeric strings. Raises `ValueError` for an unknown function. Raises `KeyError` if the column is missing.

Also create `test_csv_processor.py` with basic tests.
