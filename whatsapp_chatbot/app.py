import base64
import re
import glob
import shutil
import httpx
from io import BytesIO
from typing import Dict, List, Any
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from hmac_validator import validate_whatsapp_hmac
import os
import logging
from dotenv import load_dotenv
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import tempfile
import json


_ffmpeg_cache = None

def _find_ffmpeg():
    """Find ffmpeg/ffprobe including winget installations. Result is cached after first call."""
    global _ffmpeg_cache
    if _ffmpeg_cache is not None:
        return _ffmpeg_cache
    # 1. Try process PATH
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg:
        _ffmpeg_cache = (ffmpeg, ffprobe)
        return _ffmpeg_cache
    # 2. Deep search all winget Packages (handles any package ID suffix)
    appdata = os.environ.get("LOCALAPPDATA", "C:\\Users\\USER\\AppData\\Local")
    packages_dir = os.path.join(appdata, "Microsoft", "WinGet", "Packages")
    for ff in glob.glob(os.path.join(packages_dir, "**", "ffmpeg.exe"), recursive=True):
        probe = ff.replace("ffmpeg.exe", "ffprobe.exe")
        ff_dir = os.path.dirname(ff)
        os.environ['PATH'] = os.environ.get('PATH', '') + os.pathsep + ff_dir
        logger.info(f"Found ffmpeg at: {ff}")
        _ffmpeg_cache = (ff, probe if os.path.exists(probe) else None)
        return _ffmpeg_cache
    logger.warning("ffmpeg not found — MP3 concat will fall back to raw join")
    _ffmpeg_cache = (None, None)
    return _ffmpeg_cache


def _concat_mp3_chunks(chunks: list) -> bytes:
    """Concatenate MP3 chunks into a single valid MP3 using pydub."""
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]
    try:
        from pydub import AudioSegment
        ffmpeg_path, ffprobe_path = _find_ffmpeg()
        if ffmpeg_path:
            AudioSegment.converter = ffmpeg_path
            if ffprobe_path:
                AudioSegment.ffprobe = ffprobe_path
        combined = AudioSegment.empty()
        for chunk in chunks:
            combined += AudioSegment.from_mp3(BytesIO(chunk))
        out = BytesIO()
        combined.export(out, format="mp3")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"⚠️ pydub concat failed ({e}), using raw join")
        return b"".join(chunks)



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv()

from services import get_stt_service, get_tts_service, get_translator
from nllb_translator import yoruba_numbers_to_words
from baby_tracker import get_all_user_threads, has_logged_weight_this_month
from site_feedback import (
    save_site_feedback, get_site_feedback, get_feedback_stats,
    rows_to_csv, rows_to_json,
    DASHBOARD_USER as FEEDBACK_DASHBOARD_USER,
    DASHBOARD_PASSWORDS as FEEDBACK_DASHBOARD_PASSWORDS,
)

# Import unified agent directly
from unified_agent import unified_agent, _LANGUAGE_NAMES

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN")

from config import (
    HAUSA_TTS_VOICE, IGBO_TTS_VOICE, YORUBA_TTS_VOICE, ENGLISH_TTS_VOICE,
    FOOD_RECOGNITION_URL
)

if not all([WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN]):
    logger.error("Missing one or more required WhatsApp environment variables.")
    exit(1)

from feedback import (
    store_feedback, store_conversation, store_pending_response, get_pending_response,
    get_user_language, set_user_language,
    set_user_journey, get_user_journey, get_all_users_with_journey,
    get_all_users_for_daily_tips, get_all_users_for_weight_reminders,
    has_opted_out_of_tips, set_tips_opted_out,
    has_accepted_terms, set_terms_accepted,
    get_job_last_run_at, set_job_last_run_at, get_tip_cycle, record_tip_sent
)

# scheduler_state document ids for the two recurring background jobs
DAILY_TIPS_JOB_ID = "daily_tips"
WEIGHT_REMINDERS_JOB_ID = "weight_reminders"

logger.info("🚀 Starting WhatsApp Voice Retriever Agent")
_find_ffmpeg()  # Cache ffmpeg path at startup

# ── Onboarding messages ──────────────────────────────────────────────────
# Step 1 (before anything else): intro + Terms/Privacy, gated behind a single
# "Approve" button. Step 2 (sent right after Approve is tapped): the journey
# question, with the language-switch buttons attached — language switching
# only becomes available from this second message onward, not on the terms
# message itself.
TERMS_INTRO_MESSAGE = (
    "👋 Welcome to Chop Beta!\n\n"
    "I'm here to help pregnant and nursing mothers with nutrition — "
    "making sure you and your baby get the right foods and nutrients every day.\n\n"
    "I can respond in English, Hausa, Yoruba and Igbo — and in text or voice.\n\n"
    "By using this service, you agree to our Terms of Use and Privacy Policy:\n"
    "📄 Terms: https://chopbeta-ai.web.app/terms-of-use\n"
    "🔒 Privacy: https://chopbeta-ai.web.app/privacy-policy\n\n"
    "Tap *Approve* below to continue."
)

JOURNEY_ONBOARDING_MESSAGE = (
    "To personalise my advice, which best describes you?\n\n"
    "1️⃣ I am pregnant\n"
    "2️⃣ I am breastfeeding\n"
    "3️⃣ I am a parent or caregiver of a child\n\n"
    "Reply with *1*, *2*, or *3* to get started."
)

WEIGHT_REMINDER_MESSAGES = {
    "english": (
        "⚖️ *Baby Weight Check Reminder*\n\n"
        "Hello mama! 👋 It's time to check how well your baby is growing this month. "
        "Just send me their age and weight in kg and I'll take a look for you."
    ),
    "hausa": (
        "⚖️ *Tunatarwa Don Duba Nauyin Jariri*\n\n"
        "Sannu mama! 👋 Lokaci ya yi da za ki duba yadda jaririnki ke girma wannan wata. "
        "Kawai aiko mini da shekarunsa (a watanni) da nauyinsa a kg, zan duba miki."
    ),
    "igbo": (
        "⚖️ *Ncheta Ilele Ibu Nwa*\n\n"
        "Ndewo mama! 👋 Oge eruola iji lele etu nwa gị si eto n'ọnwa a. "
        "Ziga m afọ ya (n'ọnwa) na ibu ya na kg, m ga-elebara gị anya."
    ),
    "yoruba": (
        "⚖️ *Ìránnilétí Ìṣàyẹ̀wò Ìwúwo Ọmọ*\n\n"
        "Ẹ n lẹ o mama! 👋 Àkókò ti tó láti ṣàyẹ̀wò bí ọmọ rẹ ṣe ń dàgbà ní osù yìí. "
        "Kàn fi ọjọ́ orí rẹ̀ (ní osù) àti ìwúwo rẹ̀ ní kg ránṣẹ́ sí mi, màá wò ó fún ọ."
    ),
}
# NOTE: Hausa/Igbo/Yoruba text above is a best-effort static translation —
# please have a native speaker review it before relying on it in production.

TIP_FOOTERS = {
    "english": "🥦 *Nutritional Tip from Chop Beta*",
    "hausa": "🥦 *Shawarar Abinci Mai Gina Jiki daga Chop Beta*",
    "igbo": "🥦 *Ndụmọdụ Nri Na-elekọta Ahụ site na Chop Beta*",
    "yoruba": "🥦 *Ìmọ̀ràn Ìjẹunjẹ láti ọ̀dọ̀ Chop Beta*",
}
# Same caveat as above — best-effort translation, please have a native speaker
# sanity-check before relying on it in production.

# One-line opt-out hint appended under every daily tip, in the user's own
# language. The BUTTON itself is always titled "Stop" in English regardless of
# language (deliberate — "Stop" is widely recognised, and keeping one fixed
# label means the button id/title never has to be matched per-language).
TIP_OPT_OUT_HINTS = {
    "english": "_Tap Stop to stop getting daily tips._",
    "hausa": "_Danna Stop don dakatar da samun shawarwarin yau da kullum._",
    "igbo": "_Pịa Stop ka ị kwụsị ịnata ndụmọdụ kwa ụbọchị._",
    "yoruba": "_Tẹ Stop láti dá gbígba ìmọ̀ràn ojoojúmọ́ dúró._",
}

# Confirmation sent right after someone taps Stop, in their own language.
TIP_OPT_OUT_CONFIRMATIONS = {
    "english": (
        "✅ Done — you will no longer receive daily nutrition tips.\n\n"
        "You can still message me any time with a question about food, "
        "pregnancy, or your baby."
    ),
    "hausa": (
        "✅ An gama — ba za ki ƙara samun shawarwarin abinci na yau da kullum ba.\n\n"
        "Har yanzu za ki iya aiko mini saƙo kowane lokaci idan kina da tambaya "
        "game da abinci, ciki, ko jaririnki."
    ),
    "igbo": (
        "✅ Emechaala — ị gaghị anatakwa ndụmọdụ nri kwa ụbọchị ọzọ.\n\n"
        "Ị ka nwere ike izitere m ozi mgbe ọ bụla ma ọ bụrụ na ị nwere ajụjụ "
        "gbasara nri, ime, ma ọ bụ nwa gị."
    ),
    "yoruba": (
        "✅ Ó ti parí — o kò ní gba ìmọ̀ràn ìjẹunjẹ ojoojúmọ́ mọ́.\n\n"
        "O ṣì lè fi ìránṣẹ́ ránṣẹ́ sí mi nígbà yòówù tí o bá ní ìbéèrè nípa "
        "oúnjẹ, oyún, tàbí ọmọ rẹ."
    ),
}
# Same caveat as the messages above — best-effort translation, please have a
# native speaker sanity-check before relying on it in production.

