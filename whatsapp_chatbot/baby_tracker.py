"""
Baby weight tracking — stores measurements per user (phone number + baby index)
and checks against WHO weight-for-age standards.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional
from pymongo import MongoClient, ASCENDING
from pymongo.server_api import ServerApi
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── WHO weight-for-age medians and -2SD / -3SD thresholds (boys, 0-24 months)
# Source: WHO Child Growth Standards, weight-for-age
# Format: age_months -> (median_kg, minus2sd_kg, minus3sd_kg)
# Using combined boys/girls average — close enough for screening
WHO_WEIGHT_FOR_AGE = {
    0:  (3.3, 2.5, 2.1),
    1:  (4.5, 3.4, 2.9),
    2:  (5.6, 4.3, 3.8),
    3:  (6.4, 5.0, 4.4),
    4:  (7.0, 5.6, 4.9),
    5:  (7.5, 6.0, 5.3),
    6:  (7.9, 6.4, 5.7),
    7:  (8.3, 6.7, 5.9),
    8:  (8.6, 7.0, 6.2),
    9:  (8.9, 7.2, 6.4),
    10: (9.2, 7.5, 6.6),
    11: (9.4, 7.7, 6.8),
    12: (9.6, 7.9, 7.0),
    13: (9.9, 8.1, 7.2),
    14: (10.1, 8.3, 7.4),
    15: (10.3, 8.5, 7.6),
    16: (10.5, 8.7, 7.7),
    17: (10.7, 8.9, 7.9),
    18: (10.9, 9.0, 8.1),
    19: (11.1, 9.2, 8.2),
    20: (11.3, 9.4, 8.4),
    21: (11.5, 9.6, 8.6),
    22: (11.8, 9.7, 8.7),
    23: (12.0, 9.9, 8.9),
    24: (12.2, 10.1, 9.0),
    30: (13.3, 11.0, 9.8),
    36: (14.3, 11.8, 10.6),
    42: (15.3, 12.6, 11.3),
    48: (16.3, 13.4, 12.0),
    54: (17.3, 14.2, 12.7),
    60: (18.3, 15.0, 13.4),
}


def _get_who_thresholds(age_months: int) -> Optional[tuple]:
    """Get WHO thresholds for the closest age in the table."""
    if age_months < 0 or age_months > 60:
        return None
    closest = min(WHO_WEIGHT_FOR_AGE.keys(), key=lambda k: abs(k - age_months))
    return WHO_WEIGHT_FOR_AGE[closest]


def assess_growth(age_months: int, weight_kg: float) -> dict:
    """
    Compare weight against WHO weight-for-age standards.
    Returns a dict with: status, message, alert_level (ok/warn/danger)
    """
    thresholds = _get_who_thresholds(age_months)
    if thresholds is None:
        return {
            "status": "unknown",
            "message": f"WHO data not available for age {age_months} months.",
            "alert_level": "ok"
        }

    median, minus2sd, minus3sd = thresholds

    if weight_kg < minus3sd:
        return {
            "status": "severe_underweight",
            "message": (
                f"Your baby weighs {weight_kg}kg at {age_months} months. "
                f"This is below the healthy range (expected around {median}kg). "
                f"This needs urgent attention — please take your baby to a health centre as soon as possible."
            ),
            "alert_level": "danger"
        }
    elif weight_kg < minus2sd:
        return {
            "status": "underweight",
            "message": (
                f"Your baby weighs {weight_kg}kg at {age_months} months. "
                f"This is slightly below the healthy range (expected around {median}kg). "
                f"Please visit your nearest health centre and focus on nutrient-dense foods."
            ),
            "alert_level": "warn"
        }
    else:
        return {
            "status": "normal",
            "message": (
                f"Your baby weighs {weight_kg}kg at {age_months} months. "
                f"This is within the healthy range (expected around {median}kg). Keep it up!"
            ),
            "alert_level": "ok"
        }


def check_growth_velocity(previous: dict, current_weight_kg: float, current_age_months: int) -> Optional[str]:
    """
    Compare current weight to previous measurement.
    Returns a warning string if no growth detected, else None.
    """
    if not previous:
        return None
    prev_weight = previous.get("weight_kg", 0)
    prev_age = previous.get("age_months", 0)
    age_gap = current_age_months - prev_age

    # Only flag if we have a meaningful time gap (at least 3 weeks ~ 1 month)
    if age_gap < 1:
        return None

    weight_gain = current_weight_kg - prev_weight

    # Under 6 months: expect ~0.5-1kg/month; 6-12 months ~0.3-0.5kg/month
    if current_age_months <= 6 and weight_gain < 0.2:
        return (
            f"⚠️ Your baby has gained only {weight_gain:.2f}kg in the past {age_gap} month(s). "
            f"Babies under 6 months should gain at least 0.5kg per month. "
            f"Please visit your health centre."
        )
    elif current_age_months <= 12 and weight_gain < 0.1:
        return (
            f"⚠️ Your baby has gained only {weight_gain:.2f}kg in the past {age_gap} month(s). "
            f"Please visit your nearest health centre to check on your baby's growth."
        )
    return None


# ── MongoDB helpers ──────────────────────────────────────────────────────────

_weights_collection = None


def _get_collection():
    """Return a cached collection handle. A previous version opened a brand-new
    MongoClient on every call and never closed it — over time (e.g. many weight
    logs, or the recurring reminder task) this leaks connections and can start
    causing intermittent DB failures, which was likely contributing to tips/
    reminders silently stopping. We now open the client once and reuse it."""
    global _weights_collection
    if _weights_collection is not None:
        return _weights_collection

    uri = os.getenv("MONGO_URI")
    if not uri:
        return None
    try:
        client = MongoClient(uri, server_api=ServerApi("1"))
        db = client.get_database("Jeun-daada")
        col = db.get_collection("baby_weights")
        col.create_index([("phone_number", ASCENDING), ("baby_index", ASCENDING), ("recorded_at", ASCENDING)])
        _weights_collection = col
        return col
    except Exception as e:
        logger.error(f"❌ baby_tracker DB connection failed: {e}")
        return None


def save_weight(phone_number: str, baby_index: int, age_months: int, weight_kg: float) -> dict:
    """
    Save a weight entry and return the WHO assessment + growth velocity check.
    baby_index: 1, 2, or 3 (which baby for this mother)
    """
    col = _get_collection()
    if col is None:
        return {"error": "Database unavailable"}

    # Get the most recent previous entry for this baby
    previous = col.find_one(
        {"phone_number": phone_number, "baby_index": baby_index},
        sort=[("recorded_at", -1)]
    )

    # Save new entry
    entry = {
        "phone_number": phone_number,
        "baby_index": baby_index,
        "age_months": age_months,
        "weight_kg": weight_kg,
        "recorded_at": datetime.now(timezone.utc)
    }
    col.insert_one(entry)
    logger.info(f"✅ Saved weight: {phone_number} baby#{baby_index} — {weight_kg}kg at {age_months}mo")

    # Assess
    assessment = assess_growth(age_months, weight_kg)
    velocity_warning = check_growth_velocity(previous, weight_kg, age_months)

    return {
        "assessment": assessment,
        "velocity_warning": velocity_warning,
        "previous": {
            "weight_kg": previous["weight_kg"],
            "age_months": previous["age_months"]
        } if previous else None
    }


def has_logged_weight_this_month(phone_number: str, now: Optional[datetime] = None) -> bool:
    """Return True if this user has already logged ANY baby's weight in the
    current calendar month.

    Weighing is a monthly check-in, so once a mother has logged a weight there
    is nothing more to remind her about until next month. The weekly reminder
    job uses this to skip her for the rest of the month instead of nagging her
    every week.

    Matches on phone_number only (not baby_index): a mother with several babies
    who has logged at least one weight this month has clearly done her check-in
    and shouldn't be chased again.

    On a DB error this returns False (i.e. "not logged yet"), which makes the
    caller send the reminder. Failing toward sending is deliberate — a
    duplicate reminder is a much smaller problem than silently dropping the
    reminder entirely, which is the failure mode we have been fixing.
    """
    col = _get_collection()
    if col is None:
        return False

    now = now or datetime.now(timezone.utc)
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    try:
        return col.count_documents(
            {"phone_number": phone_number, "recorded_at": {"$gte": start_of_month}},
            limit=1
        ) > 0
    except Exception as e:
        logger.error(f"❌ has_logged_weight_this_month error for {phone_number}: {e}")
        return False


def get_weight_history(phone_number: str, baby_index: int) -> list:
    """Return all weight entries for a baby, oldest first."""
    col = _get_collection()
    if col is None:
        return []
    entries = list(col.find(
        {"phone_number": phone_number, "baby_index": baby_index},
        sort=[("recorded_at", ASCENDING)],
        projection={"_id": 0, "phone_number": 0}
    ))
    return entries


_conversations_collection = None


def get_all_user_threads() -> list:
    """
    Return all unique phone numbers to send weight reminders to.
    Pulls from the conversations collection (all users who have ever chatted),
    not just those who have already logged a weight.
    """
    global _conversations_collection
    uri = os.getenv("MONGO_URI")
    if not uri:
        return []
    try:
        if _conversations_collection is None:
            client = MongoClient(uri, server_api=ServerApi("1"))
            db = client.get_database("Helpmum-Hackthon")
            _conversations_collection = db.get_collection("conversations")
        numbers = _conversations_collection.distinct("user_id")
        logger.info(f"📋 Found {len(numbers)} user(s) from conversations for reminders")
        return [n for n in numbers if n]
    except Exception as e:
        logger.error(f"❌ get_all_user_threads error: {e}")
        return []