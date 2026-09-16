"""
Outbound routing: anonymised reports to authorities, safety alerts to neighbours.

Two very different acts, deliberately in one place because both are moments when
information leaves ComGuard and can no longer be recalled.

Forwarding to an authority is cheap and reversible-ish — a dispatcher reads a
summary. Broadcasting to a neighbourhood is neither. A false alert costs the
service the only thing that makes it useful, which is people believing it. So
broadcasts pass three gates before a single message is sent:

  1. status is corroborated or verified   (reports.evaluate_corroboration)
  2. no similar alert nearby recently      (cooldown)
  3. severity is high or critical          (a low-severity nuisance is not worth
                                            waking a neighbourhood for)

`send_message` is injected rather than imported from app.py, which keeps this
module free of the WhatsApp transport and lets the gating logic be exercised
without sending anything.
"""

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

import httpx

from anonymiser import anonymise_report_payload
from config import (
    ALERT_BROADCAST_RADIUS_KM,
    ALERT_COOLDOWN_MINUTES,
    AUTHORITY_WEBHOOK_TOKEN,
    EMERGENCY_NUMBERS,
    authority_webhook_for,
)
from reports import (
    ALERTABLE_STATUSES,
    haversine_km,
    mark_forwarded,
    record_alert,
    recent_alert_nearby,
)
from store import get_alert_subscribers

logger = logging.getLogger(__name__)

# Only these ever justify a community-wide broadcast.
BROADCAST_SEVERITIES = ("high", "critical")


# ── Authority dispatch ───────────────────────────────────────────────────────