# Fixed English button label + id for the daily-tip opt-out.
TIP_STOP_BUTTON = {"id": "stop_daily_tips", "title": "Stop"}

# ── Send schedule ────────────────────────────────────────────────────────────
# Both jobs run at a fixed wall-clock time rather than "every N hours from
# whenever the process happened to start". Mothers get their tip at the same
# time each morning, and a redeploy can never shift the schedule.
#
# Africa/Lagos has no daylight saving, so a fixed local hour is unambiguous
# year-round and .replace(hour=...) below can never land on a skipped or
# repeated wall-clock time.
SCHEDULE_TIMEZONE = ZoneInfo(os.getenv("SCHEDULE_TIMEZONE", "Africa/Lagos"))

# Daily nutrition tips — 10:00 every day.
TIPS_HOUR = int(os.getenv("TIPS_HOUR", "10"))

# Weight reminders — 10:00 once a week (0 = Monday ... 6 = Sunday).
# Weighing itself is a MONTHLY check-in; the job runs weekly only so a mother
# who hasn't weighed yet gets another nudge during the month. Anyone who has
# already logged a weight this month is skipped (has_logged_weight_this_month).
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "10"))
REMINDER_WEEKDAY = int(os.getenv("REMINDER_WEEKDAY", "0"))

# If the service was down at the scheduled time, it still sends when it comes
# back — but only within this window, so a container that starts at 11pm does
# not fire off "morning" tips in the middle of the night.
CATCH_UP_GRACE_HOURS = int(os.getenv("CATCH_UP_GRACE_HOURS", "6"))


def _last_scheduled_time(now_local, hour: int, weekday: int = None):
    """The most recent moment the job was due, at or before now_local."""
    candidate = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if weekday is None:
        if candidate > now_local:
            candidate -= timedelta(days=1)
        return candidate
    candidate -= timedelta(days=(now_local.weekday() - weekday) % 7)
    if candidate > now_local:
        candidate -= timedelta(days=7)
    return candidate


def _plan_next_run(last_run, hour: int, weekday: int = None):
    """Decide whether the job is due now, or how long to wait.

    Returns (wait_seconds, due_slot). wait_seconds == 0 means run immediately.
    `last_run` is the persisted timestamp of the previous cycle, which is what
    makes a redeploy safe: restarting re-reads it, sees this slot was already
    handled, and waits for the next one instead of resending.
    """
    now_local = datetime.now(SCHEDULE_TIMEZONE)
    due = _last_scheduled_time(now_local, hour, weekday)

    if last_run is not None:
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        last_run = last_run.astimezone(SCHEDULE_TIMEZONE)

    already_sent = last_run is not None and last_run >= due
    within_grace = (now_local - due) <= timedelta(hours=CATCH_UP_GRACE_HOURS)

    if not already_sent and within_grace:
        return 0.0, due

    step = timedelta(days=1) if weekday is None else timedelta(days=7)
    return (due + step - now_local).total_seconds(), due + step


async def _send_weight_reminders():
    """Background task — sends the weight-tracking reminder weekly at a fixed
    local time. Only to breastfeeding mothers and caregivers (pregnant women
    have no baby to weigh yet) who have accepted the terms, and only to those
    who have not already logged a weight this month.

    Runs at REMINDER_HOUR local time on REMINDER_WEEKDAY. The slot already
    handled is recorded in MongoDB, so redeploying does not resend it or shift
    the schedule.
    """
    while True:
        last_run = get_job_last_run_at(WEIGHT_REMINDERS_JOB_ID)
        wait_seconds, slot = _plan_next_run(last_run, REMINDER_HOUR, REMINDER_WEEKDAY)

        if wait_seconds > 0:
            logger.info(
                f"💤 Weight reminders: next send {slot.strftime('%a %d %b %Y at %H:%M %Z')} "
                f"(in {wait_seconds / 3600:.1f}h; last sent: "
                f"{last_run.isoformat() if last_run else 'never'})"
            )
            await asyncio.sleep(wait_seconds)

        cycle_start = datetime.now(timezone.utc)
        set_job_last_run_at(WEIGHT_REMINDERS_JOB_ID, cycle_start)
        sent_numbers = []
        already_weighed = []

        try:
            # Query already restricts to breastfeeding/caregiver + terms accepted.
            eligible = [
                (u["user_id"], u.get("language", "english"))
                for u in get_all_users_for_weight_reminders()
                if u.get("user_id")
            ]
            logger.info(
                f"⏰ Weight reminder cycle started at {cycle_start.isoformat()} — "
                f"{len(eligible)} eligible user(s) (breastfeeding + caregiver, terms accepted)"
            )
            for phone, language in eligible:
                # Belt-and-braces terms gate, same rationale as the daily tips job.
                if not has_accepted_terms(phone):
                    logger.info(f"⏭️ Skipping reminder for {phone} — terms not accepted")
                    continue

                # Weighing is a monthly check-in. If she already logged a weight
                # this calendar month there is nothing to remind her about, so
                # skip her for the remaining weekly runs of this month.
                if has_logged_weight_this_month(phone):
                    logger.info(f"⏭️ Skipping reminder for {phone} — already logged a weight this month")
                    already_weighed.append(phone)
                    continue

                try:
                    reminder_text = WEIGHT_REMINDER_MESSAGES.get(language, WEIGHT_REMINDER_MESSAGES["english"])
                    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
                    headers = {
                        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "messaging_product": "whatsapp",
                        "to": phone,
                        "type": "text",
                        "text": {"body": reminder_text}
                    }
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        logger.info(f"✅ [{datetime.now(timezone.utc).isoformat()}] Reminder sent to {phone} ({language})")
                        sent_numbers.append(phone)
                    else:
                        logger.warning(f"⚠️ Reminder failed for {phone}: {resp.status_code} {resp.text}")
                except Exception as e:
                    logger.error(f"❌ Reminder error for {phone}: {e}")
        except Exception as e:
            logger.error(f"❌ Weight reminder task error: {e}")

        logger.info(
            f"⏰ Weight reminder cycle finished at {datetime.now(timezone.utc).isoformat()} — "
            f"sent {len(sent_numbers)}: {sent_numbers}"
        )
        if already_weighed:
            logger.info(
                f"⏭️ Weight reminder skipped {len(already_weighed)} user(s) who already "
                f"weighed this month: {already_weighed}"
            )


async def _send_tips_to_all_feedback_users():
    """One-shot: send a test tip to every number in the conversations DB.

    NOTE: this function is currently dead code — it is defined but never
    scheduled anywhere (the live daily job is _send_daily_tips below). It also
    ignores opt-outs, so do NOT wire it up without adding a
    has_opted_out_of_tips() check first.
    """
    from tips import TIPS
    import random

    await asyncio.sleep(15)  # Wait for server to fully start
    try:
        to_send = get_all_user_threads()
        logger.info(f"💡 Test tips: sending to {len(to_send)} user(s)")
        tip_text = random.choice(TIPS["english"]["TIPS_BREASTFEEDING"])
        tip_prompt = (
            f"Send the user a friendly daily nutrition tip based on this:\n"
            f"{tip_text}\n\n"
            f"Keep it warm, simple, and under 3 sentences. "
            f"Do not start with 'Did you know'. Make it feel personal, not like a broadcast. "
            f"Do not call any tool. Just respond directly."
        )
        for phone in to_send:
            try:
                response = await unified_agent.get_response(
                    content=tip_prompt,
                    thread_id=phone,
                    message_type="text",
                    language="english"
                )
                await send_whatsapp_message(phone, response + "\n\n🥦 *Nutritional Tip from Chop Beta*")
                logger.info(f"✅ Test tip sent to {phone}")
            except Exception as e:
                logger.error(f"❌ Test tip failed for {phone}: {e}")
    except Exception as e:
        logger.error(f"❌ Test tips task error: {e}")


