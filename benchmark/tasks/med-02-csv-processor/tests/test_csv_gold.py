"""Gold-standard test suite for csv_processor module."""

import sys
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from csv_processor import read_csv, filter_rows, sort_rows, add_column, write_csv, aggregate


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV file and return its path."""
    p = tmp_path / "sample.csv"
    p.write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,NYC\n")
    return str(p)


@pytest.fixture
def sample_data():
    return [
        {"name": "Alice", "age": "30", "city": "NYC"},
        {"name": "Bob", "age": "25", "city": "LA"},
        {"name": "Charlie", "age": "35", "city": "NYC"},
    ]


# ── read_csv tests ───────────────────────────────────────────────────────────

class TestReadCsv:
    def test_read_basic(self, sample_csv, sample_data):
        result = read_csv(sample_csv)
        assert result == sample_data

    def test_read_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_csv("/nonexistent/path/file.csv")

    def test_read_single_row(self, tmp_path):
        p = tmp_path / "one.csv"
        p.write_text("x,y\n1,2\n")
        result = read_csv(str(p))
        assert result == [{"x": "1", "y": "2"}]

    def test_read_empty_body(self, tmp_path):
        """CSV with headers only should return empty list."""
        p = tmp_path / "empty.csv"
        p.write_text("a,b,c\n")
        result = read_csv(str(p))
        assert result == []

    def test_read_preserves_order_of_rows(self, sample_csv):
        result = read_csv(sample_csv)
        assert [r["name"] for r in result] == ["Alice", "Bob", "Charlie"]


# ── filter_rows tests ────────────────────────────────────────────────────────

class TestFilterRows:
    def test_filter_matching(self, sample_data):
        result = filter_rows(sample_data, "city", "NYC")
        assert len(result) == 2
        assert all(r["city"] == "NYC" for r in result)

    def test_filter_no_match(self, sample_data):
        result = filter_rows(sample_data, "city", "Chicago")
        assert result == []

    def test_filter_missing_column(self, sample_data):
        with pytest.raises(KeyError):
            filter_rows(sample_data, "country", "US")

    def test_filter_empty_data_with_missing_column(self):
        """Empty data means column cannot be verified; should raise KeyError."""
        with pytest.raises(KeyError):
            filter_rows([], "anything", "val")


# ── sort_rows tests ──────────────────────────────────────────────────────────

class TestSortRows:
    def test_sort_ascending(self, sample_data):
        result = sort_rows(sample_data, "name")
        assert [r["name"] for r in result] == ["Alice", "Bob", "Charlie"]

    def test_sort_descending(self, sample_data):
        result = sort_rows(sample_data, "name", reverse=True)
        assert [r["name"] for r in result] == ["Charlie", "Bob", "Alice"]

    def test_sort_missing_column(self, sample_data):
        with pytest.raises(KeyError):
            sort_rows(sample_data, "salary")

    def test_sort_does_not_mutate(self, sample_data):
        original = [dict(row) for row in sample_data]
        sort_rows(sample_data, "name", reverse=True)
        assert sample_data == original


# ── add_column tests ─────────────────────────────────────────────────────────

class TestAddColumn:
    def test_add_new_column_default(self, sample_data):
        result = add_column(sample_data, "country")
        assert all(r["country"] == "" for r in result)

    def test_add_new_column_custom_default(self, sample_data):
        result = add_column(sample_data, "country", default="US")
        assert all(r["country"] == "US" for r in result)

    def test_add_existing_column_raises(self, sample_data):
        with pytest.raises(ValueError):
            add_column(sample_data, "name")

    def test_add_column_empty_data(self):
        result = add_column([], "col")
        assert result == []


# ── write_csv tests ──────────────────────────────────────────────────────────

class TestWriteCsv:
    def test_write_and_read_back(self, tmp_path, sample_data):
        p = str(tmp_path / "out.csv")
        write_csv(p, sample_data)
        result = read_csv(p)
        assert result == sample_data

    def test_write_empty_raises(self, tmp_path):
        with pytest.raises(ValueError):
            write_csv(str(tmp_path / "empty.csv"), [])

    def test_write_creates_file(self, tmp_path, sample_data):
        p = tmp_path / "new.csv"
        assert not p.exists()
        write_csv(str(p), sample_data)
        assert p.exists()


# ── aggregate tests ──────────────────────────────────────────────────────────

class TestAggregate:
    def test_count(self, sample_data):
        result = aggregate(sample_data, "name", "count")
        assert result == "3"

    def test_sum(self, sample_data):
        result = aggregate(sample_data, "age", "sum")
        assert result == "90" or float(result) == 90.0

    def test_avg(self, sample_data):
        result = aggregate(sample_data, "age", "avg")
        assert float(result) == 30.0

    def test_min(self, sample_data):
        result = aggregate(sample_data, "age", "min")
        assert result == "25"

    def test_max(self, sample_data):
        result = aggregate(sample_data, "age", "max")
        assert result == "35"

    def test_unknown_func_raises(self, sample_data):
        with pytest.raises(ValueError):
            aggregate(sample_data, "age", "median")

    def test_missing_column_raises(self, sample_data):
        with pytest.raises(KeyError):
            aggregate(sample_data, "salary", "count")

    def test_count_on_string_column(self, sample_data):
        result = aggregate(sample_data, "city", "count")
        assert result == "3"
