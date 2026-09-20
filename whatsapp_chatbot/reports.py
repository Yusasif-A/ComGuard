"""
Report storage and multi-source corroboration.

A report moves through a small state machine:

    draft        a photo, a voice note or a location has arrived, but the
                 person has not finished telling us what happened
    submitted    complete enough to act on; forwarded to the authority
    corroborated CORROBORATION_THRESHOLD independent reporters have described
                 the same kind of thing, close together, recently
    verified     a dispatcher confirmed it directly
    dismissed    a dispatcher rejected it

Only `corroborated` and `verified` reports can trigger a community broadcast.
That rule is the whole point of this module: one person with a phone can raise a
report, but one person with a phone cannot make the system shout at a
neighbourhood. Rumour dies here.

Independence is counted by distinct reporter pseudonyms, never by report count —
otherwise anyone could clear the threshold alone by sending two photos.
"""

import logging
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.server_api import ServerApi

from anonymiser import reporter_pseudonym, scrub_text
from config import (
    CORROBORATION_RADIUS_KM,
    CORROBORATION_THRESHOLD,
    CORROBORATION_WINDOW_MINUTES,
)

logger = logging.getLogger(__name__)

DB_NAME = os.getenv("MONGO_DB_NAME", "comguard")

STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_CORROBORATED = "corroborated"
STATUS_VERIFIED = "verified"
STATUS_DISMISSED = "dismissed"

ALERTABLE_STATUSES = (STATUS_CORROBORATED, STATUS_VERIFIED)

_reports = None
_alerts = None
_connected = False


def _connect():
    """Open the report collections once and cache the handles.

    Returns (reports, alerts), either of which may be None when MONGO_URI is
    unset. Every function below tolerates that and degrades to a no-op, so the
    bot still answers people when the database is down — it just cannot file or
    corroborate while that lasts.
    """
    global _reports, _alerts, _connected
    if _connected:
        return _reports, _alerts

    _connected = True
    uri = os.getenv("MONGO_URI")
    if not uri:
        logger.warning("⚠️ MONGO_URI not set — reports will not be stored")
        return None, None

    try:
        client = MongoClient(uri, server_api=ServerApi("1"))
        client.admin.command("ping")
        db = client.get_database(DB_NAME)
        _reports = db.get_collection("reports")
        _alerts = db.get_collection("alerts")

        # Corroboration always queries "recent reports of category X", so that
        # pair carries the index. report_id is unique because it is handed out
        # to the reporter and to the authority as the one shared reference.
        _reports.create_index([("created_at", DESCENDING)])
        _reports.create_index([("category", ASCENDING), ("created_at", DESCENDING)])
        _reports.create_index([("report_id", ASCENDING)], unique=True)
        _reports.create_index([("reporter_ref", ASCENDING), ("created_at", DESCENDING)])
        _alerts.create_index([("sent_at", DESCENDING)])

        logger.info(f"✅ Connected to MongoDB for reports (db: {DB_NAME})")
        return _reports, _alerts
    except Exception as e:
        logger.error(f"❌ Report DB connection failed: {e}")
        return None, None


