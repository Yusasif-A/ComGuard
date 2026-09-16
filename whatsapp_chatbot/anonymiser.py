"""
Reporter protection.

Nothing leaves ComGuard carrying the reporter's identity. Three things happen
before a report is stored or forwarded:

1. Image metadata is destroyed. A phone photo carries GPS coordinates, the
   device serial, and a timestamp — enough to identify who stood where. We
   re-encode the pixels and drop every EXIF/XMP/ICC block rather than trying to
   delete individual tags, because a deny-list of tags is a deny-list you will
   eventually forget to update.

2. The phone number becomes a pseudonym. Reports are keyed by an HMAC of the
   number, so the system can still tell two reports apart from two reports by
   the same person (which is what "independent corroboration" requires) without
   the report row containing a number anyone could call.

3. Free text is scrubbed. People name themselves and others in voice notes —
   "this is Musa from 12 Adeniyi Street, call me on 0803..." — so phone numbers,
   emails, and account-like digit runs are redacted before the text is stored or
   sent onward.

Order matters: scrub before store, store before forward. Every write path in
reports.py goes through here.
"""

import hashlib
import hmac
import io
import logging
import os
import re
from typing import Optional, Tuple

from config import REPORT_SALT

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on deployment image
    PIL_AVAILABLE = False
    logger.warning("⚠️ Pillow not installed — image metadata CANNOT be stripped")


# ── 1. Image metadata ────────────────────────────────────────────────────────

def strip_image_metadata(image_bytes: bytes) -> Tuple[bytes, dict]:
    """Return (clean_jpeg_bytes, report) with every metadata block removed.

    The pixels are decoded and re-encoded into a brand-new image object, so only
    the pixel data survives — EXIF (including GPS), XMP, IPTC and ICC blocks are
    all left behind. The returned report says what was found, which is what the
    user is shown ("I removed the location tag from your photo").

    If Pillow is unavailable or the image will not decode, this returns the
    original bytes with ``stripped: False``. Callers MUST check that flag and
    refuse to forward an unstripped image — silently passing the original on
    would leak exactly what this function exists to remove.
    """
    report = {"stripped": False, "had_gps": False, "had_exif": False, "error": None}

    if not PIL_AVAILABLE:
        report["error"] = "pillow_unavailable"
        return image_bytes, report

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            exif = None
            try:
                exif = img.getexif()
            except Exception:
                exif = None

            if exif:
                report["had_exif"] = True
                # 0x8825 is the GPS IFD pointer — its presence means the photo
                # carries coordinates.
                report["had_gps"] = 0x8825 in exif

            img.load()
            clean = Image.new(img.mode, img.size)
            clean.putdata(list(img.getdata()))

            if clean.mode not in ("RGB", "L"):
                clean = clean.convert("RGB")

            out = io.BytesIO()
            clean.save(out, format="JPEG", quality=88, optimize=True)
            report["stripped"] = True
            logger.info(
                "🧼 Image metadata stripped (exif=%s, gps=%s, %d → %d bytes)",
                report["had_exif"], report["had_gps"], len(image_bytes), out.tell(),
            )
            return out.getvalue(), report

    except Exception as e:
        logger.error(f"❌ Could not strip image metadata: {e}")
        report["error"] = str(e)
        return image_bytes, report


# ── 2. Reporter pseudonym ────────────────────────────────────────────────────

def _salt() -> bytes:
    """The HMAC salt, with a loud warning if the deployment never set one.

    A missing salt is a real weakness — an attacker with the report table could
    confirm a guessed phone number by hashing it. We still produce a pseudonym
    rather than crashing (a reporting line that refuses to accept reports is
    worse than one with a weak pseudonym), but the warning is deliberately noisy.
    """
    if REPORT_SALT:
        return REPORT_SALT.encode()
    logger.warning(
        "⚠️ REPORT_SALT is not set — reporter pseudonyms are guessable by anyone "
        "who can read the database. Set REPORT_SALT to a long random string."
    )
    return b"comguard-unsalted-fallback"


# Numbers reach us in more than one shape — WhatsApp sends E.164 without a plus
# (2348031234567), while anything typed or imported may be national (08031234567).
# Hashing those raw would give one person two pseudonyms, and two pseudonyms look
# like two witnesses, which is exactly the thing corroboration must not get wrong.
DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "234")


def canonical_number(phone_number: str) -> str:
    """Reduce a phone number to one canonical digit string before hashing.

    A national number beginning with a trunk "0" is converted to its
    international form. Anything already international, or in a shape we do not
    recognise, is left as its digits — an unrecognised format hashes
    consistently with itself, which is the property that actually matters.
    """
    digits = re.sub(r"\D", "", phone_number or "")
    if not digits:
        return ""

    if digits.startswith("00"):
        digits = digits[2:]

    # Trunk-prefixed national number, e.g. 08031234567 -> 2348031234567
    if digits.startswith("0") and len(digits) >= 10:
        digits = DEFAULT_COUNTRY_CODE + digits[1:]

    return digits