async def forward_to_authority(report: dict) -> bool:
    """POST the anonymised report to the agency for its category.

    Returns False when no endpoint is configured — that is a normal state during
    development and is logged, not raised. The report is still stored and still
    visible on the dispatcher dashboard, so nothing is lost; it just is not
    pushed anywhere.
    """
    category = report.get("category") or "other"
    destination = authority_webhook_for(category)

    if not destination:
        logger.info(
            f"📭 No authority endpoint for '{category}' — report "
            f"{report.get('report_id')} stored for dashboard review only"
        )
        return False

    payload = anonymise_report_payload(report)
    headers = {"Content-Type": "application/json"}
    if AUTHORITY_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {AUTHORITY_WEBHOOK_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(destination, json=payload, headers=headers)
        ok = 200 <= response.status_code < 300
        if ok:
            logger.info(f"📨 Report {payload['report_id']} forwarded to {category} authority")
        else:
            logger.error(
                f"❌ Authority rejected {payload['report_id']}: "
                f"{response.status_code} {response.text[:200]}"
            )
        mark_forwarded(report["report_id"], destination, ok)
        return ok
    except Exception as e:
        logger.error(f"❌ Could not forward {payload['report_id']}: {e}")
        mark_forwarded(report["report_id"], destination, False)
        return False


# ── Community broadcast ──────────────────────────────────────────────────────

_ALERT_HEADLINES = {
    "flood": "Flooding reported in your area",
    "fire": "Fire reported in your area",
    "road_damage": "Damaged road or bridge in your area",
    "blocked_route": "A route near you is blocked",
    "structural_collapse": "A building collapse is reported near you",
    "unrest": "Avoid this area for now",
    "illegal_levy": "People are being asked for money that is not official",
    "unlawful_checkpoint": "An unofficial checkpoint has been reported nearby",
    "fake_notice": "A fake official notice is circulating in your area",
    "forged_receipt": "Forged receipts are being used in your area",
    "other": "Safety notice for your area",
}

_ALERT_ADVICE = {
    "flood": "Move to higher ground. Do not walk or drive through moving water — it is deeper and faster than it looks.",
    "fire": "Move away from the area. Do not go back for belongings.",
    "road_damage": "Do not use this road. Find another route and tell anyone travelling that way.",
    "blocked_route": "Plan another route. Do not try to pass through.",
    "structural_collapse": "Stay well away from the building and do not enter it for any reason.",
    "unrest": "Stay indoors if you are nearby, and avoid the area until it is calm.",
    "illegal_levy": "No official collector needs cash on the spot. Ask for identification and an official receipt, and do not hand over money you cannot get back. Do not argue or resist if you feel unsafe — pay if you must, keep whatever paper you are given, and report it here.",
    "unlawful_checkpoint": "Stay calm and do not argue. Do not film anyone. Report it here once you are away and safe.",
    "fake_notice": "Do not pay anything or call numbers written on a notice you were handed. Check with the agency directly using a number you already have.",
    "forged_receipt": "Keep any receipt you were given and report it here. Do not pay again on the strength of it.",
}


def build_alert_message(report: dict, language_label: str = "") -> str:
    """The English broadcast text. Translated per recipient before sending.

    Written to stand alone: somebody receiving this has not been in a
    conversation and has no context, so it says what, roughly where, what to do,
    and — importantly — that it is based on reports rather than confirmed fact.
    """
    category = report.get("category") or "other"
    headline = _ALERT_HEADLINES.get(category, _ALERT_HEADLINES["other"])
    advice = _ALERT_ADVICE.get(category, "Take care in this area and avoid unnecessary travel.")

    place = report.get("location_name") or "your area"
    count = report.get("corroboration_count", 2)

    basis = (
        "This was confirmed by an official."
        if report.get("status") == "verified"
        else f"{count} separate people nearby have reported this."
    )

    lines = [
        f"⚠️ *{headline}*",
        "",
        f"Reported near {place}. {basis}",
        "",
        advice,
    ]

    if report.get("severity") == "critical":
        lines += ["", f"If anyone is in danger, call {EMERGENCY_NUMBERS}."]

    lines += [
        "",
        "_From ComGuard. Reply STOP to stop receiving alerts for your area._",
    ]
    return "\n".join(lines)


def find_recipients(report: dict, exclude_ref: str = "") -> List[dict]:
    """Opted-in subscribers within the broadcast radius of the incident.

    The reporters themselves are not excluded — they already know, but they also
    benefit from seeing that their report was corroborated and acted on, and
    filtering them out would require matching a pseudonym back to a phone
    number, which is exactly the link this system is built not to have.
    """
    location = report.get("location") or {}
    lat, lon = location.get("lat"), location.get("lon")
    if lat is None or lon is None:
        return []

    recipients = []
    for subscriber in get_alert_subscribers():
        home = subscriber.get("alert_location") or {}
        if home.get("lat") is None or home.get("lon") is None:
            continue
        if haversine_km(lat, lon, home["lat"], home["lon"]) <= ALERT_BROADCAST_RADIUS_KM:
            recipients.append(subscriber)

    return recipients


async def maybe_broadcast(
    report: dict,
    send_message: Callable[[str, str], Awaitable[bool]],
    translate: Optional[Callable[[str, str], str]] = None,
) -> dict:
    """Run the three gates and, if all pass, send the alert.

    Args:
        report: the stored report, after evaluate_corroboration has run
        send_message: async (phone, text) -> bool, injected by app.py
        translate: sync (english_text, language_key) -> text, optional

    Returns a dict describing what happened and why, which is logged and shown
    on the dashboard. "Why was no alert sent?" is a question this service will be
    asked, and it should be able to answer it.
    """
    report_id = report.get("report_id")

    if report.get("status") not in ALERTABLE_STATUSES:
        return {"sent": False, "reason": "not corroborated or verified", "recipients": 0}

    if report.get("severity") not in BROADCAST_SEVERITIES:
        return {
            "sent": False,
            "reason": f"severity '{report.get('severity')}' is below the broadcast threshold",
            "recipients": 0,
        }

    cooled = recent_alert_nearby(report, ALERT_COOLDOWN_MINUTES)
    if cooled:
        return {
            "sent": False,
            "reason": f"an alert for this area was already sent at {cooled.get('sent_at')}",
            "recipients": 0,
        }

    recipients = find_recipients(report)
    if not recipients:
        return {"sent": False, "reason": "no opted-in residents within range", "recipients": 0}

    english = build_alert_message(report)

    # Translate once per language, not once per recipient — a neighbourhood
    # broadcast is the one place where the same text goes to many people.
    rendered = {"english": english}
    if translate:
        for language in {r.get("language", "english") for r in recipients}:
            if language in rendered:
                continue
            try:
                rendered[language] = await asyncio.to_thread(translate, english, language)
            except Exception as e:
                logger.error(f"❌ Could not translate alert to {language}: {e}")
                rendered[language] = english

    delivered = 0
    for subscriber in recipients:
        phone = subscriber.get("user_id")
        if not phone:
            continue
        text = rendered.get(subscriber.get("language", "english"), english)
        try:
            if await send_message(phone, text):
                delivered += 1
        except Exception as e:
            logger.error(f"❌ Alert to {phone} failed: {e}")

    record_alert(report, delivered, report.get("corroborated_with") or [])
    logger.info(
        f"📢 Alert for {report_id} sent to {delivered}/{len(recipients)} residents "
        f"within {ALERT_BROADCAST_RADIUS_KM}km"
    )
    return {
        "sent": delivered > 0,
        "reason": f"delivered to {delivered} of {len(recipients)} residents in range",
        "recipients": delivered,
    }
