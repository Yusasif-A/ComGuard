"""
Website feedback (the /feedback page on chopbeta.netlify.app).

Stores submissions in MongoDB next to the WhatsApp conversation data rather
than in a third-party form service, and renders a small password-protected
dashboard so the team can read them without opening a DB client.

Collection: Helpmum-Hackthon.site_feedback
"""

import os
import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient, DESCENDING
from pymongo.server_api import ServerApi
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Login for the dashboard data API and CSV export.
#
# FEEDBACK_DASHBOARD_PASSWORD accepts a comma-separated list, so more than one
# password can be valid at a time. That also makes rotation painless: add the
# new one, let people move over, then drop the old one.
#
# These must be passwords created FOR THIS DASHBOARD ONLY. Never put the real
# password of the email account here — this value sits in plaintext in a .env
# file on the server, so reusing an account password would turn one leaked
# file into a compromised email account.
#
# If no password is set the dashboard refuses to serve rather than exposing
# submissions (which include phone numbers) to the internet.
DASHBOARD_USER = os.getenv("FEEDBACK_DASHBOARD_USER", "aichopbeta@gmail.com")

DASHBOARD_PASSWORDS = [
    p.strip() for p in os.getenv("FEEDBACK_DASHBOARD_PASSWORD", "").split(",") if p.strip()
]

# Fields accepted from the public form. Anything else in the payload is dropped,
# so a malicious poster cannot inject arbitrary keys into our documents.
ALLOWED_FIELDS = (
    "name", "phone", "role", "rating",
    "recognised", "wrongFood", "helpful", "improve", "recommend",
)

MAX_FIELD_LENGTH = 5000  # generous for the free-text answers, bounded for safety

_collection = None


def _get_collection():
    """Cached collection handle — see baby_tracker._get_collection for why we
    reuse one client rather than opening a new one per call."""
    global _collection
    if _collection is not None:
        return _collection

    uri = os.getenv("MONGO_URI")
    if not uri:
        logger.warning("MONGO_URI not set — site feedback will not be saved")
        return None
    try:
        client = MongoClient(uri, server_api=ServerApi("1"))
        db = client.get_database("Helpmum-Hackthon")
        col = db.get_collection("site_feedback")
        col.create_index([("submitted_at", DESCENDING)])
        _collection = col
        return col
    except Exception as e:
        logger.error(f"❌ site_feedback DB connection failed: {e}")
        return None


def save_site_feedback(payload: dict, source_ip: str = "") -> bool:
    """Validate and store one submission. Returns True if it was written."""
    col = _get_collection()
    if col is None:
        return False

    doc = {}
    for field in ALLOWED_FIELDS:
        value = payload.get(field, "")
        if value is None:
            value = ""
        doc[field] = str(value).strip()[:MAX_FIELD_LENGTH]

    # The form marks these required; enforce server-side too, since anyone can
    # POST here directly and skip the browser validation.
    if not doc.get("role") or not doc.get("rating") or not doc.get("recommend"):
        logger.warning("⚠️ Rejected site feedback — missing required fields")
        return False

    doc["submitted_at"] = datetime.now(timezone.utc)
    doc["source_ip"] = source_ip

    try:
        col.insert_one(doc)
        logger.info(
            f"✅ Site feedback saved — {doc.get('role')}, rating {doc.get('rating')}, "
            f"food recognised: {doc.get('recognised') or 'n/a'}"
        )
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save site feedback: {e}")
        return False


def get_site_feedback(limit: int = 500) -> list:
    """Return the most recent submissions, newest first."""
    col = _get_collection()
    if col is None:
        return []
    try:
        return list(col.find({}, {"_id": 0}, sort=[("submitted_at", DESCENDING)], limit=limit))
    except Exception as e:
        logger.error(f"❌ Failed to read site feedback: {e}")
        return []


def get_feedback_stats(rows: list) -> dict:
    """Headline numbers for the dashboard, computed from the rows we already
    fetched so the dashboard makes a single DB round trip."""
    ratings = [int(r["rating"]) for r in rows if str(r.get("rating", "")).isdigit()]
    tried = [r for r in rows if r.get("recognised") in ("Yes", "No")]
    recognised_yes = [r for r in tried if r.get("recognised") == "Yes"]
    would_recommend = [r for r in rows if r.get("recommend") in ("Yes", "Maybe")]

    return {
        "total": len(rows),
        "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "recognition_rate": (
            round(100 * len(recognised_yes) / len(tried)) if tried else None
        ),
        "recommend_rate": (
            round(100 * len(would_recommend) / len(rows)) if rows else None
        ),
    }


def rows_to_csv(rows: list) -> str:
    """Flatten submissions to CSV for download / opening in Excel or Sheets."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Submitted (UTC)", "Name", "Phone", "Role", "Rating",
        "Food recognised", "Food it got wrong", "Most helpful",
        "To improve", "Would recommend",
    ])
    for r in rows:
        submitted = r.get("submitted_at")
        writer.writerow([
            submitted.strftime("%Y-%m-%d %H:%M") if isinstance(submitted, datetime) else "",
            r.get("name", ""), r.get("phone", ""), r.get("role", ""), r.get("rating", ""),
            r.get("recognised", ""), r.get("wrongFood", ""), r.get("helpful", ""),
            r.get("improve", ""), r.get("recommend", ""),
        ])
    return buf.getvalue()


def rows_to_json(rows: list) -> list:
    """Make rows JSON-serialisable for the dashboard front-end.

    The dashboard is a page in the React site that fetches this data, so the
    API returns plain data and the browser renders it. The front-end inserts
    every value with textContent (never innerHTML), so attacker-controlled
    submission text cannot execute as markup there.
    """
    out = []
    for r in rows:
        item = {k: r.get(k, "") for k in ALLOWED_FIELDS}
        submitted = r.get("submitted_at")
        item["submitted_at"] = (
            submitted.replace(tzinfo=timezone.utc).isoformat()
            if isinstance(submitted, datetime) and submitted.tzinfo is None
            else submitted.isoformat() if isinstance(submitted, datetime) else ""
        )
        out.append(item)
    return out
