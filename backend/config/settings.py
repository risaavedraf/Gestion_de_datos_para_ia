import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "Data"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"
REJECTED_DIR = DATA_DIR / "rejected"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
MODELS_DIR = BASE_DIR / "models"
RAW_CSV = BRONZE_DIR / "02_fraudTest.csv"

# Database
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://user:password@localhost:5432/fraud_db"
)

# App
APP_ENV = os.getenv("APP_ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DATA_SAMPLE_SIZE = int(os.getenv("DATA_SAMPLE_SIZE", "10000"))
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")
MODEL_DECISION_THRESHOLD = float(os.getenv("MODEL_DECISION_THRESHOLD", "0.3"))

# Pipeline
REQUIRED_COLUMNS = [
    "trans_date_trans_time",
    "cc_num",
    "merchant",
    "category",
    "amt",
    "first",
    "last",
    "gender",
    "street",
    "city",
    "state",
    "zip",
    "lat",
    "long",
    "city_pop",
    "job",
    "dob",
    "trans_num",
    "unix_time",
    "merch_lat",
    "merch_long",
    "is_fraud",
]

SILVER_REQUIRED_COLUMNS = [
    "trans_date_trans_time",
    "cc_num_masked",
    "merchant",
    "category",
    "amt",
    "gender",
    "city",
    "state",
    "zip",
    "lat",
    "long",
    "city_pop",
    "job",
    "trans_num",
    "unix_time",
    "merch_lat",
    "merch_long",
    "is_fraud",
    "trans_hour",
    "trans_day_of_week",
    "trans_month",
    "age_at_transaction",
    "distance_km",
]

VALID_CATEGORIES = [
    "personal_care",
    "health_fitness",
    "misc_pos",
    "travel",
    "kids_pets",
    "shopping_pos",
    "food_dining",
    "home",
    "entertainment",
    "shopping_net",
    "misc_net",
    "grocery_pos",
    "gas_transport",
    "grocery_net",
]

VALID_GENDERS = ["M", "F"]

# Coordinates (US bounds)
LAT_MIN, LAT_MAX = 25.0, 72.0
LONG_MIN, LONG_MAX = -180.0, -60.0

# Risk thresholds
RISK_LOW = 0.3
RISK_HIGH = 0.7
