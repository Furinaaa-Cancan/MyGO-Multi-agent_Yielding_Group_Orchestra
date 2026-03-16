"""Tests for csv_processor module."""

import os
import pytest
import tempfile

from csv_processor import read_csv, filter_rows, sort_rows, add_column, write_csv, aggregate


@pytest.fixture
def sample_data():
    return [
        {"name": "Alice", "age": "30", "city": "NYC"},
        {"name": "Bob", "age": "25", "city": "LA"},
        {"name": "Charlie", "age": "35", "city": "NYC"},
    ]


@pytest.fixture
def csv_file(sample_data):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        f.write("name,age,city\n")
        for row in sample_data:
            f.write(f"{row['name']},{row['age']},{row['city']}\n")
        path = f.name
    yield path
    os.unlink(path)


# --- read_csv ---

class TestReadCsv:
    def test_read_csv_basic(self, csv_file, sample_data):
        result = read_csv(csv_file)
        assert result == sample_data

    def test_read_csv_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_csv("nonexistent.csv")


# --- filter_rows ---

class TestFilterRows:
    def test_filter_rows_basic(self, sample_data):
        result = filter_rows(sample_data, "city", "NYC")
        assert len(result) == 2
        assert all(r["city"] == "NYC" for r in result)

    def test_filter_rows_no_match(self, sample_data):
        result = filter_rows(sample_data, "city", "Chicago")
        assert result == []

    def test_filter_rows_missing_column(self, sample_data):
        with pytest.raises(KeyError):
            filter_rows(sample_data, "nonexistent", "value")

    def test_filter_rows_empty_data(self):
        result = filter_rows([], "col", "val")
        assert result == []

    def test_filter_rows_does_not_mutate(self, sample_data):
        original_len = len(sample_data)
        filter_rows(sample_data, "city", "NYC")
        assert len(sample_data) == original_len


# --- sort_rows ---

class TestSortRows:
    def test_sort_rows_basic(self, sample_data):
        result = sort_rows(sample_data, "name")
        assert [r["name"] for r in result] == ["Alice", "Bob", "Charlie"]

    def test_sort_rows_reverse(self, sample_data):
        result = sort_rows(sample_data, "name", reverse=True)
        assert [r["name"] for r in result] == ["Charlie", "Bob", "Alice"]

    def test_sort_rows_missing_column(self, sample_data):
        with pytest.raises(KeyError):
            sort_rows(sample_data, "nonexistent")

    def test_sort_rows_empty(self):
        result = sort_rows([], "col")
        assert result == []

    def test_sort_rows_does_not_mutate_original(self, sample_data):
        original_order = [r["name"] for r in sample_data]
        sort_rows(sample_data, "name", reverse=True)
        assert [r["name"] for r in sample_data] == original_order


# --- add_column ---

class TestAddColumn:
    def test_add_column_basic(self, sample_data):
        result = add_column(sample_data, "status", "active")
        assert all(r["status"] == "active" for r in result)

    def test_add_column_default_empty(self, sample_data):
        result = add_column(sample_data, "status")
        assert all(r["status"] == "" for r in result)

    def test_add_column_already_exists(self, sample_data):
        with pytest.raises(ValueError):
            add_column(sample_data, "name")

    def test_add_column_empty_data(self):
        result = add_column([], "col")
        assert result == []

    def test_add_column_does_not_mutate_original(self, sample_data):
        original = [dict(row) for row in sample_data]
        result = add_column(sample_data, "status", "active")
        # Original data should be unchanged
        assert sample_data == original
        assert "status" not in sample_data[0]
        # Result should have the new column
        assert all(r["status"] == "active" for r in result)


# --- write_csv ---

class TestWriteCsv:
    def test_write_csv_basic(self, sample_data):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_csv(path, sample_data)
            result = read_csv(path)
            assert result == sample_data
        finally:
            os.unlink(path)

    def test_write_csv_empty(self):
        with pytest.raises(ValueError):
            write_csv("out.csv", [])


# --- aggregate ---

class TestAggregate:
    def test_count(self, sample_data):
        assert aggregate(sample_data, "name", "count") == "3"

    def test_sum(self, sample_data):
        assert aggregate(sample_data, "age", "sum") == "90.0"

    def test_avg(self, sample_data):
        assert aggregate(sample_data, "age", "avg") == "30.0"

    def test_min(self, sample_data):
        assert aggregate(sample_data, "age", "min") == "25"

    def test_max(self, sample_data):
        assert aggregate(sample_data, "age", "max") == "35"

    def test_unknown_func(self, sample_data):
        with pytest.raises(ValueError):
            aggregate(sample_data, "age", "median")

    def test_missing_column(self, sample_data):
        with pytest.raises(KeyError):
            aggregate(sample_data, "nonexistent", "count")

    def test_sum_non_numeric_raises(self, sample_data):
        with pytest.raises(ValueError):
            aggregate(sample_data, "name", "sum")

    def test_avg_non_numeric_raises(self, sample_data):
        with pytest.raises(ValueError):
            aggregate(sample_data, "name", "avg")

    def test_empty_data_raises_key_error(self):
        with pytest.raises(KeyError):
            aggregate([], "col", "count")
