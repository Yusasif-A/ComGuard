import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Pending responses for feedback pairing (stores question and response for feedback)
pending_responses: Dict[str, Dict[str, str]] = {}


_db = None  # cached db handle, used to hand out further collections (scheduler state, tip log)
            # without opening yet another MongoClient (see baby_tracker._get_collection
            # for why we cache clients rather than reconnecting every call).


def get_feedback_collection():
    """Initialize MongoDB connection and return feedback collection"""
    global _db
    connection_string = os.getenv("MONGO_URI")
    if not connection_string:
        logger.warning("MONGO_URI is not set - feedback logging will be disabled")
        return None, None

    try:
        client = MongoClient(connection_string, server_api=ServerApi('1'))
        client.admin.command('ping')
        logger.info("Connected to MongoDB Atlas for feedback!")
        db = client.get_database("Helpmum-Hackthon")
        _db = db
        return db.get_collection("conversations"), db.get_collection("user_settings")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return None, None


# Initialize collections
feedback_collection, settings_collection = get_feedback_collection()

# scheduler_state: one doc per background job (_id="daily_tips", "weight_reminders", ...)
# holding when it last actually ran. tip_send_log: one doc per tip sent, for a durable,
# queryable audit trail ("who got a tip and when") that survives container restarts —
# unlike container logs, which reset/scroll away every time the process restarts.
scheduler_collection = _db.get_collection("scheduler_state") if _db is not None else None
tip_send_log_collection = _db.get_collection("tip_send_log") if _db is not None else None


def store_feedback(thread_id: str, user_name: str, question: str, response: str, feedback_type: str) -> bool:
    """
    Store user feedback in MongoDB
    
    Args:
        thread_id: User's phone number (thread identifier)
        user_name: User's display name
        question: User's original question
        response: Agent's response
        feedback_type: 'like' or 'dislike'
    
    Returns:
        bool: True if stored successfully, False otherwise
    """
    if feedback_collection is None:
        logger.warning(f"⚠️ MongoDB feedback collection not available")
        return False
    
    try:
        feedback_doc = {
            "user_id": thread_id,
            "user_name": user_name,
            "question": question,
            "response": response,
            "feedback": feedback_type,
            "timestamp": datetime.utcnow()
        }
        result = feedback_collection.insert_one(feedback_doc)
        logger.info(f"✅ Feedback saved to MongoDB! ID: {result.inserted_id}")
        logger.info(f"   User: {user_name} ({thread_id})")
        logger.info(f"   Question: {question[:100]}...")
        logger.info(f"   Response: {response[:100]}...")
        logger.info(f"   Feedback: {feedback_type}")
        return True
    except Exception as e:
        logger.error(f"Failed to store feedback: {e}")
        return False


def store_conversation(thread_id: str, user_name: str, question: str, response: str, feedback: str = None) -> bool:
    """
    Store conversation (question + response) in MongoDB.
    Feedback is optional and can be updated later.
    
    Args:
        thread_id: User's phone number
        user_name: User's display name
        question: User's question
        response: Agent's response
        feedback: 'like', 'dislike', or None
    
    Returns:
        bool: Success
    """
    if feedback_collection is None:
        logger.warning("⚠️ MongoDB feedback collection not available")
        return False
    
    try:
        doc = {
            "user_id": thread_id,
            "user_name": user_name,
            "question": question,
            "response": response,
            "feedback": feedback,  # None initially
            "timestamp": datetime.utcnow(),
            "has_feedback": feedback is not None
        }
        
        # Use upsert: if feedback comes later, we can update it
        result = feedback_collection.update_one(
            {
                "user_id": thread_id,
                "question": question,
                "response": response,
                "timestamp": {"$gte": datetime.utcnow() - timedelta(minutes=10)}  # rough match
            },
            {"$set": doc},
            upsert=True
        )
        
        if result.upserted_id:
            logger.info(f"✅ Conversation saved (new): {result.upserted_id}")
        elif result.modified_count > 0:
            logger.info("✅ Conversation feedback updated")
        else:
            logger.info("✅ Conversation saved (matched existing)")
            
        return True
    except Exception as e:
        logger.error(f"Failed to store conversation: {e}")
        return False