async def _send_daily_tips():
    """
    Background task — sends a personalised daily tip to every user who has saved a journey.

    Timing is anchored to a `last_run_at` timestamp persisted in MongoDB (via
    get_job_last_run_at/set_job_last_run_at) rather than a plain `asyncio.sleep`
    counted from process start. This service restarts automatically on any crash
    (`restart: unless-stopped` in docker-compose), and a pure in-memory sleep would
    reset its 24h countdown to zero on every restart — which was the likely cause
    of tips going out for a few days and then silently stopping (each restart
    pushed the next send another full day out, and if restarts happened more
    often than once a day, the 24h mark could never be reached at all).
    """
    from tips import TIPS
    import random

    def _get_tip_list(language: str, journey: str, baby_age):
        """
        Pick the right tip list for this user's language + journey.

        Returns (tip_list, cycle_key). cycle_key identifies WHICH list this is,
        e.g. "yoruba|TIPS_PREGNANT". The no-repeat-until-exhausted logic stores
        seen positions as indices into a specific list, so those indices become
        meaningless if the underlying list changes (user switches language,
        moves from pregnant to breastfeeding, or their baby ages into the next
        bracket). Comparing cycle_key lets us detect that and start a fresh
        cycle instead of carrying stale indices across a different list.

        Falls back to English if the user's language isn't in TIPS (shouldn't
        happen, but safe) — and the key reflects the language actually used.
        For caregivers with no known baby age there's no single "general
        caregiver" list per language, so we combine the 6-12mo + 12-24mo lists
        for that language — keeps it in the right language instead of silently
        falling back to English.
        """
        resolved_lang = language if language in TIPS else "english"
        lang_tips = TIPS[resolved_lang]

        if journey == "pregnant":
            bucket = "TIPS_PREGNANT"
        elif journey == "breastfeeding":
            bucket = "TIPS_BREASTFEEDING"
        elif journey == "caregiver":
            if baby_age is not None:
                if baby_age < 6:
                    bucket = "TIPS_BABY_0_6"
                elif baby_age < 12:
                    bucket = "TIPS_BABY_6_12"
                else:
                    bucket = "TIPS_BABY_12_24"
            else:
                bucket = "TIPS_BABY_6_12+TIPS_BABY_12_24"
        else:
            return None, None

        if bucket == "TIPS_BABY_6_12+TIPS_BABY_12_24":
            tip_list = lang_tips["TIPS_BABY_6_12"] + lang_tips["TIPS_BABY_12_24"]
        else:
            tip_list = lang_tips[bucket]

        return tip_list, f"{resolved_lang}|{bucket}"

    while True:
        # Wait until the next scheduled send time. The slot already handled is
        # persisted in MongoDB, so a redeploy re-reads it, sees today is done,
        # and waits for tomorrow rather than resending or resetting a countdown.
        last_run = get_job_last_run_at(DAILY_TIPS_JOB_ID)
        wait_seconds, slot = _plan_next_run(last_run, TIPS_HOUR)

        if wait_seconds > 0:
            logger.info(
                f"💤 Daily tips: next send {slot.strftime('%a %d %b %Y at %H:%M %Z')} "
                f"(in {wait_seconds / 3600:.1f}h; last sent: "
                f"{last_run.isoformat() if last_run else 'never'})"
            )
            await asyncio.sleep(wait_seconds)

        cycle_start = datetime.now(timezone.utc)
        set_job_last_run_at(DAILY_TIPS_JOB_ID, cycle_start)
        sent_numbers = []
        skipped = []

        try:
            # get_all_users_for_daily_tips() already excludes anyone who tapped
            # Stop, so opted-out users are never even iterated over here.
            users = get_all_users_for_daily_tips()
            logger.info(f"💡 Daily tips cycle started at {cycle_start.isoformat()} — {len(users)} eligible user(s) (opted-out users excluded)")

            for user in users:
                phone = user.get("user_id")
                journey = user.get("journey", "")
                baby_age = user.get("baby_age_months")
                language = user.get("language", "english")

                if not phone or not journey:
                    skipped.append(f"{phone or '(no phone)'}: missing phone/journey")
                    continue

                # Belt-and-braces terms gate. The DB query already filters these
                # out, but daily tips are an unsolicited outbound broadcast, so we
                # re-check per user rather than trusting a single query condition.
                if not has_accepted_terms(phone):
                    logger.info(f"⏭️ Skipping {phone} — terms not accepted")
                    skipped.append(f"{phone}: terms not accepted")
                    continue

                # Everything below is wrapped in try/except so a problem with ONE user
                # (e.g. an edge case like a single-item tip list, a DB hiccup, an LLM
                # error) can never abort the loop and silently skip every user that
                # comes after them in this cycle — that was causing tips to stop
                # reaching some users after some time.
                try:
                    tip_list, cycle_key = _get_tip_list(language, journey, baby_age)
                    if tip_list is None:
                        skipped.append(f"{phone}: no tip list for journey={journey}")
                        continue

                    if not tip_list:
                        logger.warning(f"⚠️ No tips available for {phone} ({journey}, {language}) — skipping")
                        skipped.append(f"{phone}: empty tip list ({journey}, {language})")
                        continue

                    # ── No-repeat-until-exhausted selection ──────────────────
                    # Every tip in the user's list is sent once before ANY tip
                    # repeats. We track the indices already sent in this pass
                    # ("seen") in MongoDB and only ever choose from what's left,
                    # so a 27-tip list means 27 distinct days before a repeat.
                    # (The old logic excluded only the previous tip, so with
                    # random.choice the same tip could easily come back two days
                    # later.) Persisted in the DB so the cycle survives restarts.
                    cycle = get_tip_cycle(phone)
                    last_idx = cycle.get("last_index")

                    # Only carry "seen" forward if it belongs to the SAME list.
                    # If the user switched language/journey, or their baby aged
                    # into a new bracket, the stored indices point into a
                    # different list and must be discarded.
                    if cycle.get("key") == cycle_key:
                        seen = {
                            i for i in (cycle.get("seen") or [])
                            if isinstance(i, int) and 0 <= i < len(tip_list)
                        }
                    else:
                        seen = set()

                    remaining = [i for i in range(len(tip_list)) if i not in seen]

                    if not remaining:
                        # Whole list exhausted — start a fresh cycle. Exclude the
                        # tip we just sent so the last tip of one cycle and the
                        # first of the next can't be the same one.
                        seen = set()
                        remaining = [i for i in range(len(tip_list)) if i != last_idx] or list(range(len(tip_list)))
                        logger.info(
                            f"🔄 {phone}: completed full cycle of {len(tip_list)} tips "
                            f"({cycle_key}) — starting over"
                        )

                    idx = random.choice(remaining)
                    tip_text = tip_list[idx]
                    new_cycle = {
                        "key": cycle_key,
                        "seen": sorted(seen | {idx}),
                        "last_index": idx,
                    }

                    lang_name = _LANGUAGE_NAMES.get(language, "English")
                    # tip_text is ALREADY written natively in the user's language —
                    # it is not English being handed to Gemma to translate. We only
                    # want Gemma to warm it up / make it feel personal, in the SAME
                    # language and using the SAME wording for key terms, so it can't
                    # reintroduce the mistranslation issues (e.g. "breast milk")
                    # that happened when Gemma had to translate from English itself.
                    # (No need to add "[Respond in X]" manually here — get_response()
                    # already prepends that directive for multilingual mode.)
                    tip_prompt = (
                        f"This nutrition tip is already written in {lang_name}. Do NOT translate it into "
                        f"another language and do NOT translate it to English — keep it in {lang_name}, "
                        f"and keep the same meaning and key terms exactly as written:\n\n"
                        f"{tip_text}\n\n"
                        f"Just make it sound warm and personal, like a short message to a friend, "
                        f"under 3 sentences. Do not start with the equivalent of 'Did you know'. "
                        f"Do not call any tool. Just respond directly."
                    )

                    response = await unified_agent.get_response(
                        content=tip_prompt,
                        thread_id=phone,
                        message_type="text",
                        language=language
                    )

                    # Tip + footer + a one-line opt-out hint in the user's language,
                    # delivered with a single "Stop" button (always English-labelled).
                    tip_message = (
                        response
                        + "\n\n" + TIP_FOOTERS.get(language, TIP_FOOTERS["english"])
                        + "\n\n" + TIP_OPT_OUT_HINTS.get(language, TIP_OPT_OUT_HINTS["english"])
                    )
                    delivered = await send_interactive_buttons(
                        phone, tip_message, [TIP_STOP_BUTTON]
                    )

                    # send_interactive_buttons swallows API errors and returns
                    # False. Ignoring that return value meant a rejected send was
                    # still logged as "sent" and written to tip_send_log, so the
                    # audit trail claimed delivery while the phone got nothing.
                    # The commonest rejection is WhatsApp error 131047: outside the
                    # 24-hour customer service window a business may only send an
                    # approved template, not free-form text. Anyone who has not
                    # messaged in 24h silently stops receiving tips.
                    #
                    # On failure we do NOT advance the tip cycle, so this tip is
                    # still unseen and will be offered again next time rather than
                    # being burned on a message that never arrived.
                    if not delivered:
                        logger.warning(
                            f"❌ Daily tip NOT delivered to {phone} ({journey}, {language}) "
                            f"— WhatsApp rejected the send; tip #{idx} not consumed"
                        )
                        skipped.append(f"{phone}: WhatsApp rejected the send")
                        continue

                    # Persist only after a CONFIRMED send: updates the anti-repeat
                    # index and appends to tip_send_log, a durable audit trail you
                    # can query in MongoDB to see exactly when a number last got a
                    # tip — more reliable than container logs, which reset on restart.
                    sent_at = datetime.now(timezone.utc)
                    record_tip_sent(phone, idx, journey, language, tip_cycle=new_cycle)
                    logger.info(
                        f"✅ [{sent_at.isoformat()}] Daily tip sent to {phone} "
                        f"({journey}, {language}, tip #{idx} — "
                        f"{len(new_cycle['seen'])}/{len(tip_list)} through cycle)"
                    )
                    sent_numbers.append(phone)
                except Exception as e:
                    logger.error(f"❌ Failed to send tip to {phone}: {e}")
                    skipped.append(f"{phone}: send error — {e}")
                    continue

        except Exception as e:
            logger.error(f"❌ Daily tips task error: {e}")

        finished_at = datetime.now(timezone.utc)
        logger.info(
            f"💡 Daily tips cycle finished at {finished_at.isoformat()} — "
            f"sent {len(sent_numbers)}: {sent_numbers}"
        )
        if skipped:
            logger.info(f"⏭️ Daily tips skipped {len(skipped)}: {skipped}")

        # Loop back to the top — it will read last_run_at again and sleep for
        # whatever's left of the interval (normally the full interval, since
        # we just set it above).


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the lifecycle of the FastAPI app and initialize components."""

    logger.info("=" * 60)
    logger.info("🥗 Starting Beta Food WhatsApp Agent")
    logger.info("=" * 60)

    try:
        # Initialize unified agent directly
        logger.info("🔧 Initializing nutrition agent...")
        await unified_agent.initialize()
        logger.info("✅ Nutrition agent ready!")

        # Start daily weight reminder background task
        reminder_task = asyncio.create_task(_send_weight_reminders())
        logger.info(
            f"⏰ Weight reminder scheduler started — weekly, "
            f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][REMINDER_WEEKDAY]} "
            f"{REMINDER_HOUR:02d}:00 {SCHEDULE_TIMEZONE}"
        )

        # Start daily tips background task
        tips_task = asyncio.create_task(_send_daily_tips())
        logger.info(f"💡 Daily tips scheduler started — every day at {TIPS_HOUR:02d}:00 {SCHEDULE_TIMEZONE}")


        logger.info("=" * 60)
        logger.info("✅ Beta Food WhatsApp Agent Ready!")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"❌ Failed to initialize: {e}")
        raise

    yield

    # Shutdown
    reminder_task.cancel()
    tips_task.cancel()
    logger.info("Cleaning up components...")
    logger.info("✅ Cleanup completed!")


# FastAPI app with lifespan management
app = FastAPI(
    title="WhatsApp Voice Retriever Agent",
    lifespan=lifespan
)

# The marketing site (Netlify) posts to /feedback from a different origin, so
# the browser needs CORS. Scoped to the known site origins rather than "*" —
# only the feedback endpoint is meant to be called cross-origin, and the
# dashboard is opened directly rather than fetched.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "FEEDBACK_ALLOWED_ORIGINS",
            "https://chopbeta-ai.web.app,https://chopbeta-ai.firebaseapp.com,"
            "https://chopbeta.netlify.app,https://www.chopbeta.com,https://chopbeta.com",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

async def download_media(media_id: str) -> bytes:
    """Download media from WhatsApp."""
    media_metadata_url = f"https://graph.facebook.com/v20.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    async with httpx.AsyncClient() as client:
        metadata_response = await client.get(media_metadata_url, headers=headers)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        download_url = metadata.get("url")

        if not download_url:
            raise HTTPException(status_code=404, detail="Media URL not found.")

        media_response = await client.get(download_url, headers=headers)
        media_response.raise_for_status()
        return media_response.content

def convert_to_data_uri(media_bytes: bytes, mime_type: str) -> str:
    """Convert media bytes to data URI for the agent."""
    b64_data = base64.b64encode(media_bytes).decode()
    return f"data:{mime_type};base64,{b64_data}"

async def recognize_food_from_image(image_bytes: bytes) -> dict:
    """
    Send image to food recognition model and get detected foods.
    Returns dict with 'detected' list of {food, confidence} objects.
    """
    try:
        logger.info(f"📸 Sending image to food recognition model: {FOOD_RECOGNITION_URL}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"file": ("food_image.jpg", image_bytes, "image/jpeg")}
            response = await client.post(FOOD_RECOGNITION_URL, files=files)
            response.raise_for_status()

            result = response.json()
            logger.info(f"✅ Food recognition result: {result}")
            return result

    except httpx.TimeoutException as e:
        logger.error(f"❌ Food recognition timeout: {e}")
        return {"detected": [], "error": "timeout"}
    except httpx.HTTPError as e:
        logger.error(f"❌ Food recognition HTTP error: {e}")
        return {"detected": [], "error": "http_error"}
    except Exception as e:
        logger.error(f"❌ Food recognition error: {e}")
        return {"detected": [], "error": str(e)}

def clean_whatsapp_message(text: str) -> str:
    """Clean up markdown formatting for WhatsApp display."""
    if not text:
        return text
        
    # 1. Convert headers (### Header) to bold (*Header*)
    # Standardize headers to WhatsApp bold
    text = re.sub(r'^#+\s+(.*)$', r'*\1*', text, flags=re.MULTILINE)
    
    # 2. Convert standard markdown bold (**text**) to WhatsApp bold (*text*)
    text = text.replace('**', '*')
    
    # 3. Handle bullet points - convert '*' or '-' at start of line to '•'
    # This prevents them from being interpreted as the start of a bold tag
    text = re.sub(r'^[*-]\s+', r'• ', text, flags=re.MULTILINE)
    
    # 4. Handle horizontal rules
    text = re.sub(r'^---+$', r'────────────────', text, flags=re.MULTILINE)
    
    return text.strip()

async def upload_media_to_whatsapp(media_content: bytes, mime_type: str) -> str:
    """Upload media to WhatsApp servers and return media ID."""
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    # Create a temporary file for upload
    temp_file_path = ""
    try:
        # WhatsApp accepts audio/mpeg (MP3), audio/ogg (OGG with opus codec), audio/amr, audio/mp4
        # Use .mp3 extension for audio/mpeg
        suffix = ".mp3" if "audio" in mime_type else ".png"
        
        logger.info(f"Preparing to upload media: {len(media_content)} bytes, type: {mime_type}")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(media_content)
            temp_file_path = temp_file.name
        
        logger.info(f"Created temp file: {temp_file_path}")

        # The 'with' block has now closed the file, making it safe to re-open.
        with open(temp_file_path, "rb") as temp_file_to_upload:
            # For WhatsApp, use the correct MIME type
            files = {"file": (os.path.basename(temp_file_path), temp_file_to_upload, mime_type)}
            data = {"messaging_product": "whatsapp"}

            async with httpx.AsyncClient(timeout=30.0) as client:
                upload_url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/media"
                logger.info(f"Uploading to: {upload_url}")
                
                response = await client.post(
                    upload_url,
                    headers=headers,
                    files=files,
                    data=data,
                )
                
                logger.info(f"Upload response status: {response.status_code}")
                logger.info(f"Upload response body: {response.text}")
                
                response.raise_for_status()
                result = response.json()
                logger.info(f"Media uploaded successfully. Media ID: {result.get('id')}")

        if "id" not in result:
            raise Exception("Failed to upload media - no ID in response")
        return result["id"]
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        raise
    finally:
        # Cleanup temp file
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
            logger.info(f"Cleaned up temp file: {temp_file_path}")

async def send_typing_indicator(message_id: str) -> bool:
    """Send typing indicator and mark message as read via WhatsApp Cloud API."""
    if not message_id:
        logger.warning("No message ID provided for typing indicator")
        return False

    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    
    json_data = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {
            "type": "text"
        }
    }

    logger.info(f"Sending typing indicator for message {message_id}")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, headers=headers, json=json_data)
            logger.info(f"Typing indicator response status: {response.status_code}")
            logger.info(f"Typing indicator response body: {response.text}")
            
            if response.status_code == 200:
                result = response.json()
                if result.get("success") is True:
                    logger.info("✅ Typing indicator sent successfully!")
                    return True
                else:
                    logger.warning(f"Typing indicator API returned non-success: {result}")
                    return False
            else:
                logger.error(f"Typing indicator API error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending typing indicator: {e}")
            return False

async def send_whatsapp_message(to_number: str, message_text: str = None, media_id: str = None, media_type: str = "text") -> bool:
    """Send message via WhatsApp API (text, audio, or image)."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    
    if media_type == "audio" and media_id:
        json_data = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "audio",
            "audio": {"id": media_id}
        }
    elif media_type == "image" and media_id:
        json_data = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "image",
            "image": {"id": media_id, "caption": message_text or ""}
        }
    else:
        json_data = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": message_text or ""}
        }

    # Enhanced logging for debugging
    logger.info(f"Attempting to send WhatsApp message to {to_number}")
    logger.info(f"Message type: {media_type}")
    logger.info(f"JSON payload: {json.dumps(json_data, indent=2)}")
    logger.info(f"Token length: {len(WHATSAPP_TOKEN) if WHATSAPP_TOKEN else 0}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
            logger.info(f"Making request to URL: {url}")
            
            response = await client.post(
                url,
                headers=headers,
                json=json_data,
            )
            
            logger.info(f"WhatsApp API response status: {response.status_code}")
            
            response_text = response.text
            logger.info(f"WhatsApp API response body: {response_text}")
            
            # Try to parse as JSON for better logging
            try:
                response_json = response.json()
                logger.info(f"WhatsApp API response JSON: {json.dumps(response_json, indent=2)}")
            except:
                logger.info("Response is not valid JSON")
            
            response.raise_for_status()
            logger.info("Message sent successfully!")
            return True
            
        except httpx.TimeoutException as e:
            logger.error(f"WhatsApp API timeout error: {e}")
            return False
        except httpx.HTTPStatusError as e:
            logger.error(f"WhatsApp API HTTP error: {e.response.status_code}")
            logger.error(f"Error response text: {e.response.text}")
            logger.error(f"Error response headers: {dict(e.response.headers)}")
            try:
                error_json = e.response.json()
                logger.error(f"Error response JSON: {json.dumps(error_json, indent=2)}")
            except:
                logger.error("Error response is not valid JSON")
            return False
        except httpx.RequestError as e:
            logger.error(f"WhatsApp API request error: {e}")
            logger.error(f"Request URL: {e.request.url if e.request else 'Unknown'}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending WhatsApp message: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

async def send_interactive_buttons(to_number: str, body: str, buttons: List[dict | str], header_text: str = None) -> bool:
    """Send interactive button message via WhatsApp API."""
    
    # WhatsApp interactive buttons have a 1024 character limit
    MAX_INTERACTIVE_LENGTH = 1024
    if len(body) > MAX_INTERACTIVE_LENGTH:
        await send_whatsapp_message(to_number, message_text=body)
        body = "Select an option:"
    
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    action_buttons = []
    for btn in buttons:
        if isinstance(btn, str):
            action_buttons.append({"type": "reply", "reply": {"id": f"btn_{btn.lower()}", "title": btn[:20]}})
        else:
            action_buttons.append({"type": "reply", "reply": {"id": btn["id"], "title": btn["title"][:20]}})

    json_data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": action_buttons[:3]}
        }
    }
    
    if header_text and header_text.strip():
        json_data["interactive"]["header"] = {"type": "text", "text": header_text[:60]}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
            response = await client.post(url, headers=headers, json=json_data)
            if response.status_code != 200:
                # Log the body, not just the status: WhatsApp puts the useful
                # part in there (e.g. code 131047 = outside the 24h window and a
                # template is required). Without this you cannot tell a rejected
                # send from a network blip.
                logger.error(
                    f"Interactive send to {to_number} failed: "
                    f"{response.status_code} {response.text}"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"Error sending interactive buttons to {to_number}: {e}")
            return False