# ── Geography ────────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km.

    Done in Python rather than with a Mongo 2dsphere query on purpose: the
    candidate set is already bounded to one category within a few hours, so it
    is small, and this keeps the service running against any MongoDB without
    needing a geospatial index to have been created first.
    """
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


# Categories that describe the same underlying event closely enough to
# corroborate one another. Somebody reporting "the road is gone" and somebody
# reporting "we cannot get through" at the same spot are two witnesses to one
# incident, and requiring them to pick identical words would mean neither counts.
_EQUIVALENT_CATEGORIES = {
    "flood": {"flood", "blocked_route", "road_damage"},
    "road_damage": {"road_damage", "blocked_route", "structural_collapse", "flood"},
    "blocked_route": {"blocked_route", "road_damage", "flood"},
    "structural_collapse": {"structural_collapse", "road_damage"},
    "fire": {"fire", "structural_collapse"},
    "unrest": {"unrest", "blocked_route"},
    "illegal_levy": {"illegal_levy", "unlawful_checkpoint", "forged_receipt", "fake_notice"},
    "unlawful_checkpoint": {"unlawful_checkpoint", "illegal_levy"},
    "fake_notice": {"fake_notice", "forged_receipt", "illegal_levy"},
    "forged_receipt": {"forged_receipt", "fake_notice", "illegal_levy"},
}


def related_categories(category: str) -> set:
    return _EQUIVALENT_CATEGORIES.get(category, {category})


# ── Writing ──────────────────────────────────────────────────────────────────

def new_report_id() -> str:
    """Short, unambiguous reference the reporter can quote, e.g. "CG-7K3M9Q"."""
    alphabet = "ACDEFGHJKMNPQRTUVWXY3456789"  # no 0/O/1/I/L/S/B — read aloud safely
    return "CG-" + "".join(secrets.choice(alphabet) for _ in range(6))


def open_draft(phone_number: str, language: str = "english") -> dict:
    """Start (or reuse) the open draft for this reporter.

    A person sends a photo, then a voice note, then a location, as three separate
    WhatsApp messages. They are one report, so the first piece opens a draft and
    the rest attach to it. Reusing an existing draft rather than opening a new
    one per message is what stops a single incident becoming three reports — and
    three reports from one person must never look like corroboration.
    """
    reports, _ = _connect()
    ref = reporter_pseudonym(phone_number)

    if reports is not None:
        existing = reports.find_one(
            {"reporter_ref": ref, "status": STATUS_DRAFT},
            sort=[("created_at", DESCENDING)],
        )
        # A draft older than the corroboration window is a forgotten one; start
        # fresh rather than bolting today's photo onto last week's report.
        if existing:
            age = datetime.now(timezone.utc) - _as_utc(existing["created_at"])
            if age < timedelta(minutes=CORROBORATION_WINDOW_MINUTES):
                return existing

    draft = {
        "report_id": new_report_id(),
        "reporter_ref": ref,
        "status": STATUS_DRAFT,
        "language": language,
        "category": None,
        "severity": None,
        "summary": "",
        "next_steps": "",
        "ocr_text": "",
        "image_authenticity": None,
        "has_image": False,
        "has_audio": False,
        "location": None,
        "location_name": "",
        "corroboration_count": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "forwarded_at": None,
        "alerted_at": None,
    }

    if reports is not None:
        try:
            reports.insert_one(dict(draft))
        except Exception as e:
            logger.error(f"❌ Could not open draft report: {e}")

    logger.info(f"📝 Draft report {draft['report_id']} opened for {ref}")
    return draft


def update_report(report_id: str, fields: dict) -> bool:
    """Patch a report. Free-text fields are scrubbed on the way in.

    Scrubbing here rather than at the point of forwarding means the database
    never holds the identifier either — a leak of the store is not a leak of
    who reported what.
    """
    reports, _ = _connect()
    if reports is None:
        return False

    patch = dict(fields)
    for key in ("summary", "next_steps", "ocr_text", "location_name"):
        if key in patch and isinstance(patch[key], str):
            patch[key] = scrub_text(patch[key])
    patch["updated_at"] = datetime.now(timezone.utc)

    try:
        result = reports.update_one({"report_id": report_id}, {"$set": patch})
        return result.matched_count > 0
    except Exception as e:
        logger.error(f"❌ Could not update report {report_id}: {e}")
        return False


def get_report(report_id: str) -> Optional[dict]:
    reports, _ = _connect()
    if reports is None:
        return None
    try:
        return reports.find_one({"report_id": report_id}, {"_id": 0})
    except Exception as e:
        logger.error(f"❌ Could not read report {report_id}: {e}")
        return None


def submit_report(report_id: str) -> Optional[dict]:
    """Move a draft to `submitted` and return the stored report."""
    update_report(report_id, {"status": STATUS_SUBMITTED, "submitted_at": datetime.now(timezone.utc)})
    report = get_report(report_id)
    if report:
        logger.info(
            f"📨 Report {report_id} submitted — {report.get('category')} "
            f"({report.get('severity')})"
        )
    return report


# ── Corroboration ────────────────────────────────────────────────────────────

def _as_utc(value) -> datetime:
    """Mongo hands back naive datetimes; treat them as the UTC they were written as."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def find_corroborating(report: dict) -> List[dict]:
    """Reports from OTHER people describing the same thing, nearby and recently.

    Three filters, all of which must pass:
      - a related category (see _EQUIVALENT_CATEGORIES)
      - inside CORROBORATION_RADIUS_KM of this report
      - within CORROBORATION_WINDOW_MINUTES

    A report with no location cannot corroborate and cannot be corroborated: we
    have no way to tell whether it describes the same incident or one 200km away.
    That is a deliberate gap, not an oversight — guessing here is how a system
    starts broadcasting alerts about the wrong neighbourhood.
    """
    reports, _ = _connect()
    location = report.get("location") or {}
    lat, lon = location.get("lat"), location.get("lon")

    if reports is None or lat is None or lon is None:
        return []

    since = datetime.now(timezone.utc) - timedelta(minutes=CORROBORATION_WINDOW_MINUTES)

    try:
        candidates = list(reports.find(
            {
                "report_id": {"$ne": report.get("report_id")},
                "reporter_ref": {"$ne": report.get("reporter_ref")},  # independence
                "category": {"$in": list(related_categories(report.get("category")))},
                "status": {"$in": [STATUS_SUBMITTED, STATUS_CORROBORATED, STATUS_VERIFIED]},
                "created_at": {"$gte": since},
                "location": {"$ne": None},
            },
            {"_id": 0},
        ))
    except Exception as e:
        logger.error(f"❌ Corroboration query failed: {e}")
        return []

    nearby = []
    for candidate in candidates:
        cloc = candidate.get("location") or {}
        if cloc.get("lat") is None or cloc.get("lon") is None:
            continue
        distance = haversine_km(lat, lon, cloc["lat"], cloc["lon"])
        if distance <= CORROBORATION_RADIUS_KM:
            candidate["_distance_km"] = round(distance, 2)
            nearby.append(candidate)

    return nearby