def store_pending_response(thread_id: str, question: str, response: str):
    """
    Store a pending response waiting for feedback
    
    Args:
        thread_id: User's phone number (thread identifier)
        question: User's original question
        response: Agent's response
    """
    pending_responses[thread_id] = {
        "question": question,
        "response": response
    }
    logger.info(f"[Feedback] Stored question & response for feedback tracking (thread: {thread_id})")


def get_pending_response(thread_id: str) -> Optional[Dict[str, str]]:
    """
    Get and remove pending response for a user
    
    Args:
        thread_id: User's phone number (thread identifier)
    
    Returns:
        Dict with 'question' and 'response' keys, or None if not found
    """
    return pending_responses.pop(thread_id, None)


def has_pending_response(thread_id: str) -> bool:
    """
    Check if there's a pending response for a user
    
    Args:
        thread_id: User's phone number (thread identifier)
    
    Returns:
        bool: True if pending response exists
    """
    return thread_id in pending_responses


def get_user_language(thread_id: str) -> str:
    """Get user's preferred language from MongoDB, default to 'english'"""
    if settings_collection is None:
        return "english"
    try:
        user_settings = settings_collection.find_one({"user_id": thread_id})
        if user_settings and "language" in user_settings:
            return user_settings["language"]
    except Exception as e:
        logger.error(f"Error getting user language: {e}")
    return "english"

def is_new_user(thread_id: str) -> bool:
    """Return True if this phone number has never appeared in the conversations collection."""
    if feedback_collection is None:
        return False
    try:
        return feedback_collection.count_documents({"user_id": thread_id}, limit=1) == 0
    except Exception as e:
        logger.error(f"Error checking new user: {e}")
        return False


def has_accepted_terms(thread_id: str) -> bool:
    """
    Return True if the user has approved the Terms of Use / Privacy Policy.

    Grandfathers existing users: anyone who already has a saved `journey`
    (i.e. went through the OLD combined onboarding message, which already
    included the terms/privacy links) is treated as having accepted, so we
    don't retroactively block people who were already using the bot before
    the separate Approve step existed.
    """
    if settings_collection is None:
        return False
    try:
        doc = settings_collection.find_one({"user_id": thread_id})
        if not doc:
            return False
        if doc.get("terms_accepted") is True:
            return True
        if doc.get("journey"):
            return True
        return False
    except Exception as e:
        logger.error(f"Error checking terms acceptance: {e}")
        return False


def set_terms_accepted(thread_id: str) -> bool:
    """Record that the user tapped Approve on the Terms of Use / Privacy Policy message."""
    if settings_collection is None:
        return False
    try:
        settings_collection.update_one(
            {"user_id": thread_id},
            {"$set": {"terms_accepted": True, "terms_accepted_at": datetime.utcnow()}},
            upsert=True
        )
        logger.info(f"✅ User {thread_id} accepted Terms of Use / Privacy Policy")
        return True
    except Exception as e:
        logger.error(f"Failed to set terms acceptance: {e}")
        return False


def set_user_journey(thread_id: str, journey: str, baby_age_months: int = None) -> bool:
    """
    Save user's journey to MongoDB.
    journey: 'pregnant' | 'breastfeeding' | 'caregiver'
    baby_age_months: optional, for caregiver to personalise tips
    """
    if settings_collection is None:
        return False
    try:
        update = {"journey": journey, "updated_at": datetime.utcnow()}
        if baby_age_months is not None:
            update["baby_age_months"] = baby_age_months
        settings_collection.update_one(
            {"user_id": thread_id},
            {"$set": update},
            upsert=True
        )
        logger.info(f"✅ User {thread_id} journey set to {journey}")
        return True
    except Exception as e:
        logger.error(f"Failed to set user journey: {e}")
        return False


def get_user_journey(thread_id: str) -> dict:
    """
    Get user's saved journey profile.
    Returns dict with 'journey' and optionally 'baby_age_months', or empty dict.
    """
    if settings_collection is None:
        return {}
    try:
        doc = settings_collection.find_one({"user_id": thread_id})
        if doc and "journey" in doc:
            return {
                "journey": doc["journey"],
                "baby_age_months": doc.get("baby_age_months"),
            }
    except Exception as e:
        logger.error(f"Error getting user journey: {e}")
    return {}