async def send_interactive_list(to_number: str, data: Dict[str, Any]) -> bool:
    """Send interactive list message (radio-style) via WhatsApp API."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    
    rows = []
    for row in data.get("rows", []):
        rows.append({
            "id": row["id"],
            "title": row["title"][:24],
            "description": row.get("description", "")[:72]
        })
        
    json_data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": data.get("body", "")},
            "footer": {"text": data.get("footer", "")},
            "action": {
                "button": data.get("button_label", "Select"),
                "sections": [
                    {
                        "title": data.get("section_title", "Options"),
                        "rows": rows
                    }
                ]
            }
        }
    }

    if data.get("header"):
        json_data["interactive"]["header"] = {"type": "text", "text": data.get("header", "")[:60]}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
            response = await client.post(url, headers=headers, json=json_data)
            response.raise_for_status()
            logger.info("Interactive list sent successfully!")
            return True
        except Exception as e:
            logger.error(f"Error sending interactive list: {e}")
            return False

def _lang_buttons(current_language: str = "english") -> list:
    """Return 3 language buttons — always the languages OTHER than the current one."""
    all_langs = [
        {"id": "lang_en", "title": "English"},
        {"id": "lang_ha", "title": "Hausa"},
        {"id": "lang_ig", "title": "Igbo"},
        {"id": "lang_yo", "title": "Yoruba"},
    ]
    lang_key = {"lang_en": "english", "lang_ha": "hausa", "lang_ig": "igbo", "lang_yo": "yoruba"}
    others = [b for b in all_langs if lang_key[b["id"]] != current_language]
    return others[:3]


async def process_audio_message(message: Dict, language: str = "english") -> str:
    """Process audio message and return transcribed text."""
    try:
        audio_id = message["audio"]["id"]
        audio_bytes = await download_media(audio_id)
        
        stt_service = get_stt_service(language)
        if not stt_service:
            raise Exception(f"STT service for {language} not initialized")
            
        transcribed_text = await stt_service.transcribe(audio_bytes)
        logger.info(f"Transcribed audio ({language}): {transcribed_text}")
        return transcribed_text
    except Exception as e:
        logger.error(f"Error processing audio in {language}: {e}")
        raise
@app.api_route("/whatsapp", methods=["GET", "POST"])
async def whatsapp_handler(request: Request) -> Response:
    """
    Lightweight handler that ACKs Facebook immediately and
    delegates the real work to a fire-and-forget background task.
    """
    # ---------- GET (webhook verification) ----------
    if request.method == "GET":
        params = request.query_params
        if params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN:
            return Response(content=params.get("hub.challenge"), status_code=200)
        return Response(content="Verification token mismatch", status_code=403)

    # ---------- POST (incoming message) -------------
    await validate_whatsapp_hmac(request)
    try:
        data = await request.json()
    except Exception:
        logger.warning("Malformed JSON received")
        return Response(status_code=400)

    # Extract the message envelope
    try:
        value = data["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
        if not messages:  # ignore status/delivery receipts
            return Response(status_code=200)
        message = messages[0]
        from_number = message["from"]
        
        # Extract user's name from contacts
        contacts = value.get("contacts", [])
        user_name = "there"  # default
        if contacts and len(contacts) > 0:
            profile = contacts[0].get("profile", {})
            user_name = profile.get("name", "there")
        
        # Add user name to message for background processing
        message["_user_name"] = user_name
        
    except (KeyError, IndexError):
        logger.warning("Missing expected fields in webhook payload")
        return Response(status_code=200)

    message_id = message.get("id")

    # ✅ FIRE-AND-FORGET: Schedule heavy work WITHOUT waiting
    task = asyncio.create_task(
        process_whatsapp_message(message, from_number)
    )
    
    # Add exception handler to prevent "Task exception was never retrieved" warnings
    def handle_task_exception(task_obj):
        try:
            task_obj.result()
        except Exception as e:
            logger.error(f"❌ Background task failed for {from_number}: {e}", exc_info=True)
    
    task.add_done_callback(handle_task_exception)
    
    # ✅ IMMEDIATE RESPONSE: ACK Facebook instantly
    logger.info(f"⚡ Webhook acknowledged immediately for message {message_id} from {from_number}")
    return Response(status_code=200)

async def process_whatsapp_message(message: dict, from_number: str):
    """
    Full pipeline:
    - download media (if any)
    - STT / vision / agent logic via MICROSERVICE
    - TTS (optional)
    - send reply via WhatsApp API
    """
    refresher_task = None
    stop_typing = None
    try:
        user_name = (
            message.get("_user_name") or
            "there"
        )
        message_type = message.get("type", "unknown")
        message_id = message.get("id")
        thread_id = from_number.replace("+", "")
        logger.info(f"[bg] Processing {message_type} from {from_number} (ID: {message_id})")

        content_for_agent = []
        should_respond_with_audio = False

        # ---------- INTERACTIVE ----------
        if message_type == "interactive":
            interactive = message["interactive"]
            itype = interactive["type"]
            if itype == "button_reply":
                button_reply = interactive["button_reply"]
                button_id = button_reply.get("id")

                if button_id == "approve_terms":
                    set_terms_accepted(thread_id)
                    logger.info(f"[bg] ✅ Terms accepted: {from_number}")
                    approve_lang = get_user_language(thread_id)
                    await send_interactive_buttons(from_number, JOURNEY_ONBOARDING_MESSAGE, _lang_buttons(approve_lang))
                    return

                # Daily-tips opt-out. Handled here (before the terms gate below)
                # so that tapping Stop always works and can never be swallowed by
                # the onboarding flow — a user must be able to opt out of a
                # broadcast at any time, regardless of their onboarding state.
                if button_id == TIP_STOP_BUTTON["id"]:
                    set_tips_opted_out(thread_id, True)
                    stop_lang = get_user_language(thread_id)
                    logger.info(f"[bg] 🛑 Daily tips opt-out: {from_number} ({stop_lang})")
                    await send_whatsapp_message(
                        from_number,
                        TIP_OPT_OUT_CONFIRMATIONS.get(stop_lang, TIP_OPT_OUT_CONFIRMATIONS["english"])
                    )
                    return

                if button_id in ("lang_en", "lang_ha", "lang_ig", "lang_yo"):
                    lang_map = {"lang_en": "english", "lang_ha": "hausa", "lang_ig": "igbo", "lang_yo": "yoruba"}
                    selected_lang = lang_map[button_id]
                    set_user_language(thread_id, selected_lang)
                    confirms = {
                        "english": "✅ This conversation will now be in English. How can I help you?",
                        "hausa": "✅ Wannan tattaunawar za ta kasance da Hausa yanzu. Ta yaya zan taimaka miki?",
                        "igbo": "✅ Mkparịta ụka a ga-adị n'asụsụ Igbo ugbu a. Kedu ka m ga-esi nyere gị aka?",
                        "yoruba": "✅ Ìbánisọ̀rọ̀ yìí yóò máa lọ ní èdè Yorùbá báyìí. Kí ni mo lè ràn ọ́ lọ́wọ́ rẹ̀?"
                    }
                    confirm_text = confirms[selected_lang]
                    buttons = _lang_buttons(selected_lang)
                    await send_interactive_buttons(from_number, confirm_text, buttons)
                    return
                else:
                    button_title = button_reply.get("title", "")
                    content_for_agent.append({
                        "type": "text",
                        "text": f"Interactive: {button_title}"
                    })

            elif itype == "list_reply":
                list_reply = interactive["list_reply"]
                row_id = list_reply.get("id")
                item_title = list_reply.get("title", "")
                content_for_agent.append({
                    "type": "text",
                    "text": f"Selection: {item_title}"
                })
            else:
                logger.info(f"Unsupported interactive type: {itype}")
                return

        # ── Terms & Conditions gate ──────────────────────────────────
        # Runs for every message type. The approve_terms button click itself
        # is handled above (before this point) and returns early, so it never
        # hits this gate. Anyone who hasn't approved yet gets the terms
        # message resent, no matter what they send, until they tap Approve.
        if not has_accepted_terms(thread_id):
            logger.info(f"[bg] 📄 Terms not yet accepted — sending/resending to {from_number}")
            await send_interactive_buttons(
                from_number,
                TERMS_INTRO_MESSAGE,
                [{"id": "approve_terms", "title": "Approve"}]
            )
            return
        # ────────────────────────────────────────────────────────────

        # Get user's preferred language
        user_language = get_user_language(thread_id)
        logger.info(f"[bg] User language: {user_language}")

        # ---------- TEXT ----------
        if message_type == "text":
            raw_text = message["text"]["body"]

            # ── Hardcoded journey capture (1/2/3 reply to onboarding) ───
            # (This runs after the terms gate above, so it's only reachable
            # once the user has already approved the Terms/Privacy step.)
            stripped = raw_text.strip()
            journey_map = {"1": "pregnant", "2": "breastfeeding", "3": "caregiver"}
            if stripped in journey_map:
                journey_choice = journey_map[stripped]
                set_user_journey(thread_id, journey_choice)
                logger.info(f"[bg] ✅ Journey saved: {from_number} → {journey_choice}")
                if journey_choice == "pregnant":
                    confirm_text = (
                        "Great! I'll make sure my advice supports you and your growing baby.\n\n"
                        "I can help you:\n"
                        "• Find out what nutrients are in your food\n"
                        "• Spot what's missing from a meal and suggest local foods to add\n"
                        "• Plan meals using foods you already have at home\n\n"
                        "Send me a photo of your meal to get started, or just ask a question. 😊"
                    )
                elif journey_choice == "breastfeeding":
                    confirm_text = (
                        "Great! I'll focus on foods that support your milk and keep you and your baby healthy.\n\n"
                        "I can help you:\n"
                        "• Find out what nutrients are in your food\n"
                        "• Spot what's missing from a meal and suggest local foods to add\n"
                        "• Track your baby's weight to spot early signs of malnutrition\n"
                        "• Plan meals using foods you already have at home\n\n"
                        "Send me a photo of your meal to get started, or just ask a question. 😊"
                    )
                else:  # caregiver
                    confirm_text = (
                        "Great! I'll help you choose the right foods for your child's age and growth.\n\n"
                        "I can help you:\n"
                        "• Find out what nutrients are in your child's food\n"
                        "• Spot what's missing from a meal and suggest local foods to add\n"
                        "• Choose the right foods for your child's age\n"
                        "• Track your child's weight to spot early signs of malnutrition\n"
                        "• Plan meals using foods you already have at home\n\n"
                        "Send me a photo of your child's meal to get started, or just ask a question. 😊"
                    )
                await send_whatsapp_message(from_number, confirm_text)
                return
            # ────────────────────────────────────────────────────────────

            # NOTE: We no longer translate the incoming text via NLLB. Gemma now
            # receives the user's raw message directly (see [Respond in X] directive
            # in unified_agent.py), understands it natively, and replies natively —
            # so the NLLB call here was just adding latency/cost for a translation
            # that unified_agent.py ends up discarding anyway.
            content_for_agent.append({
                "type": "text",
                "text": raw_text,
                "original_text": raw_text
            })

        # ---------- AUDIO ----------
        elif message_type == "audio":
            try:
                # Transcribe in correct language
                transcribed_text = await process_audio_message(message, language=user_language)
                
                # NOTE: no NLLB translation here either — same reasoning as text
                # above. Gemma gets the raw transcript directly.
                agent_input_text = transcribed_text
                content_for_agent.append({
                    "type": "text",
                    "text": f"[Voice]: {agent_input_text}",
                    "original_text": transcribed_text
                })
                should_respond_with_audio = True
            except Exception as e:
                logger.error(f"[bg] STT failed: {e}")
                logger.error(f"Error details: STT failed — language: {user_language}")
                await send_interactive_buttons(from_number, "Sorry, I couldn't process that voice message. Please try again.", _lang_buttons(user_language))
                return

        # ---------- IMAGE (FOOD RECOGNITION WORKFLOW) ----------
        elif message_type == "image":
            caption = message.get("image", {}).get("caption", "")
            image_media_id = message["image"]["id"]
            image_bytes = None
            _img_max_retries = 3

            for _attempt in range(1, _img_max_retries + 1):
                try:
                    logger.info(f"[bg] 📥 Downloading food image (attempt {_attempt}/{_img_max_retries})")
                    image_bytes = await download_media(image_media_id)
                    if not image_bytes:
                        raise ValueError("Downloaded image is empty")
                    logger.info(f"[bg] ✅ Image downloaded ({len(image_bytes)} bytes)")
                    break
                except Exception as dl_err:
                    logger.warning(f"[bg] Image download failed (attempt {_attempt}): {dl_err}")
                    if _attempt < _img_max_retries:
                        await asyncio.sleep(1.5 * _attempt)
                    else:
                        logger.error(f"[bg] Image download failed after {_img_max_retries} attempts: {dl_err}")
                        logger.error(f"Error details: Food image processing failed — user: {from_number}")
                        await send_interactive_buttons(from_number, "Sorry, I couldn't process that image. Please try again.", _lang_buttons(user_language))
                        return

            try:
                # Step 2: Try food recognition model (optional — fallback to Gemma if it fails)
                logger.info("[bg] 🔍 Sending image to food recognition model")
                foods_hint = ""
                try:
                    food_recognition_result = await recognize_food_from_image(image_bytes)
                    detected_foods = food_recognition_result.get("detected", []) if "error" not in food_recognition_result else []
                    if detected_foods:
                        foods_hint = ", ".join([f"{item['food']} ({item['confidence']:.0%} confidence)" for item in detected_foods])
                        logger.info(f"[bg] ✅ Detected foods: {foods_hint}")
                    else:
                        logger.warning("[bg] Food recognition returned no results — Gemma will analyse directly")
                except Exception as rec_err:
                    logger.warning(f"[bg] Food recognition unavailable ({rec_err}) — sending image to Gemma directly")

                # Step 3: Send image (+ optional hints) to Gemma
                data_uri = convert_to_data_uri(image_bytes, "image/jpeg")
                # Strip confidence levels — only send food names
                if foods_hint:
                    food_names_only = ", ".join([item['food'] for item in detected_foods])
                    hint_text = (
                        f"\n\n[System note: A food detection model suggested these foods may be in the image: {food_names_only}. "
                        f"This list can be wrong. Always look at the image yourself and use your own judgement. "
                        f"Do NOT just repeat what the detector says — verify it with your own vision before responding to the user.]"
                    )
                else:
                    hint_text = ""
                content_for_agent.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri}
                })
                content_for_agent.append({
                    "type": "text",
                    "text": f"[Food Image] Caption: {caption}{hint_text}\n\nLook at the image and confirm what foods you can see. Respond naturally: 'I can see [food1], [food2], and [food3] in your meal. Is that correct?' Do NOT mention the model or that you received an image."
                })

            except Exception as e:
                logger.error(f"[bg] Image processing failed: {e}")
                logger.error(f"Error details: Food image processing failed — user: {from_number}")
                await send_interactive_buttons(from_number, "Sorry, I couldn't process that image. Please try again.", _lang_buttons(user_language))
                return

        # ---------- UNSUPPORTED ----------
        else:
            # Unsupported type with Language List
            await send_interactive_buttons(from_number, "I can process text, voice messages, and images.", _lang_buttons(user_language))
            return

        # ── Inject user journey profile into agent context ─────────────
        user_profile = get_user_journey(thread_id)
        if user_profile.get("journey"):
            journey_label = {
                "pregnant": "This user is pregnant.",
                "breastfeeding": "This user is a breastfeeding/nursing mother.",
                "caregiver": "This user is a parent or caregiver of a young child.",
            }.get(user_profile["journey"], "")
            if journey_label:
                prefix = f"[User profile: {journey_label}]\n\n"
                for part in content_for_agent:
                    if isinstance(part, dict) and part.get("type") == "text":
                        part["text"] = prefix + part["text"]
                        # The profile MUST go on "original_text" too. English mode
                        # sends "text", but multilingual mode sends "original_text"
                        # (the user's untranslated message), so prefixing only
                        # "text" dropped the journey for every Hausa/Igbo/Yoruba
                        # user: the model never learned she was pregnant, and fell
                        # back to asking "is this meal for you or your baby?".
                        if "original_text" in part:
                            part["original_text"] = prefix + part["original_text"]
                        break
        # ────────────────────────────────────────────────────────────────

        # ---------- SEND TYPING INDICATOR ----------
        if message_id:
            await send_typing_indicator(message_id)

        # Set up refresher if needed (for long-running voice responses)
        if should_respond_with_audio:
            stop_typing = asyncio.Event()
            async def typing_refresher():
                while not stop_typing.is_set():
                    if not await send_typing_indicator(message_id):
                        logger.warning("Failed to refresh typing indicator")
                    await asyncio.sleep(20)
            refresher_task = asyncio.create_task(typing_refresher())

        # ---------- CALL UNIFIED AGENT DIRECTLY ----------
        # Extract user's question from content_for_agent
        user_question = ""
        original_content = None
        for item in content_for_agent:
            if item.get("type") == "text":
                user_question = item.get("text", "")
                original_content = item.get("original_text")
                break

        logger.info(f"[bg] 📤 Calling unified agent for thread {thread_id}")

        # Sentence boundary regex used for streaming TTS pipeline
        _sent_re = re.compile(r'(?<=[.!?])\s+')

        # Holds asyncio.Tasks for concurrent TTS
        pre_tts_tasks: list = []
        _already_translated = False  # set True when the voice streaming pipeline below already handled NLLB translation
        dashboard_conversation_id = None  # set after microservice logs the conversation (currently unused/dead — kept for future use)

        try:
            if should_respond_with_audio and user_language != "english":
                    # ── STREAMING path: LLM (English) → NLLB (sentence) → TTS tasks ──
                    logger.info(f"[bg] 🌊 Streaming: LLM → NLLB → TTS ({user_language} voice)")
                    response_text = ""
                    en_buf = ""
                    local_sentences: list = []
                    _translator = get_translator()
                    _tts_svc = get_tts_service(user_language)
                    if user_language == "hausa":
                        _voice_param = HAUSA_TTS_VOICE
                    elif user_language == "igbo":
                        _voice_param = IGBO_TTS_VOICE
                    else:
                        _voice_param = YORUBA_TTS_VOICE

                    async def _translate_sentence(sentence: str) -> str:
                        if user_language == "hausa":
                            return await asyncio.to_thread(_translator.english_to_hausa, sentence)
                        elif user_language == "igbo":
                            return await asyncio.to_thread(_translator.english_to_igbo, sentence)
                        else:
                            return await asyncio.to_thread(_translator.english_to_yoruba, sentence)

                    # Call unified agent with streaming (Gemma streams in English)
                    async for chunk in unified_agent.get_response_stream(
                        content=user_question,
                        thread_id=thread_id,
                        message_type="voice",
                        language=user_language,
                        original_content=original_content
                    ):
                        response_text += chunk
                        en_buf += chunk
                        parts = _sent_re.split(en_buf)
                        if len(parts) > 1:
                            for sentence in parts[:-1]:
                                sentence = sentence.strip()
                                if len(sentence) <= 3:
                                    continue
                                logger.info(f"[bg] 🌍 Translating: {sentence[:60]}")
                                local_sent = (await _translate_sentence(sentence)).strip()
                                if local_sent:
                                    local_sentences.append(local_sent)
                                    tts_input = yoruba_numbers_to_words(local_sent) if user_language == "yoruba" else local_sent
                                    pre_tts_tasks.append(asyncio.create_task(
                                        asyncio.to_thread(_tts_svc.synthesize_sync, tts_input, _voice_param)
                                    ))
                            en_buf = parts[-1]

                    # Flush leftover buffer
                    if en_buf.strip() and len(en_buf.strip()) > 3:
                        local_sent = (await _translate_sentence(en_buf.strip())).strip()
                        if local_sent:
                            local_sentences.append(local_sent)
                            tts_input = yoruba_numbers_to_words(local_sent) if user_language == "yoruba" else local_sent
                            pre_tts_tasks.append(asyncio.create_task(
                                asyncio.to_thread(_tts_svc.synthesize_sync, tts_input, _voice_param)
                            ))

                    # Final response_text is the translated (native-language) text —
                    # this is what gets sent as the WhatsApp text alongside the audio.
                    response_text = " ".join(local_sentences) if local_sentences else response_text
                    _already_translated = True
                    logger.info(f"[bg] ✅ Streamed+translated {len(local_sentences)} sentences, {len(pre_tts_tasks)} TTS tasks queued")

            elif should_respond_with_audio and user_language == "english":
                    # ── STREAMING path: LLM streams → TTS tasks start immediately ─
                    logger.info("[bg] 🌊 Streaming agent + concurrent TTS (English voice)")
                    response_text = ""
                    text_buf = ""
                    tts_svc = get_tts_service("english")

                    # Call unified agent with streaming
                    async for chunk in unified_agent.get_response_stream(
                        content=user_question,
                        thread_id=thread_id,
                        message_type="voice",
                        language=user_language,
                        original_content=original_content
                    ):
                        response_text += chunk
                        text_buf += chunk
                        parts = _sent_re.split(text_buf)
                        if len(parts) > 1:
                            for sentence in parts[:-1]:
                                sentence = sentence.strip()
                                if len(sentence) > 3:
                                    logger.info(f"[bg] 🎤 TTS task launched: {sentence[:60]}")
                                    pre_tts_tasks.append(
                                        asyncio.create_task(
                                            asyncio.to_thread(tts_svc.synthesize_sync, sentence, ENGLISH_TTS_VOICE)
                                        )
                                    )
                            text_buf = parts[-1]

                    # Flush remaining buffer
                    if text_buf.strip() and len(text_buf.strip()) > 3:
                        pre_tts_tasks.append(
                            asyncio.create_task(
                                asyncio.to_thread(tts_svc.synthesize_sync, text_buf.strip(), ENGLISH_TTS_VOICE)
                            )
                        )
                    if not response_text:
                        response_text = "I received your message. How can I help you?"

                    # Fallback: if streaming returned error, retry with non-streaming
                    _error_phrases = ("sorry, something went wrong", "i'm having trouble", "please try again")
                    if len(response_text) < 60 and any(p in response_text.lower() for p in _error_phrases):
                        logger.warning("[bg] ⚠️ Streaming returned error response, falling back to non-streaming")
                        pre_tts_tasks.clear()
                        response_text = await unified_agent.get_response(
                            content=user_question,
                            thread_id=thread_id,
                            message_type="voice",
                            language=user_language,
                            original_content=original_content
                        )

                    logger.info(f"[bg] ✅ Streamed {len(response_text)} chars, {len(pre_tts_tasks)} TTS tasks running")

            else:
                    # ── Standard non-streaming path ──────────────────────────────
                    response_text = await unified_agent.get_response(
                        content=content_for_agent,
                        thread_id=thread_id,
                        message_type="voice" if should_respond_with_audio else "text",
                        language=user_language,
                        original_content=original_content
                    )
                    if not response_text:
                        logger.error("[bg] ❌ Agent returned empty response")
                        response_text = "I received your message. How can I help you?"
                    logger.info(f"[bg] ✅ Agent response: {response_text[:100]}...")

        except Exception as e:
            logger.error(f"[bg] ❌ Error calling agent: {e}")
            logger.error(f"Error details:Agent call failed — user: {from_number}, lang: {user_language}")
            await send_interactive_buttons(from_number, "Something went wrong. Please try again.", _lang_buttons(user_language))
            return

        # Translate Gemma's English response into the user's language via NLLB.
        # (For voice, this is skipped here because the streaming voice path below
        # already does its own per-sentence NLLB translation as it streams — see
        # `_already_translated`.)
        if user_language != "english" and not _already_translated:
            logger.info(f"🔄 Translating agent response to {user_language}...")
            try:
                translator = get_translator()
                # Split by newline to preserve list/paragraph structure
                # Protect numbered list markers (e.g. "1.") from being translated
                _num_re = re.compile(r'^(\s*\d+\.\s*)')
                lines = response_text.split('\n')
                translated_lines = []
                for line in lines:
                    if not line.strip():
                        translated_lines.append(line)
                        continue
                    num_match = _num_re.match(line)
                    prefix = num_match.group(1) if num_match else ""
                    text_part = line[len(prefix):].strip()
                    if not text_part:
                        translated_lines.append(line)
                        continue
                    if user_language == "hausa":
                        translated = translator.english_to_hausa(text_part)
                    elif user_language == "igbo":
                        translated = translator.english_to_igbo(text_part)
                    elif user_language == "yoruba":
                        translated = translator.english_to_yoruba(text_part)
                    else:
                        translated = text_part
                    translated_lines.append(prefix + translated)
                response_text = "\n".join(translated_lines)
                logger.info(f"✅ Final {user_language} response (via NLLB): {response_text[:100]}...")
            except Exception as e:
                logger.error(f"❌ Translation back failed: {e}")
        else:
            logger.info(f"✅ Final {user_language} response: {response_text[:100]}...")

        # Clean up markdown for WhatsApp display
        response_text = clean_whatsapp_message(response_text)

        # === SAVE CONVERSATION TO MONGODB (Old feedback system for backward compatibility) ===
        store_conversation(
            thread_id=thread_id,
            user_name=user_name,
            question=user_question,
            response=response_text,
            feedback=None  # No feedback yet
        )

        # Set pending response for feedback (store both question and response)
        store_pending_response(thread_id, user_question, response_text)

        # ---------- SEND REPLY ----------
        # Stop typing refresher before sending reply
        if refresher_task:
            stop_typing.set()
            refresher_task.cancel()
            try:
                await asyncio.wait_for(refresher_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            refresher_task = None
        
        # Send audio if voice message
        if should_respond_with_audio:
            try:
                logger.info(f"[bg] 🔊 Generating regional audio for: {response_text[:50]}...")
                
                # Pick correct TTS
                tts_service = get_tts_service(user_language)
                if not tts_service:
                    raise Exception(f"TTS service for {user_language} not initialized")
                
                # Check for regional voice param
                voice_param = "regional_voice"
                if user_language == "english":
                    voice_param = ENGLISH_TTS_VOICE
                elif user_language == "hausa":
                    voice_param = HAUSA_TTS_VOICE
                elif user_language == "igbo":
                    voice_param = IGBO_TTS_VOICE
                elif user_language == "yoruba":
                    voice_param = YORUBA_TTS_VOICE
                
                if pre_tts_tasks:
                    # English: await all concurrent tasks launched during LLM streaming
                    logger.info(f"[bg] ⏳ Awaiting {len(pre_tts_tasks)} concurrent TTS tasks...")
                    audio_parts = []
                    for task in pre_tts_tasks:
                        try:
                            audio_parts.append(await task)
                        except Exception as tts_err:
                            logger.warning(f"[bg] ⚠️ TTS task failed: {tts_err}")
                    audio_bytes = await asyncio.to_thread(_concat_mp3_chunks, audio_parts)
                    if not audio_bytes:
                        raise Exception("All TTS tasks produced no audio")
                    logger.info(f"[bg] ✅ Concatenated {len(audio_parts)} chunks → {len(audio_bytes)} bytes")
                else:
                    # Non-English or fallback: single TTS call on full text
                    sentences = [s.strip() for s in _sent_re.split(response_text) if len(s.strip()) > 3]
                    if not sentences:
                        sentences = [response_text]
                    logger.info(f"[bg] 🎤 Running parallel TTS for {len(sentences)} sentences ({user_language})")
                    tasks = [
                        asyncio.create_task(asyncio.to_thread(tts_service.synthesize_sync, s, voice_param))
                        for s in sentences
                    ]
                    audio_parts = []
                    for t in tasks:
                        try:
                            audio_parts.append(await t)
                        except Exception as tts_err:
                            logger.warning(f"[bg] ⚠️ TTS sentence failed: {tts_err}")
                    audio_bytes = await asyncio.to_thread(_concat_mp3_chunks, audio_parts)
                    if not audio_bytes:
                        raise Exception("All TTS sentences produced no audio")

                logger.info(f"[bg] ✅ Audio generated: {len(audio_bytes)} bytes")
                
                media_id = await upload_media_to_whatsapp(audio_bytes, "audio/mpeg")
                if media_id:
                    logger.info(f"[bg] ✅ Media uploaded successfully. Sending audio with media_id: {media_id}")
                    
                    # Wait for WhatsApp to process the uploaded media before sending
                    await asyncio.sleep(0.5)
                    
                    success = await send_whatsapp_message(from_number, media_id=media_id, media_type="audio")
                    if success:
                        logger.info(f"[bg] ✅ Audio message sent successfully to WhatsApp API!")
                        # Just send a small confirmation/language change option
                        await send_interactive_buttons(from_number, ".", _lang_buttons(user_language))
                        return

                # Standard text response + language buttons
                if len(response_text) > 1024:
                    await send_whatsapp_message(from_number, message_text=response_text)
                    await send_interactive_buttons(from_number, "Switch language:", _lang_buttons(user_language))
                else:
                    await send_interactive_buttons(from_number, response_text, _lang_buttons(user_language))

            except Exception as e:
                logger.error(f"[bg] ❌ Operation failed: {e}", exc_info=True)
                logger.error(f"Error details:TTS/audio upload failed — user: {from_number}, lang: {user_language}")
                await send_interactive_buttons(from_number, response_text, _lang_buttons(user_language))
        else:
            # Standard text response + language buttons
            if len(response_text) > 1024:
                await send_whatsapp_message(from_number, message_text=response_text)
                await send_interactive_buttons(from_number, "Switch language:", _lang_buttons(user_language))
            else:
                await send_interactive_buttons(from_number, response_text, _lang_buttons(user_language))

    except Exception as e:
        logger.exception("[bg] ❌ Unhandled exception in background task")
        logger.error(f"Error details:Unhandled exception in message processing — user: {from_number}")
        try:
            lang = get_user_language(thread_id)
            await send_interactive_buttons(from_number, "Something went wrong on our side. Please try again later.", _lang_buttons(lang))
        except Exception:
            pass
    finally:
        # Cleanup typing refresher
        if refresher_task:
            if stop_typing:
                stop_typing.set()
            refresher_task.cancel()
            try:
                await asyncio.wait_for(refresher_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
@app.get("/")
async def root():
    return {"message": "WhatsApp Voice Retriever Agent is running!"}


# ── Website feedback form ────────────────────────────────────────────────────
# Backs the /feedback page on the marketing site. Submissions are stored in
# MongoDB alongside the WhatsApp data instead of a third-party form service,
# and read back through a key-protected dashboard.

@app.post("/feedback")
async def submit_feedback(request: Request):
    """Receive a submission from the website feedback form."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")

    # X-Forwarded-For is set by the nginx reverse proxy in front of this app;
    # fall back to the socket peer when running without a proxy.
    forwarded = request.headers.get("x-forwarded-for", "")
    source_ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else ""
    )

    saved = save_site_feedback(payload, source_ip=source_ip)
    if not saved:
        raise HTTPException(status_code=400, detail="Could not save feedback")
    return {"ok": True}


