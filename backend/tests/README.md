# Tests

## Running Tests

```bash
# All tests
pytest backend/tests/ -v

# Specific test file
pytest backend/tests/test_pipeline_e2e.py -v

# With coverage
pytest backend/tests/ --cov=backend --cov-report=html
```

## Test Structure

| File | Tests |
|------|-------|
| `test_pipeline_e2e.py` | End-to-end pipeline with sample data |
| `test_validation.py` | Validation rules (structural + semantic) |
| `test_cleaning.py` | Cleaning transformations |

## Environment

Tests use SQLite by default (no PostgreSQL needed locally).