def evaluate_corroboration(report: dict) -> dict:
    """Decide whether this report now clears the bar for a community alert.

    Returns the decision and the evidence behind it, so the dispatcher dashboard
    can show WHY an alert fired rather than just that it did.
    """
    matches = find_corroborating(report)

    # Independent WITNESSES, not independent reports — this report's own reporter
    # plus everyone else who described the same thing.
    witnesses = {report.get("reporter_ref")} | {m.get("reporter_ref") for m in matches}
    witnesses.discard(None)
    count = len(witnesses)

    verified_nearby = any(m.get("status") == STATUS_VERIFIED for m in matches)
    threshold_met = count >= CORROBORATION_THRESHOLD

    decision = {
        "independent_reporters": count,
        "threshold": CORROBORATION_THRESHOLD,
        "matching_reports": [m.get("report_id") for m in matches],
        "radius_km": CORROBORATION_RADIUS_KM,
        "window_minutes": CORROBORATION_WINDOW_MINUTES,
        "alertable": bool(threshold_met or verified_nearby),
        "reason": (
            "dispatcher-verified report nearby" if verified_nearby
            else f"{count} independent reporters (need {CORROBORATION_THRESHOLD})"
        ),
    }

    if decision["alertable"] and report.get("status") == STATUS_SUBMITTED:
        update_report(report["report_id"], {
            "status": STATUS_CORROBORATED,
            "corroboration_count": count,
            "corroborated_with": decision["matching_reports"],
        })
        # Everyone in the cluster is corroborated by this arrival, not just the
        # newcomer — otherwise the dashboard shows one confirmed report beside
        # several that look unconfirmed but are the same incident.
        for match in matches:
            if match.get("status") == STATUS_SUBMITTED:
                update_report(match["report_id"], {
                    "status": STATUS_CORROBORATED,
                    "corroboration_count": count,
                })

    logger.info(
        "🔗 Corroboration for %s: %s (%s)",
        report.get("report_id"), decision["alertable"], decision["reason"],
    )
    return decision


def mark_verified(report_id: str, dispatcher: str = "") -> bool:
    """A dispatcher confirmed this directly — the other route to alertable."""
    return update_report(report_id, {
        "status": STATUS_VERIFIED,
        "verified_by": dispatcher,
        "verified_at": datetime.now(timezone.utc),
    })


