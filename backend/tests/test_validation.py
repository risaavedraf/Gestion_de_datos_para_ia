"""
Unit tests for Gold validation layer (Task 7.1).
Tests structural rules, semantic rules, and edge cases
using tmp_path to isolate test data from real pipeline.
"""

import pytest
import pandas as pd

from backend.src.validation import validate


@pytest.fixture
def sample_data():
    """Create sample Silver-format data for testing."""
    return pd.DataFrame(
        {
            "trans_date_trans_time": [
                "2020-06-21 12:14:25",
                "2020-06-21 12:15:00",
                "2020-06-21 12:16:00",
            ],
            "cc_num": ["1234567890", "2345678901", "3456789012"],
            "cc_num_masked": ["abc123", "def456", "ghi789"],
            "merchant": ["fraud_Shop A", "fraud_Shop B", "fraud_Shop C"],
            "category": ["shopping_pos", "grocery_pos", "gas_transport"],
            "amt": [100.0, 50.0, 25.0],
            "gender": ["M", "F", "M"],
            "city": ["New York", "Boston", "Chicago"],
            "state": ["NY", "MA", "IL"],
            "zip": ["10001", "02101", "60601"],
            "lat": [40.7128, 42.3601, 41.8781],
            "long": [-74.0060, -71.0589, -87.6298],
            "city_pop": [8000000, 700000, 2700000],
            "job": ["Engineer", "Teacher", "Doctor"],
            "trans_num": ["t001", "t002", "t003"],
            "unix_time": [1592745265, 1592745300, 1592745360],
            "merch_lat": [40.7200, 42.3700, 41.8800],
            "merch_long": [-74.0100, -71.0600, -87.6300],
            "is_fraud": [0, 1, 0],
            "trans_hour": [12, 12, 12],
            "trans_day_of_week": [6, 6, 6],
            "trans_month": [6, 6, 6],
            "age_at_transaction": [35, 28, 42],
            "distance_km": [5.2, 3.1, 8.7],
        }
    )


def _run_validate(sample_data, tmp_path):
    """Helper: save data as Silver parquet, run validate with patched paths."""
    silver_path = tmp_path / "silver.parquet"
    sample_data.to_parquet(silver_path, index=False)

    import backend.src.validation as val_module

    original_silver = val_module.SILVER_DIR
    original_gold = val_module.GOLD_DIR
    original_rejected = val_module.REJECTED_DIR

    val_module.SILVER_DIR = tmp_path
    val_module.GOLD_DIR = tmp_path / "gold"
    val_module.REJECTED_DIR = tmp_path / "rejected"
    val_module.GOLD_DIR.mkdir()
    val_module.REJECTED_DIR.mkdir()

    result = validate(input_path=str(silver_path))

    val_module.SILVER_DIR = original_silver
    val_module.GOLD_DIR = original_gold
    val_module.REJECTED_DIR = original_rejected

    return result


def test_valid_data_passes(sample_data, tmp_path):
    """All valid records should pass validation."""
    result = _run_validate(sample_data, tmp_path)

    assert result["status"] == "success"
    assert result["valid"] == 3
    assert result["rejected"] == 0


def test_negative_amount_rejected(sample_data, tmp_path):
    """Negative amounts should be rejected with amt_invalid reason."""
    sample_data.loc[0, "amt"] = -50.0

    result = _run_validate(sample_data, tmp_path)

    assert result["valid"] == 2
    assert result["rejected"] == 1
    assert "amt_invalid" in result["rejection_breakdown"]


def test_invalid_gender_rejected(sample_data, tmp_path):
    """Invalid gender value should be rejected with invalid_gender reason."""
    sample_data.loc[0, "gender"] = "X"

    result = _run_validate(sample_data, tmp_path)

    assert result["valid"] == 2
    assert result["rejected"] == 1
    assert "invalid_gender" in result["rejection_breakdown"]


def test_invalid_category_rejected(sample_data, tmp_path):
    """Category not in VALID_CATEGORIES should be rejected."""
    sample_data.loc[0, "category"] = "invalid_category"

    result = _run_validate(sample_data, tmp_path)

    assert result["valid"] == 2
    assert result["rejected"] == 1
    assert "invalid_category" in result["rejection_breakdown"]


def test_invalid_is_fraud_rejected(sample_data, tmp_path):
    """is_fraud values outside {0, 1} should be rejected."""
    sample_data.loc[0, "is_fraud"] = 2

    result = _run_validate(sample_data, tmp_path)

    assert result["valid"] == 2
    assert result["rejected"] == 1
    assert "invalid_is_fraud" in result["rejection_breakdown"]


def test_duplicate_trans_num_rejected(sample_data, tmp_path):
    """Duplicate trans_num should be rejected with duplicate_trans_num reason."""
    sample_data.loc[1, "trans_num"] = "t001"

    result = _run_validate(sample_data, tmp_path)

    assert result["valid"] == 2
    assert result["rejected"] == 1
    assert "duplicate_trans_num" in result["rejection_breakdown"]