def get_all_users_with_journey() -> list:
    """Return all users that have a saved journey — used for daily tips."""
    if settings_collection is None:
        return []
    try:
        docs = list(settings_collection.find(
            {"journey": {"$exists": True}},
            {"user_id": 1, "journey": 1, "baby_age_months": 1, "language": 1, "_id": 0}
        ))
        return docs
    except Exception as e:
        logger.error(f"Error fetching users with journey: {e}")
        return []


def get_all_users_for_daily_tips() -> list:
    """Return users eligible for the daily nutrition tip. All three conditions
    must hold: they have a saved journey, they have ACCEPTED THE TERMS, and they
    have not tapped the Stop button.

    All three are applied here in the Mongo query (rather than filtered in the
    caller) so every code path that sends daily tips inherits them and it is
    impossible to accidentally message someone who opted out or never accepted
    the terms. `tips_opted_out` is only ever set to True — users who never opted
    out simply don't have the field, hence `$ne: True` rather than matching
    `$exists`/`False`.

    The terms condition mirrors has_accepted_terms() exactly, INCLUDING its
    grandfather clause: a user with a saved `journey` went through the old
    combined onboarding message, which already carried the terms/privacy links,
    so they count as having accepted. Written out explicitly here rather than
    relying on "journey exists implies accepted" so that tightening
    has_accepted_terms() later doesn't silently start sending tips to people who
    never accepted.
    """
    if settings_collection is None:
        return []
    try:
        docs = list(settings_collection.find(
            {
                "journey": {"$exists": True, "$nin": [None, ""]},
                "tips_opted_out": {"$ne": True},
                "$or": [
                    {"terms_accepted": True},
                    {"journey": {"$exists": True, "$nin": [None, ""]}},  # grandfathered
                ],
            },
            {"user_id": 1, "journey": 1, "baby_age_months": 1, "language": 1, "_id": 0}
        ))
        return docs
    except Exception as e:
        logger.error(f"Error fetching users for daily tips: {e}")
        return []


def get_all_users_for_weight_reminders() -> list:
    """Return users eligible for the weight-tracking reminder: breastfeeding
    mothers and caregivers only (pregnant women have no baby to weigh yet), who
    have accepted the terms.

    Same terms rule as get_all_users_for_daily_tips(). Note this deliberately
    does NOT honour `tips_opted_out` — that flag is set by the Stop button on
    the daily NUTRITION TIP, whose confirmation message tells the user they'll
    stop receiving daily tips. Weight reminders are a separate message stream;
    silently folding them into that opt-out would stop a message the user never
    asked to stop. If a separate reminder opt-out is wanted, add a distinct flag.
    """
    if settings_collection is None:
        return []
    try:
        docs = list(settings_collection.find(
            {
                "journey": {"$in": ["breastfeeding", "caregiver"]},
                "$or": [
                    {"terms_accepted": True},
                    {"journey": {"$exists": True, "$nin": [None, ""]}},  # grandfathered
                ],
            },
            {"user_id": 1, "journey": 1, "language": 1, "_id": 0}
        ))
        return docs
    except Exception as e:
        logger.error(f"Error fetching users for weight reminders: {e}")
        return []


def has_opted_out_of_tips(thread_id: str) -> bool:
    """Return True if this user tapped Stop and should no longer get daily tips."""
    if settings_collection is None:
        return False
    try:
        doc = settings_collection.find_one({"user_id": thread_id}, {"tips_opted_out": 1})
        return bool(doc and doc.get("tips_opted_out") is True)
    except Exception as e:
        logger.error(f"Error checking tips opt-out for {thread_id}: {e}")
        return False


