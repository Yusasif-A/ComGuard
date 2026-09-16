"""
User settings and conversation log.

Two collections:

  user_settings   one document per phone number — chosen language, whether they
                  accepted the terms, and whether they opted in to area alerts
                  (with the home location those alerts are matched against)
  conversations   a rolling log of what was asked and answered, for debugging
                  and for the transparency record

This is the one place that still holds phone numbers, and it has to: WhatsApp
needs a number to deliver a message to. Reports never reference it — they carry
a pseudonym instead (see anonymiser.reporter_pseudonym), so the link between
"who reported the collapsed bridge" and "this phone number" does not exist in
the report data at all.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pymongo import DESCENDING, MongoClient
from pymongo.server_api import ServerApi

from anonymiser import scrub_text
from config import DEFAULT_LANGUAGE, LANGUAGES

logger = logging.getLogger(__name__)

DB_NAME = os.getenv("MONGO_DB_NAME", "comguard")

_conversations = None
_settings = None
_connected = False


def _connect():
    global _conversations, _settings, _connected
    if _connected:
        return _conversations, _settings

    _connected = True
    uri = os.getenv("MONGO_URI")
    if not uri:
        logger.warning("⚠️ MONGO_URI not set — settings and conversations will not persist")
        return None, None

    try:
        client = MongoClient(uri, server_api=ServerApi("1"))
        client.admin.command("ping")
        db = client.get_database(DB_NAME)
        _conversations = db.get_collection("conversations")
        _settings = db.get_collection("user_settings")
        _settings.create_index("user_id", unique=True)
        _conversations.create_index([("timestamp", DESCENDING)])
        logger.info(f"✅ Connected to MongoDB for settings (db: {DB_NAME})")
        return _conversations, _settings
    except Exception as e:
        logger.error(f"❌ Settings DB connection failed: {e}")
        return None, None


# ── Language ─────────────────────────────────────────────────────────────────

def get_user_language(user_id: str) -> str:
    """The user's chosen language, falling back to English.

    A stored language that is no longer configured (say Arabic is switched off
    again) falls back too, so a deployment change can never strand someone in a
    language the service can no longer speak.
    """
    _, settings = _connect()
    if settings is None:
        return DEFAULT_LANGUAGE
    try:
        doc = settings.find_one({"user_id": user_id})
        chosen = (doc or {}).get("language")
        if chosen in LANGUAGES:
            return chosen
    except Exception as e:
        logger.error(f"Error reading language for {user_id}: {e}")
    return DEFAULT_LANGUAGE


def set_user_language(user_id: str, language: str) -> bool:
    _, settings = _connect()
    if settings is None:
        return False
    try:
        settings.update_one(
            {"user_id": user_id},
            {"$set": {"language": language, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        logger.info(f"🌍 {user_id} language set to {language}")
        return True
    except Exception as e:
        logger.error(f"Failed to set language: {e}")
        return False


# ── Terms ────────────────────────────────────────────────────────────────────

def has_accepted_terms(user_id: str) -> bool:
    _, settings = _connect()
    if settings is None:
        # No database means we cannot know. Treating that as "accepted" would
        # start a conversation with someone who never saw the privacy terms, so
        # the gate stays closed and they are shown the terms again.
        return False
    try:
        doc = settings.find_one({"user_id": user_id})
        return bool(doc and doc.get("terms_accepted") is True)
    except Exception as e:
        logger.error(f"Error checking terms for {user_id}: {e}")
        return False


def set_terms_accepted(user_id: str) -> bool:
    _, settings = _connect()
    if settings is None:
        return False
    try:
        settings.update_one(
            {"user_id": user_id},
            {"$set": {
                "terms_accepted": True,
                "terms_accepted_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        logger.info(f"✅ {user_id} accepted the terms")
        return True
    except Exception as e:
        logger.error(f"Failed to record terms acceptance: {e}")
        return False


# ── Area alerts ──────────────────────────────────────────────────────────────

def set_alert_optin(user_id: str, opted_in: bool, lat: float = None, lon: float = None) -> bool:
    """Opt in or out of safety broadcasts for the user's area.

    Opting in stores a home location, because "nearby" has to be measured
    against something. Opting out clears it — an opted-out user's location has
    no remaining purpose, so keeping it would be storing a location for nothing.
    """
    _, settings = _connect()
    if settings is None:
        return False

    update = {"alerts_opted_in": opted_in, "updated_at": datetime.now(timezone.utc)}
    if opted_in and lat is not None and lon is not None:
        update["alert_location"] = {"lat": float(lat), "lon": float(lon)}
        update["alert_location_set_at"] = datetime.now(timezone.utc)

    unset = {} if opted_in else {"alert_location": ""}

    try:
        operation = {"$set": update}
        if unset:
            operation["$unset"] = unset
        settings.update_one({"user_id": user_id}, operation, upsert=True)
        logger.info(f"🔔 {user_id} alert opt-in set to {opted_in}")
        return True
    except Exception as e:
        logger.error(f"Failed to set alert opt-in: {e}")
        return False


def remember_location(user_id: str, lat: float, lon: float) -> bool:
    """Store a location WITHOUT opting the user in to alerts.

    Someone who shares a location to route a report has not agreed to be
    messaged later. Keeping the coordinate lets a subsequent "Yes, warn me" work
    in one tap, while get_alert_subscribers still ignores them because
    alerts_opted_in is untouched.
    """
    _, settings = _connect()
    if settings is None:
        return False
    try:
        settings.update_one(
            {"user_id": user_id},
            {"$set": {
                "alert_location": {"lat": float(lat), "lon": float(lon)},
                "alert_location_set_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        return True
    except Exception as e:
        logger.error(f"Failed to remember location: {e}")
        return False


def get_alert_subscribers() -> List[dict]:
    """Everyone who opted in to alerts AND has a location to match against.

    Both conditions are required: a subscriber with no location cannot be told
    whether an incident is near them, and sending them everything regardless
    would make the alert channel worthless.
    """
    _, settings = _connect()
    if settings is None:
        return []
    try:
        return list(settings.find(
            {
                "alerts_opted_in": True,
                "terms_accepted": True,
                "alert_location": {"$exists": True, "$ne": None},
            },
            {"user_id": 1, "language": 1, "alert_location": 1, "_id": 0},
        ))
    except Exception as e:
        logger.error(f"Error fetching alert subscribers: {e}")
        return []


def is_alert_subscriber(user_id: str) -> bool:
    _, settings = _connect()
    if settings is None:
        return False
    try:
        doc = settings.find_one({"user_id": user_id})
        return bool(doc and doc.get("alerts_opted_in"))
    except Exception:
        return False


# ── Conversation log ─────────────────────────────────────────────────────────

def store_conversation(user_id: str, question: str, response: str,
                       message_type: str = "text", report_id: str = "") -> bool:
    """Append one exchange to the log.

    Both sides are scrubbed before writing. People describe scams by quoting
    them — "he said to send it to 0123456789" — so the log would otherwise
    accumulate exactly the identifiers the rest of the system works to remove.
    """
    conversations, _ = _connect()
    if conversations is None:
        return False
    try:
        conversations.insert_one({
            "user_id": user_id,
            "question": scrub_text(question or "")[:4000],
            "response": scrub_text(response or "")[:8000],
            "message_type": message_type,
            "report_id": report_id,
            "timestamp": datetime.now(timezone.utc),
        })
        return True
    except Exception as e:
        logger.error(f"Failed to store conversation: {e}")
        return False


def recent_conversations(user_id: str = "", limit: int = 100) -> List[Dict]:
    conversations, _ = _connect()
    if conversations is None:
        return []
    query = {"user_id": user_id} if user_id else {}
    try:
        return list(conversations.find(query, {"_id": 0})
                    .sort("timestamp", DESCENDING)
                    .limit(max(1, min(limit, 1000))))
    except Exception as e:
        logger.error(f"Failed to read conversations: {e}")
        return []


def get_user_settings(user_id: str) -> Optional[dict]:
    _, settings = _connect()
    if settings is None:
        return None
    try:
        return settings.find_one({"user_id": user_id}, {"_id": 0})
    except Exception:
        return None