_dashboard_auth = HTTPBasic(realm="ChopBeta Feedback")


def _require_login(credentials: HTTPBasicCredentials = Depends(_dashboard_auth)) -> str:
    """Guard the dashboard and export with a browser login prompt.

    Submissions contain phone numbers, so if no password is configured we
    refuse to serve rather than defaulting to open. Both halves are compared
    with compare_digest, and both are always compared even when the username
    is already wrong, so response timing doesn't reveal which part matched.
    """
    if not FEEDBACK_DASHBOARD_PASSWORDS:
        raise HTTPException(
            status_code=503,
            detail="Dashboard not configured — set FEEDBACK_DASHBOARD_PASSWORD in .env",
        )
    user_ok = secrets.compare_digest(credentials.username, FEEDBACK_DASHBOARD_USER)
    # Any configured password is accepted. Every candidate is compared (no early
    # break) so the time taken doesn't reveal which one matched.
    pass_ok = False
    for candidate in FEEDBACK_DASHBOARD_PASSWORDS:
        if secrets.compare_digest(credentials.password, candidate):
            pass_ok = True
    if not (user_ok and pass_ok):
        logger.warning(f"⚠️ Failed dashboard login attempt for user {credentials.username!r}")
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": 'Basic realm="ChopBeta Feedback"'},
        )
    return credentials.username


@app.get("/feedback/data")
async def feedback_data(user: str = Depends(_require_login)):
    """Feedback rows + headline stats, for the dashboard page on the website.

    The dashboard itself lives in the front-end (frontend-react, served from
    Firebase/Netlify); this endpoint is the data behind it.
    """
    rows = get_site_feedback()
    return {"rows": rows_to_json(rows), "stats": get_feedback_stats(rows)}


@app.get("/feedback/check-login")
async def feedback_check_login(user: str = Depends(_require_login)):
    """Lets the dashboard's login screen verify credentials before showing the
    dashboard, so a wrong password gives a clear message instead of an empty
    table."""
    return {"ok": True, "user": user}


@app.get("/feedback/export.csv")
async def feedback_export(user: str = Depends(_require_login)):
    """Download every submission as CSV (opens in Excel or Google Sheets)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=rows_to_csv(get_site_feedback(limit=10000)),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="chopbeta-feedback-{stamp}.csv"',
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 4000)))