def set_tips_opted_out(thread_id: str, opted_out: bool = True) -> bool:
    """Record that the user tapped Stop (or re-subscribed) for daily nutrition tips."""
    if settings_collection is None:
        return False
    try:
        settings_collection.update_one(
            {"user_id": thread_id},
            {"$set": {
                "tips_opted_out": opted_out,
                "tips_opted_out_at": datetime.utcnow() if opted_out else None,
            }},
            upsert=True
        )
        logger.info(
            f"{'🛑' if opted_out else '✅'} User {thread_id} "
            f"{'opted OUT of' if opted_out else 're-subscribed to'} daily nutrition tips"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to set tips opt-out for {thread_id}: {e}")
        return False


def get_job_last_run_at(job_id: str) -> Optional[datetime]:
    """When did the named background job last actually start a send cycle?

    Persisted in MongoDB (instead of only living in the asyncio task's memory)
    so that a container restart — which happens automatically on any crash,
    since docker-compose runs this service with `restart: unless-stopped` —
    doesn't reset the interval countdown back to zero. Without this, a service
    that restarts even once per interval can end up never actually reaching the
    interval mark, which is why scheduled sends would go out for a while and
    then silently stop.

    job_id is the scheduler_state document _id, e.g. "daily_tips" or
    "weight_reminders" — each job tracks its own last-run timestamp.
    """
    if scheduler_collection is None:
        return None
    try:
        doc = scheduler_collection.find_one({"_id": job_id})
        return doc.get("last_run_at") if doc else None
    except Exception as e:
        logger.error(f"Error reading scheduler state for {job_id}: {e}")
        return None


def set_job_last_run_at(job_id: str, when: Optional[datetime] = None) -> None:
    """Record that the named job's send cycle just started."""
    if scheduler_collection is None:
        return
    try:
        scheduler_collection.update_one(
            {"_id": job_id},
            {"$set": {"last_run_at": when or datetime.utcnow()}},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving scheduler state for {job_id}: {e}")


def get_tip_cycle(thread_id: str) -> dict:
    """Return this user's current tip cycle: which tips they have already been
    sent in the current pass through their tip list.

    Shape: {"key": "<language>|<bucket>", "seen": [int, ...], "last_index": int}

    "seen" is every index already sent in the CURRENT cycle. The picker only
    chooses from indices not in "seen", so a user works through their whole
    list before any tip can repeat. When the list is exhausted the cycle resets
    and starts again.

    Persisted per-user so the cycle survives container restarts — an in-memory
    version would reset on every restart and reintroduce repeats.

    Migration: users from the older 1-deep scheme have `last_tip_index` but no
    `tip_cycle`. We seed a fresh cycle from that value so their next tip still
    won't repeat the one they just got.
    """
    if settings_collection is None:
        return {}
    try:
        doc = settings_collection.find_one(
            {"user_id": thread_id},
            {"tip_cycle": 1, "last_tip_index": 1}
        )
        if not doc:
            return {}
        cycle = doc.get("tip_cycle") or {}
        if not cycle and doc.get("last_tip_index") is not None:
            return {"key": None, "seen": [], "last_index": doc["last_tip_index"]}
        return cycle
    except Exception as e:
        logger.error(f"Error getting tip cycle for {thread_id}: {e}")
        return {}


def record_tip_sent(
    thread_id: str,
    tip_index: int,
    journey: str,
    language: str,
    tip_cycle: Optional[dict] = None,
) -> None:
    """Persist that a daily tip was sent: updates the user's own record (the tip
    cycle, used to guarantee no repeats until the list is exhausted) AND appends
    to tip_send_log (a durable audit trail — query this collection to see
    exactly when a number last got a tip, instead of relying on container logs
    which scroll away/reset on every restart)."""
    now = datetime.utcnow()
    if settings_collection is not None:
        try:
            update = {
                # kept for backwards-compat and quick eyeballing in the DB;
                # tip_cycle is the field the picker actually reads.
                "last_tip_index": tip_index,
                "last_tip_sent_at": now,
                "last_tip_journey": journey,
                "last_tip_language": language,
            }
            if tip_cycle is not None:
                update["tip_cycle"] = tip_cycle
            settings_collection.update_one(
                {"user_id": thread_id},
                {"$set": update},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error recording last tip for {thread_id}: {e}")
    if tip_send_log_collection is not None:
        try:
            tip_send_log_collection.insert_one({
                "user_id": thread_id,
                "tip_index": tip_index,
                "journey": journey,
                "language": language,
                "sent_at": now,
            })
        except Exception as e:
            logger.error(f"Error writing tip_send_log for {thread_id}: {e}")


def set_user_language(thread_id: str, language: str) -> bool:
    """Save user's preferred language to MongoDB"""
    if settings_collection is None:
        return False
    try:
        settings_collection.update_one(
            {"user_id": thread_id},
            {"$set": {"language": language, "updated_at": datetime.utcnow()}},
            upsert=True
        )
        logger.info(f"✅ User {thread_id} language set to {language}")
        return True
    except Exception as e:
        logger.error(f"Failed to set user language: {e}")
        return False