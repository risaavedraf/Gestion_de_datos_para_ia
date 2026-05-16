"""
Seed script for production deployments.

Generates a small synthetic fraud dataset and runs the full pipeline
(bronze → silver → gold) so the app has data on first boot.

Idempotent: skips generation if the raw CSV already exists.
"""
import sys
import os
import uuid
import random
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running as `python backend/seed.py` from project root
# ---------------------------------------------------------------------------
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

# Reduce log noise from third-party libs
os.environ.setdefault("LOG_LEVEL", "WARNING")

from config.settings import (
    BRONZE_DIR,
    RAW_CSV,
    VALID_CATEGORIES,
    VALID_GENDERS,
    LAT_MIN,
    LAT_MAX,
    LONG_MIN,
    LONG_MAX,
)

SAMPLE_SIZE = 500

# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

# Realistic-looking sample data pools
_MERCHANTS = [
    "fraud_Rippin, Kub and Mann", "fraud_Kunde-Sanford",
    "fraud_Schoen, Kuphal and Nitzsche", "fraud_Lemke and Sons",
    "fraud_Greenfelder, Grant and Feest", "fraud_Kohler Inc",
    "fraud_Cassin, Harber and Murray", "fraud_Romaguera Ltd",
    "fraud_Schumm PLC", "fraud_Corwin-Hartmann",
    "fraud_O'Kon Ltd", "fraud_Bauch LLC",
]

_CITIES = [
    "Houston", "Dallas", "New York", "Los Angeles", "Chicago",
    "Phoenix", "San Antonio", "San Diego", "Philadelphia", "Miami",
]

_STATES = [
    "TX", "CA", "NY", "FL", "IL", "PA", "OH", "GA", "NC", "MI",
]

_JOBS = [
    "Engineer, mining", "Psychologist, clinical",
    "Environmental consultant", "Patent attorney",
    "IT consultant", "Therapist, occupational",
    "Audiologist", "Systems analyst",
    "Architect", "Teacher, secondary school",
]


def _random_datetime(start: datetime, end: datetime) -> datetime:
    """Return a random datetime between start and end."""
    delta = end - start
    random_seconds = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=random_seconds)


def generate_synthetic_data(n: int = SAMPLE_SIZE) -> list[dict]:
    """Generate *n* rows matching the original fraud dataset schema."""
    rows = []
    base_date = datetime(2019, 1, 1)
    end_date = datetime(2020, 12, 31)

    for _ in range(n):
        trans_dt = _random_datetime(base_date, end_date)
        dob = _random_datetime(datetime(1950, 1, 1), datetime(2002, 12, 31))

        lat = round(random.uniform(LAT_MIN, LAT_MAX), 6)
        long_ = round(random.uniform(LONG_MIN, LONG_MAX), 6)
        # Merchant location: small offset from cardholder location
        merch_lat = round(lat + random.uniform(-2.0, 2.0), 6)
        merch_long = round(long_ + random.uniform(-2.0, 2.0), 6)
        # Clamp to valid bounds
        merch_lat = max(LAT_MIN, min(LAT_MAX, merch_lat))
        merch_long = max(LONG_MIN, min(LONG_MAX, merch_long))

        rows.append({
            "trans_date_trans_time": trans_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "cc_num": str(random.randint(4000000000000000, 4999999999999999)),
            "merchant": random.choice(_MERCHANTS),
            "category": random.choice(VALID_CATEGORIES),
            "amt": round(random.uniform(1.0, 5000.0), 2),
            "first": f"User{random.randint(1, 999)}",
            "last": f"Last{random.randint(1, 999)}",
            "gender": random.choice(VALID_GENDERS),
            "street": f"{random.randint(1, 9999)} Main St",
            "city": random.choice(_CITIES),
            "state": random.choice(_STATES),
            "zip": str(random.randint(10000, 99999)),
            "lat": lat,
            "long": long_,
            "city_pop": random.randint(1000, 9000000),
            "job": random.choice(_JOBS),
            "dob": dob.strftime("%Y-%m-%d"),
            "trans_num": str(uuid.uuid4()),
            "unix_time": int(trans_dt.timestamp()),
            "merch_lat": merch_lat,
            "merch_long": merch_long,
            "is_fraud": random.choices([0, 1], weights=[95, 5])[0],
        })

    return rows


def seed_data() -> None:
    """Generate synthetic CSV and run the full pipeline if data is missing."""
    # --- Idempotency gate ---
    if RAW_CSV.exists():
        print(f"[seed] Raw CSV already exists at {RAW_CSV} — skipping generation.")
    else:
        print(f"[seed] Generating {SAMPLE_SIZE} synthetic rows → {RAW_CSV}")
        BRONZE_DIR.mkdir(parents=True, exist_ok=True)

        import csv
        rows = generate_synthetic_data(SAMPLE_SIZE)
        fieldnames = list(rows[0].keys())

        with open(RAW_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"[seed] Wrote {len(rows)} rows to {RAW_CSV}")

    # --- Run pipeline stages ---
    from src.ingestion import ingest
    from src.cleaning import clean
    from src.validation import validate

    print("[seed] Running bronze (ingest)...")
    result = ingest(sample_size=SAMPLE_SIZE)
    print(f"[seed]   → {result.get('status')} | {result.get('rows', '?')} rows")

    print("[seed] Running silver (clean)...")
    result = clean(sample_size=SAMPLE_SIZE)
    print(f"[seed]   → {result.get('status')} | {result.get('rows_out', '?')} rows")

    print("[seed] Running gold (validate)...")
    result = validate(sample_size=SAMPLE_SIZE)
    print(f"[seed]   → {result.get('status')} | valid={result.get('valid', '?')} rejected={result.get('rejected', '?')}")

    print("[seed] Pipeline complete. Data layers populated.")


if __name__ == "__main__":
    seed_data()