def mark_dismissed(report_id: str, dispatcher: str = "", note: str = "") -> bool:
    return update_report(report_id, {
        "status": STATUS_DISMISSED,
        "dismissed_by": dispatcher,
        "dismissed_note": note[:500],
        "dismissed_at": datetime.now(timezone.utc),
    })


def mark_forwarded(report_id: str, destination: str, ok: bool,
                   with_location: bool = False) -> None:
    """Record a dispatch attempt.

    `with_location` is what stops a report being re-sent forever: it says the
    agency now holds the coordinates, so later messages on the same report do
    not trigger another update.
    """
    update_report(report_id, {
        "forwarded_at": datetime.now(timezone.utc),
        "forwarded_to": destination,
        "forward_ok": ok,
        "forwarded_with_location": with_location,
    })


# ── Reading (dispatcher dashboard) ───────────────────────────────────────────

def recent_reports(limit: int = 200, status: str = "", category: str = "") -> List[dict]:
    """Reports newest-first for the dashboard. Already anonymised in storage."""
    reports, _ = _connect()
    if reports is None:
        return []

    query = {}
    if status:
        query["status"] = status
    if category:
        query["category"] = category

    try:
        return list(reports.find(query, {"_id": 0})
                    .sort("created_at", DESCENDING)
                    .limit(max(1, min(limit, 2000))))
    except Exception as e:
        logger.error(f"❌ Could not list reports: {e}")
        return []


def report_stats() -> dict:
    """Headline counts for the dashboard."""
    reports, _ = _connect()
    if reports is None:
        return {}

    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        by_status = {
            row["_id"]: row["count"]
            for row in reports.aggregate([
                {"$group": {"_id": "$status", "count": {"$sum": 1}}}
            ])
        }
        by_category = {
            row["_id"] or "unknown": row["count"]
            for row in reports.aggregate([
                {"$match": {"status": {"$ne": STATUS_DRAFT}}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ])
        }
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_category": by_category,
            "last_24h": reports.count_documents({
                "created_at": {"$gte": since_24h},
                "status": {"$ne": STATUS_DRAFT},
            }),
            "awaiting_review": by_status.get(STATUS_SUBMITTED, 0),
        }
    except Exception as e:
        logger.error(f"❌ Could not compute report stats: {e}")
        return {}


# ── Alert bookkeeping ────────────────────────────────────────────────────────

def record_alert(report: dict, recipients: int, cluster: List[str]) -> None:
    _, alerts = _connect()
    if alerts is None:
        return
    try:
        alerts.insert_one({
            "report_id": report.get("report_id"),
            "category": report.get("category"),
            "severity": report.get("severity"),
            "location": report.get("location"),
            "cluster": cluster,
            "recipients": recipients,
            "sent_at": datetime.now(timezone.utc),
        })
        update_report(report["report_id"], {"alerted_at": datetime.now(timezone.utc)})
    except Exception as e:
        logger.error(f"❌ Could not record alert: {e}")


def recent_alert_nearby(report: dict, cooldown_minutes: int) -> Optional[dict]:
    """The last alert for this kind of incident in this area, if still in cooldown.

    Without this, every new report about one flood sends the whole neighbourhood
    another identical warning — which is how a safety channel trains people to
    ignore it.
    """
    _, alerts = _connect()
    location = report.get("location") or {}
    lat, lon = location.get("lat"), location.get("lon")
    if alerts is None or lat is None or lon is None:
        return None

    since = datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)
    try:
        recent = list(alerts.find(
            {
                "category": {"$in": list(related_categories(report.get("category")))},
                "sent_at": {"$gte": since},
            },
            {"_id": 0},
        ))
    except Exception as e:
        logger.error(f"❌ Alert cooldown query failed: {e}")
        return None

    for alert in recent:
        aloc = alert.get("location") or {}
        if aloc.get("lat") is None:
            continue
        if haversine_km(lat, lon, aloc["lat"], aloc["lon"]) <= CORROBORATION_RADIUS_KM:
            return alert
    return None


def recent_alerts(limit: int = 50) -> List[dict]:
    _, alerts = _connect()
    if alerts is None:
        return []
    try:
        return list(alerts.find({}, {"_id": 0}).sort("sent_at", DESCENDING).limit(limit))
    except Exception as e:
        logger.error(f"❌ Could not list alerts: {e}")
        return []
