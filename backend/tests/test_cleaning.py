"""
Unit tests for Silver cleaning layer and utility functions (Task 7.2).
Tests type conversions, normalization, derived columns, PII masking,
and utility functions (haversine, age calculation, risk levels).
"""

import pytest
import pandas as pd

from backend.src.cleaning import clean
from backend.src.utils import (
    calculate_age_at_transaction,
    get_risk_level,
    haversine,
    mask_cc_num,
    mask_pii,
)


# ─── Utility function tests ───────────────────────────────────────


def test_haversine():
    """Test haversine distance: New York to Los Angeles ≈ 3,944 km."""
    dist = haversine(40.7128, -74.0060, 34.0522, -118.2437)
    assert 3900 < dist < 4000


def test_haversine_same_point():
    """Same point should return ~0 km."""
    dist = haversine(40.7128, -74.0060, 40.7128, -74.0060)
    assert dist < 0.01


def test_calculate_age():
    """Test age calculation from DOB to transaction date."""
    age = calculate_age_at_transaction("1990-01-01", "2020-06-21")
    assert age == 30


def test_mask_pii():
    """PII masking should produce consistent SHA256 hash."""
    hash1 = mask_pii("1234567890")
    hash2 = mask_pii("1234567890")
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA256 hex length


def test_mask_cc_num():
    """Credit card masking should show last 4 digits with *** prefix."""
    masked = mask_cc_num("1234567890123456")
    assert masked.endswith("3456")
    assert "***" in masked


def test_mask_cc_num_short():
    """Short cc_num (≤4 chars) should not be masked."""
    masked = mask_cc_num("123")
    assert masked == "123"


def test_risk_levels():
    """Test risk level classification at boundaries."""
    assert get_risk_level(0.1) == "low"
    assert get_risk_level(0.5) == "medium"
    assert get_risk_level(0.8) == "high"
    assert get_risk_level(0.3) == "medium"  # boundary: 0.3 is medium (not < 0.3)
    assert get_risk_level(0.7) == "high"  # boundary: 0.7 is high (not < 0.7)


# ─── Cleaning function tests ──────────────────────────────────────


@pytest.fixture
def bronze_data(tmp_path):
    """Create sample Bronze-format data for cleaning tests."""
    df = pd.DataFrame(
        {
            "Unnamed: 0": [0, 1, 2],
            "trans_date_trans_time": [
                "2020-06-21 12:14:25",
                "2020-06-21 12:15:00",
                "2020-06-21 12:16:00",
            ],
            "cc_num": [1234567890, 2345678901, 3456789012],
            "merchant": ["Shop A", "Shop B", "Shop C"],
            "category": ["SHOPPING_POS", "grocery_pos", "GAS_TRANSPORT"],
            "amt": [100.0, 50.0, 25.0],
            "first": ["John", "Jane", "Bob"],
            "last": ["Doe", "Smith", "Jones"],
            "gender": ["m", "F", "M"],
            "street": ["123 Main St", "456 Oak Ave", "789 Pine Rd"],
            "city": ["New York", "Boston", "Chicago"],
            "state": ["ny", "MA", "il"],
            "zip": ["10001", "02101", "60601"],
            "lat": [40.7128, 42.3601, 41.8781],
            "long": [-74.0060, -71.0589, -87.6298],
            "city_pop": [8000000, 700000, 2700000],
            "job": ["Engineer", "Teacher", "Doctor"],
            "dob": ["1990-01-01", "1995-05-15", "1985-12-30"],
            "trans_num": ["t001", "t002", "t003"],
            "unix_time": [1592745265, 1592745300, 1592745360],
            "merch_lat": [40.7200, 42.3700, 41.8800],
            "merch_long": [-74.0100, -71.0600, -87.6300],
            "is_fraud": [0, 1, 0],
        }
    )

    bronze_path = tmp_path / "fraud_bronze.parquet"
    df.to_parquet(bronze_path, index=False)
    return bronze_path


def _run_clean(bronze_data, tmp_path):
    """Helper: run clean with patched paths, return (result, silver_df)."""
    import backend.src.cleaning as clean_module

    original_bronze = clean_module.BRONZE_DIR
    original_silver = clean_module.SILVER_DIR

    clean_module.BRONZE_DIR = bronze_data.parent
    clean_module.SILVER_DIR = tmp_path / "silver"
    clean_module.SILVER_DIR.mkdir()

    result = clean(input_path=str(bronze_data))

    clean_module.BRONZE_DIR = original_bronze
    clean_module.SILVER_DIR = original_silver

    silver_df = pd.read_parquet(tmp_path / "silver" / "fraud_silver.parquet")
    return result, silver_df


def test_clean_drops_unnamed(bronze_data, tmp_path):
    """Unnamed: 0 technical column should be dropped."""
    result, silver_df = _run_clean(bronze_data, tmp_path)

    assert "Unnamed: 0" not in silver_df.columns


def test_clean_normalizes_strings(bronze_data, tmp_path):
    """Strings should be normalized: category=lower, gender/state=upper."""
    result, silver_df = _run_clean(bronze_data, tmp_path)

    # Category should be lowercase
    assert all(c.islower() or c == "_" for c in silver_df["category"].iloc[0])

    # Gender should be uppercase M/F
    assert all(g in ["M", "F"] for g in silver_df["gender"])

    # State should be uppercase
    assert all(s == s.upper() for s in silver_df["state"])


def test_clean_creates_derived_columns(bronze_data, tmp_path):
    """All 6 derived columns should be created."""
    result, silver_df = _run_clean(bronze_data, tmp_path)

    assert "trans_hour" in silver_df.columns
    assert "trans_day_of_week" in silver_df.columns
    assert "trans_month" in silver_df.columns
    assert "age_at_transaction" in silver_df.columns
    assert "distance_km" in silver_df.columns
    assert "cc_num_masked" in silver_df.columns


def test_clean_drops_pii(bronze_data, tmp_path):
    """PII columns (first, last, street, dob) should be dropped from Silver."""
    result, silver_df = _run_clean(bronze_data, tmp_path)

    assert "first" not in silver_df.columns
    assert "last" not in silver_df.columns
    assert "street" not in silver_df.columns
    assert "dob" not in silver_df.columns


def test_clean_output_count(bronze_data, tmp_path):
    """Output should have same row count as input (no rows lost in cleaning)."""
    result, silver_df = _run_clean(bronze_data, tmp_path)

    assert result["rows_in"] == 3
    assert result["rows_out"] == 3