def reporter_pseudonym(phone_number: str) -> str:
    """Stable, non-reversible id for a reporter, e.g. "CG-4F2A9C31".

    Same person always gives the same id regardless of how their number was
    written, so corroboration can tell "two people saw this" from "one person
    sent two photos" — but the id cannot be turned back into a number.
    """
    digest = hmac.new(
        _salt(), canonical_number(phone_number).encode(), hashlib.sha256
    ).hexdigest()
    return f"CG-{digest[:8].upper()}"


def coarse_location(lat: Optional[float], lon: Optional[float], places: int = 2) -> Optional[dict]:
    """Round coordinates to roughly a 1km cell for anything shown to an authority.

    The precise point is needed internally to cluster nearby reports, but a
    forwarded payload should not say which building someone was standing in.
    Two decimal places is about 1.1km at the equator.
    """
    if lat is None or lon is None:
        return None
    return {"lat": round(float(lat), places), "lon": round(float(lon), places)}


# ── 3. Text scrubbing ────────────────────────────────────────────────────────

# Nigerian mobile numbers appear as 08031234567, +2348031234567, 0803 123 4567.
# The pattern is deliberately broad: over-redacting a long number in a report is
# harmless, under-redacting a phone number is not.
_PHONE_RE = re.compile(r"(?:(?:\+?234)|0)[\s\-.]?\d(?:[\s\-.]?\d){8,11}")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
# Bank account (10 digits) / NIN (11 digits) / BVN (11 digits) style runs.
_LONG_DIGITS_RE = re.compile(r"\b\d{10,}\b")
_URL_RE = re.compile(r"https?://\S+")

# "my name is X", "this is X speaking", "I am X" — the openings people actually
# use in a voice note. Only the name that follows is replaced, so the sentence
# still reads naturally in the stored summary.
#
# The trigger phrase is matched case-insensitively via an inline (?i:...) group,
# but the name itself is NOT: it has to be capitalised to count. A blanket
# re.IGNORECASE here makes [A-Z] match lowercase too, so "My name is Musa
# Ibrahim and they said..." swallows "and" along with the name.
_SELF_ID_RE = re.compile(
    r"\b(?i:my name is|this is|i am|i'm|na me be|dis na)\s+"
    r"([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){0,2})"
)


def scrub_text(text: str) -> str:
    """Redact direct identifiers from free text before it is stored or shared."""
    if not text:
        return text

    cleaned = _EMAIL_RE.sub("[email removed]", text)
    cleaned = _URL_RE.sub("[link removed]", cleaned)
    # One label for both: a 10-digit run starting with 0 is equally likely to be
    # a phone number or a bank account, and calling an account number a "phone"
    # in a scam report misleads the dispatcher reading it.
    cleaned = _PHONE_RE.sub("[number removed]", cleaned)
    cleaned = _LONG_DIGITS_RE.sub("[number removed]", cleaned)
    # The self-identification pass runs last so the trigger phrase is still
    # intact — an earlier pass could otherwise have rewritten part of it.
    cleaned = _SELF_ID_RE.sub(
        lambda m: m.group(0)[: m.start(1) - m.start(0)] + "[name removed]", cleaned
    )
    return cleaned


def contains_identifier(text: str) -> bool:
    """True if the text still looks like it holds a direct identifier.

    Used as a post-scrub assertion on anything about to leave the system, so a
    pattern we failed to match shows up in the logs rather than in a payload.
    """
    if not text:
        return False
    return bool(
        _PHONE_RE.search(text) or _EMAIL_RE.search(text) or _LONG_DIGITS_RE.search(text)
    )


def anonymise_report_payload(report: dict) -> dict:
    """Build the JSON an authority receives — pseudonym in, phone number out.

    This is the only shape that should ever be POSTed outward. It is constructed
    by picking fields explicitly rather than by deleting them from the stored
    report, so a field added to the database later cannot leak by default.
    """
    location = report.get("location") or {}
    payload = {
        "report_id": report.get("report_id"),
        "reporter_ref": report.get("reporter_ref"),  # pseudonym, never a number
        "category": report.get("category"),
        "severity": report.get("severity"),
        "summary": scrub_text(report.get("summary") or ""),
        "next_steps": report.get("next_steps"),
        "evidence": {
            "had_photo": bool(report.get("has_image")),
            "had_voice_note": bool(report.get("has_audio")),
            "image_authenticity": report.get("image_authenticity"),
            "notice_text": scrub_text(report.get("ocr_text") or "") or None,
        },
        "location": coarse_location(location.get("lat"), location.get("lon")),
        "location_name": report.get("location_name"),
        "reported_at": report.get("created_at"),
        "corroborating_reports": report.get("corroboration_count", 1),
        "status": report.get("status"),
        "language": report.get("language"),
    }

    leaked = [
        key for key in ("summary", "next_steps")
        if contains_identifier(str(payload.get(key) or ""))
    ]
    if leaked:
        logger.error(
            "❌ Identifier survived scrubbing in %s for report %s — blanking the field",
            leaked, payload.get("report_id"),
        )
        for key in leaked:
            payload[key] = "[withheld — could not be safely anonymised]"

    return payload
