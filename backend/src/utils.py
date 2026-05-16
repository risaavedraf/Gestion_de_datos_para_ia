import hashlib
from math import radians, cos, sin, asin, sqrt
from datetime import datetime
from typing import Optional
import pandas as pd

def mask_pii(value: str) -> str:
    """SHA256 hash for PII masking"""
    return hashlib.sha256(str(value).encode()).hexdigest()

def mask_cc_num(cc_num: str) -> str:
    """Show only last 4 digits"""
    s = str(cc_num)
    if len(s) > 4:
        return "*" * (len(s) - 4) + s[-4:]
    return s

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two points using Haversine formula"""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in km
    return c * r

def calculate_age_at_transaction(dob, trans_date) -> Optional[int]:
    """Calculate approximate age at transaction date"""
    try:
        if pd.isna(dob) or pd.isna(trans_date):
            return None
        dob_dt = pd.to_datetime(dob)
        trans_dt = pd.to_datetime(trans_date)
        age = trans_dt.year - dob_dt.year
        if trans_dt.month < dob_dt.month or (trans_dt.month == dob_dt.month and trans_dt.day < dob_dt.day):
            age -= 1
        return age
    except Exception:
        return None

def get_risk_level(probability: float, low_threshold: float = 0.3, high_threshold: float = 0.7) -> str:
    """Classify risk level based on probability"""
    if probability < low_threshold:
        return "low"
    elif probability < high_threshold:
        return "medium"
    else:
        return "high"

def calculate_checksum(file_path: str) -> str:
    """Calculate SHA256 checksum of a file"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def generate_run_id() -> str:
    """Generate unique run ID"""
    import uuid
    return str(uuid.uuid4())
