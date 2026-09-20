

import asyncio
import glob
import logging
import os
import re
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv()

import alerts
import reports
import store
import vision
from anonymiser import scrub_text, strip_image_metadata
from config import (
    DASHBOARD_PASSWORDS,
    DASHBOARD_USER,
    DASHBOARD_ALLOWED_ORIGINS,
    EMERGENCY_NUMBERS,
    LANGUAGES,
    get_language,
    language_by_button_id,
    language_button_ids,
)
from hmac_validator import validate_whatsapp_hmac
from nllb_translator import numbers_to_words
from services import get_stt_service, get_translator, get_tts_service, get_voice
from unified_agent import is_distress_signal, unified_agent

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN")
GRAPH_API = "https://graph.facebook.com/v20.0"

if not all([WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN]):
    logger.error("Missing one or more required WhatsApp environment variables.")
    raise SystemExit(1)


# ── ffmpeg / audio joining ───────────────────────────────────────────────────

_ffmpeg_cache = None


def _find_ffmpeg():
    """Locate ffmpeg once, including a winget install on a Windows dev machine."""
    global _ffmpeg_cache
    if _ffmpeg_cache is not None:
        return _ffmpeg_cache

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        _ffmpeg_cache = (ffmpeg, shutil.which("ffprobe"))
        return _ffmpeg_cache

    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        pattern = os.path.join(appdata, "Microsoft", "WinGet", "Packages", "**", "ffmpeg.exe")
        for found in glob.glob(pattern, recursive=True):
            probe = found.replace("ffmpeg.exe", "ffprobe.exe")
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + os.path.dirname(found)
            logger.info(f"Found ffmpeg at: {found}")
            _ffmpeg_cache = (found, probe if os.path.exists(probe) else None)
            return _ffmpeg_cache

    logger.warning("ffmpeg not found — MP3 joining will fall back to a raw concatenation")
    _ffmpeg_cache = (None, None)
    return _ffmpeg_cache


def _concat_mp3(chunks: list) -> bytes:
    """Join per-sentence MP3s into one playable file."""
    chunks = [c for c in chunks if c]
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
        # A raw join usually still plays; players tolerate concatenated frames.
        logger.warning(f"⚠️ pydub join failed ({e}) — using a raw concatenation")
        return b"".join(chunks)


UI_STRINGS = {
    "terms": {
        "english": (
            "👋 *Welcome to ComGuard*\n\n"
            "I help you report and stay safe from two kinds of trouble:\n\n"
            "🌊 *Emergencies* — flooding, fire, a collapsed road or bridge, a blocked route\n"
            "🚨 *Scams* — fake public notices, unofficial 'tax' collectors, unlawful checkpoints, forged receipts\n\n"
            "You can send me a picture / voice of what is happening, and I will help send the report to the right government authority.\n\n"
            "*Your privacy:* your phone number is never attached to a report. "
            "Photo location tags are removed. Your location helps us alert OTHER people "
            "nearby so they can avoid danger areas and stay safe.\n\n"
            "By continuing you agree to the Terms of Use and Privacy Policy.\n\n"
            "Tap *Approve* to begin."
        ),
        "yoruba": (
            "👋 *Kú àbọ̀ sí ComGuard*\n\n"
            "Mo ń ràn ọ́ lọ́wọ́ láti ròyìn kí o sì wà ní àìléwu nínú ohun méjì:\n\n"
            "🌊 *Ewu* — ìkún omi, iná, afárá tàbí ọ̀nà tí ó wó, ọ̀nà tí ó dí\n"
            "🚨 *Ìwà ẹ̀tàn* — ìwé ìkéde èké, àwọn tí ń gba owó orí láìjẹ́ ti ìjọba, "
            "ibùdó ìdálẹ́kun àìtọ́, ìwé ẹ̀rí owó èké\n\n"
            "Fi àwòrán, ohùn, tàbí kọ ohun tí ó ń ṣẹlẹ̀ ránṣẹ́ sí mi, èmi yóò ràn ọ́ lọ́wọ́ "
            "láti fi ròyìn sí ilé-iṣẹ́ ìjọba tí ó tọ́.\n\n"
            "*Àṣírí rẹ:* nọ́mbà rẹ kì í tẹ̀lé ìròyìn rẹ. A ó mú àmì ibi tí àwòrán "
            "rẹ ti wá kúrò. Ibi tí o wà ń ràn wá lọ́wọ́ láti kìlọ̀ fún ÀWỌN ẸLÒMÍRÀN "
            "kí wọ́n lè yẹra fún ewu.\n\n"
            "Tẹ *Approve* láti bẹ̀rẹ̀."
        ),
        "arabic": (
            "👋 *مرحبًا بك في ComGuard*\n\n"
            "أساعدك على الإبلاغ عن نوعين من المشكلات وعلى حماية نفسك:\n\n"
            "🌊 *الطوارئ* — الفيضانات، الحرائق، انهيار طريق أو جسر، طريق مسدود\n"
            "🚨 *الاحتيال* — إشعارات رسمية مزيفة، جباة ضرائب غير رسميين، "
            "نقاط تفتيش غير قانونية، إيصالات مزورة\n\n"
            "أرسل لي صورة أو رسالة صوتية، وسأساعدك في إرسال البلاغ إلى "
            "الجهة الحكومية المختصة.\n\n"
            "*خصوصيتك:* رقم هاتفك لا يُرفق أبدًا بأي بلاغ. تُحذف بيانات موقع "
            "الصور. يساعدنا موقعك على تحذير الآخرين القريبين لتجنب مناطق الخطر "
            "والبقاء آمنين.\n\n"
            "بمتابعتك فإنك توافق على شروط الاستخدام وسياسة الخصوصية.\n\n"
            "اضغط *Approve* للبدء."
        ),
    },
"ready": {
    "english": (
        "✅ You're ready.\n\n"
        "Send a photo of what you see, or a voice note explaining the problem.\n"
        "I will tell you what to do next.\n\n"
        f"If someone is in immediate danger, call {EMERGENCY_NUMBERS} first."
    ),
    "yoruba": (
        "✅ Ó ti ṣetán.\n\n"
        "Fi àwòrán ohun tí o rí ránṣẹ́, tàbí ohùn kan tí ó ṣàlàyé iṣòro náà.\n"
        "Èmi yóò sọ fún ọ ohun tí o ní láti ṣe.\n\n"
        f"Tí ẹnikẹ́ni bá wà nínú ewu lẹ́sẹ̀kẹsẹ̀, kọ́kọ́ pe {EMERGENCY_NUMBERS}."
    ),
    "arabic": (
        "✅ أنت جاهز.\n\n"
        "أرسل صورة لما تراه، أو رسالة صوتية تشرح المشكلة.\n"
        "وسأخبرك بما يجب فعله بعد ذلك.\n\n"
        f"إذا كان أحد في خطر فوري، اتصل أولًا بـ {EMERGENCY_NUMBERS}."
    ),
},
    "need_location": {
        "english": (
            "📍 If it's safe to do so, share your location — tap 📎 then *Location*.\n"
            "This helps us alert OTHER people nearby so they can avoid the area "
            "and stay safe. You can skip this."
        ),
        "yoruba": (
            "📍 Tí ó bá léwu, pín ibi tí o wà — tẹ 📎 lẹ́yìn náà *Location*.\n"
            "Èyí ń ràn wá lọ́wọ́ láti kìlọ̀ fún ÀWỌN ẸLÒMÍRÀN tí ó wà nítòsí "
            "kí wọ́n lè yẹra fún ibẹ̀. O lè fo èyí."
        ),
        "arabic": (
            "📍 إذا كان الأمر آمنًا، شارك موقعك — اضغط 📎 ثم *الموقع*.\n"
            "يساعدنا هذا على تحذير الآخرين القريبين لتجنب المنطقة "
            "والبقاء آمنين. يمكنك تخطي هذه الخطوة."
        ),
    },
    "location_received": {
        "english": (
            "📍 Got your location{place}.\n"
            "It goes to the agency with your report so they know where to respond, "
            "and it lets us warn people nearby to avoid the area."
        ),
        "yoruba": (
            "📍 A ti gba ibi tí o wà{place}.\n"
            "A ó fi ránṣẹ́ pẹ̀lú ìròyìn rẹ kí ilé-iṣẹ́ tó yẹ lè mọ ibi tí wọ́n yóò lọ, "
            "yóò sì jẹ́ kí a kìlọ̀ fún àwọn tí ó wà nítòsí kí wọ́n yẹra fún ibẹ̀."
        ),
        "arabic": (
            "📍 تم استلام موقعك{place}.\n"
            "سيُرسل مع بلاغك حتى تعرف الجهة المختصة أين تتوجه، ويساعدنا على تحذير "
            "من هم قريبون منك لتجنب المنطقة."
        ),
    },
    "report_filed": {
        "english": (
            "📋 Your report is logged as *{report_id}*. Keep that reference.\n"
            "{routing}"
        ),
        "yoruba": (
            "📋 A ti kọ ìròyìn rẹ sílẹ̀ gẹ́gẹ́ bí *{report_id}*. Pa àmì yìí mọ́.\n"
            "{routing}"
        ),
        "arabic": (
            "📋 تم تسجيل بلاغك برقم *{report_id}*. احتفظ بهذا الرقم.\n"
            "{routing}"
        ),
    },
    "routing_sent": {
        "english": "It has been sent to the relevant agency without your phone number.",
        "yoruba": "A ti fi ránṣẹ́ sí ilé-iṣẹ́ tí ó yẹ láì fi nọ́mbà rẹ kún un.",
        "arabic": "تم إرساله إلى الجهة المختصة دون رقم هاتفك.",
    },
    "routing_held": {
        "english": "It is waiting for a dispatcher to review it.",
        "yoruba": "Ó ń dúró de aláṣẹ láti ṣàyẹ̀wò rẹ̀.",
        "arabic": "في انتظار مراجعته من قبل أحد المسؤولين.",
    },
    "alerts_offer": {
        "english": (
            "🔔 Want to be warned when something serious is reported near you?\n\n"
            "You'll only hear from me when at least two separate people report the "
            "same thing nearby, or an official confirms it. No daily messages."
        ),
        "yoruba": (
            "🔔 Ṣé o fẹ́ kí n kìlọ̀ fún ọ nígbà tí nǹkan pàtàkì bá ṣẹlẹ̀ nítòsí rẹ?\n\n"
            "Èmi yóò kàn sí ọ nìkan nígbà tí ó bá kéré tán ènìyàn méjì ọ̀tọ̀ọ̀tọ̀ bá "
            "ròyìn ohun kan náà nítòsí, tàbí tí aláṣẹ bá jẹ́rìí sí i. Kò sí ìránṣẹ́ ojoojúmọ́."
        ),
        "arabic": (
            "🔔 هل تريد أن أحذرك عند الإبلاغ عن حادث خطير قريب منك؟\n\n"
            "لن أراسلك إلا إذا أبلغ شخصان مختلفان على الأقل عن الأمر نفسه في "
            "منطقتك، أو أكّده مسؤول. لا رسائل يومية."
        ),
    },
    "alerts_on": {
        "english": (
            "🔔 Done — I'll warn you about confirmed incidents near this location.\n"
            "Send *STOP* at any time to turn this off."
        ),
        "yoruba": (
            "🔔 Ó ti parí — èmi yóò kìlọ̀ fún ọ nípa ewu tí a jẹ́rìí sí nítòsí ibí yìí.\n"
            "Fi *STOP* ránṣẹ́ nígbàkigbà láti pa á."
        ),
        "arabic": (
            "🔔 تم — سأحذرك من الحوادث المؤكدة قرب هذا الموقع.\n"
            "أرسل *STOP* في أي وقت لإيقاف ذلك."
        ),
    },
    "alerts_off": {
        "english": "🔕 Area alerts are off. You can still report anything to me at any time.",
        "yoruba": "🔕 Ìkìlọ̀ agbègbè ti parí. O ṣì lè ròyìn ohunkóhun fún mi nígbàkigbà.",
        "arabic": "🔕 تم إيقاف تنبيهات المنطقة. لا يزال بإمكانك الإبلاغ عن أي شيء في أي وقت.",
    },
    "alerts_need_location": {
        "english": (
            "📍 To warn you about incidents near you, I need to know roughly where "
            "you are. Share your location (tap 📎 → *Location*) and I'll switch alerts on."
        ),
        "yoruba": (
            "📍 Kí n lè kìlọ̀ fún ọ nípa ewu tí ó wà nítòsí rẹ, mo nílò láti mọ̀ níbi "
            "tí o wà. Pín ibi tí o wà (tẹ 📎 → *Location*) èmi yóò sì tan ìkìlọ̀."
        ),
        "arabic": (
            "📍 لكي أحذرك من الحوادث القريبة، أحتاج إلى معرفة مكانك تقريبًا. "
            "شارك موقعك (اضغط 📎 ← *الموقع*) وسأفعّل التنبيهات."
        ),
    },
    "distress": {
        "english": (
            f"If you are in danger right now, call {EMERGENCY_NUMBERS}.\n\n"
            "Get yourself somewhere safe first. When you can, tell me what is "
            "happening — a photo, a voice note, or just a few words."
        ),
        "yoruba": (
            f"Tí o bá wà nínú ewu báyìí, pe {EMERGENCY_NUMBERS}.\n\n"
            "Kọ́kọ́ wá ibi àìléwu. Nígbà tí o bá lè ṣe é, sọ ohun tí ó ń ṣẹlẹ̀ fún mi — "
            "àwòrán, ohùn, tàbí ọ̀rọ̀ díẹ̀."
        ),
        "arabic": (
            f"إذا كنت في خطر الآن، اتصل بـ {EMERGENCY_NUMBERS}.\n\n"
            "انتقل أولًا إلى مكان آمن. وعندما تستطيع، أخبرني بما يحدث — "
            "صورة أو رسالة صوتية أو بضع كلمات."
        ),
    },
    "photo_failed": {
        "english": "I couldn't open that photo. Please send it again, or just tell me what you're seeing.",
        "yoruba": "Mi ò lè ṣí àwòrán yẹn. Jọ̀wọ́ fi í ránṣẹ́ lẹ́ẹ̀kan sí i, tàbí sọ ohun tí o ń rí fún mi.",
        "arabic": "لم أتمكن من فتح هذه الصورة. أعد إرسالها، أو أخبرني بما تراه.",
    },
    "no_voice_input": {
        "english": (
            "I can't listen to voice notes in this language yet — I'd risk "
            "mishearing something important. Please type what is happening, or "
            "send a photo. You can also switch to English for voice."
        ),
        "arabic": (
            "لا أستطيع بعد الاستماع إلى الرسائل الصوتية بهذه اللغة، وقد أسيء فهم "
            "أمر مهم. من فضلك اكتب ما يحدث، أو أرسل صورة. يمكنك أيضًا التحويل إلى "
            "الإنجليزية لاستخدام الصوت."
        ),
        "yoruba": (
            "Mi ò tíì lè gbọ́ ohùn ní èdè yìí — mo lè ṣì ohun pàtàkì gbọ́. "
            "Jọ̀wọ́ kọ ohun tí ó ń ṣẹlẹ̀, tàbí fi àwòrán ránṣẹ́."
        ),
    },
    "voice_failed": {
        "english": "I couldn't hear that clearly. Please try again, or type what is happening.",
        "yoruba": "Mi ò gbọ́ ọ̀rọ̀ yẹn kedere. Jọ̀wọ́ gbìyànjú lẹ́ẹ̀kan sí i, tàbí kọ ohun tí ó ń ṣẹlẹ̀.",
        "arabic": "لم أسمع ذلك بوضوح. حاول مرة أخرى، أو اكتب ما يحدث.",
    },
    "unsupported": {
        "english": "I can read text, listen to voice notes, look at photos, and use your location.",
        "yoruba": "Mo lè ka ìkọ̀wé, gbọ́ ohùn, wo àwòrán, kí n sì lo ibi tí o wà.",
        "arabic": "يمكنني قراءة النصوص، والاستماع إلى الرسائل الصوتية، والنظر إلى الصور، واستخدام موقعك.",
    },
    "error": {
        "english": (
            "Something went wrong on my side. Please send that again.\n"
            f"If anyone is in danger, call {EMERGENCY_NUMBERS} now."
        ),
        "yoruba": (
            "Nǹkan kan bàjẹ́ ní ọ̀dọ̀ mi. Jọ̀wọ́ fi í ránṣẹ́ lẹ́ẹ̀kan sí i.\n"
            f"Tí ẹnikẹ́ni bá wà nínú ewu, pe {EMERGENCY_NUMBERS} báyìí."
        ),
        "arabic": (
            "حدث خطأ من جهتي. أعد إرسال رسالتك.\n"
            f"إذا كان أحد في خطر، اتصل بـ {EMERGENCY_NUMBERS} الآن."
        ),
    },
}


def ui(key: str, language: str = "english", **kwargs) -> str:
    """One fixed string in the user's language, falling back to English."""
    variants = UI_STRINGS.get(key, {})
    text = variants.get(language) or variants.get("english", "")
    return text.format(**kwargs) if kwargs else text


BUTTON_APPROVE_TERMS = {"id": "approve_terms", "title": "Approve"}
BUTTON_ALERTS_ON = {"id": "alerts_on", "title": "Yes, warn me"}
BUTTON_ALERTS_OFF = {"id": "alerts_off", "title": "No thanks"}


# ── WhatsApp transport ───────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }


async def download_media(media_id: str) -> bytes:
    """Fetch media bytes — a two-step lookup then download, as Meta requires."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        meta = await client.get(
            f"{GRAPH_API}/{media_id}",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
        )
        meta.raise_for_status()
        url = meta.json().get("url")
        if not url:
            raise HTTPException(status_code=404, detail="Media URL not found")

        media = await client.get(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
        media.raise_for_status()
        return media.content


async def upload_media(content: bytes, mime_type: str) -> Optional[str]:
    """Upload outbound media and return its id, or None on failure."""
    suffix = ".mp3" if "audio" in mime_type else ".jpg"
    path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(content)
            path = handle.name

        with open(path, "rb") as handle:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{GRAPH_API}/{WHATSAPP_PHONE_NUMBER_ID}/media",
                    headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
                    files={"file": (os.path.basename(path), handle, mime_type)},
                    data={"messaging_product": "whatsapp"},
                )
        response.raise_for_status()
        return response.json().get("id")
    except Exception as e:
        logger.error(f"❌ Media upload failed: {e}")
        return None
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def clean_for_whatsapp(text: str) -> str:
    """Convert model markdown into what WhatsApp actually renders."""
    if not text:
        return text
    text = re.sub(r"^#+\s+(.*)$", r"*\1*", text, flags=re.MULTILINE)
    text = text.replace("**", "*")
    text = re.sub(r"^[*-]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^---+$", "────────────────", text, flags=re.MULTILINE)
    return text.strip()


async def send_message(to_number: str, text: str = None,
                       media_id: str = None, media_type: str = "text") -> bool:
    """Send one WhatsApp message. Returns False rather than raising.

    Callers are usually mid-conversation with somebody in trouble; a failed send
    should be logged and worked around, not allowed to abort the turn.
    """
    if media_type == "audio" and media_id:
        payload = {"type": "audio", "audio": {"id": media_id}}
    elif media_type == "image" and media_id:
        payload = {"type": "image", "image": {"id": media_id, "caption": text or ""}}
    else:
        payload = {"type": "text", "text": {"body": text or ""}}

    body = {"messaging_product": "whatsapp", "to": to_number, **payload}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GRAPH_API}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers=_headers(),
                json=body,
            )
        if response.status_code != 200:
            # Error 131047 (outside the 24-hour window, a template is required)
            # is the common one and is invisible without the response body.
            logger.error(f"❌ Send to {to_number} failed: {response.status_code} {response.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"❌ Send to {to_number} errored: {e}")
        return False


async def send_buttons(to_number: str, body: str, buttons: List[dict],
                       header: str = None) -> bool:
    """Send up to three reply buttons.

    WhatsApp caps an interactive body at 1024 characters and rejects anything
    longer outright, so a long answer is sent as plain text first and the buttons
    follow with a short prompt — the person still gets the whole answer.
    """
    if len(body) > 1024:
        await send_message(to_number, text=body)
        body = "What would you like to do?"

    action_buttons = [
        {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
        for b in buttons[:3]
    ]

    interactive = {
        "type": "button",
        "body": {"text": body},
        "action": {"buttons": action_buttons},
    }
    if header and header.strip():
        interactive["header"] = {"type": "text", "text": header[:60]}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GRAPH_API}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers=_headers(),
                json={
                    "messaging_product": "whatsapp",
                    "to": to_number,
                    "type": "interactive",
                    "interactive": interactive,
                },
            )
        if response.status_code != 200:
            logger.error(f"❌ Buttons to {to_number} failed: {response.status_code} {response.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"❌ Buttons to {to_number} errored: {e}")
        return False


async def mark_read_and_typing(message_id: str) -> bool:
    """Show the person their message arrived and something is happening."""
    if not message_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{GRAPH_API}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers=_headers(),
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                    "typing_indicator": {"type": "text"},
                },
            )
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"Typing indicator failed: {e}")
        return False


def language_buttons(current: str = "english") -> List[dict]:
    """Up to three buttons offering the OTHER available languages.

    Built from the registry, so enabling Arabic makes it appear here with no
    change to this function.
    """
    return [
        {"id": lang.button_id, "title": lang.label[:20]}
        for key, lang in LANGUAGES.items()
        if key != current
    ][:3]


# ── Speech ───────────────────────────────────────────────────────────────────

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


class NoSpeechRecogniser(Exception):
    """This language has no speech-to-text service configured."""

    def __init__(self, language: str):
        self.language = language
        super().__init__(f"No STT service for {language}")


async def transcribe(message: dict, language: str) -> str:
    service = get_stt_service(language)
    if not service:
        # Raised before the download so we do not pull media we cannot use.
        raise NoSpeechRecogniser(language)
    audio_id = message["audio"]["id"]
    audio_bytes = await download_media(audio_id)
    text = await service.transcribe(audio_bytes)
    logger.info(f"🎤 Transcribed ({language}): {text[:120]}")
    return text


async def synthesise(text: str, language: str) -> Optional[bytes]:
    """Speak a reply. Returns None when the language has no voice configured.

    Sentence-by-sentence but SEQUENTIAL (not parallel) to avoid hitting ElevenLabs
    concurrent request limits (max 2 concurrent). One failed sentence loses only
    a few words instead of the entire answer.
    """
    service = get_tts_service(language)
    if not service:
        logger.info(f"🔇 No voice configured for {language} — replying in text only")
        return None

    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if len(s.strip()) > 3] or [text]
    voice = get_voice(language)

    parts = []
    for sentence in sentences:
        try:
            audio = await asyncio.to_thread(
                service.synthesize_sync, numbers_to_words(sentence, language), voice
            )
            parts.append(audio)
        except Exception as e:
            logger.warning(f"⚠️ TTS failed for one sentence: {e}")

    if not parts:
        return None
    return await asyncio.to_thread(_concat_mp3, parts)


def translate_out(text: str, language: str) -> str:
    """Render an English reply into the user's language (sync; call in a thread)."""
    if language == "english":
        return text
    return get_translator().from_english(text, language)


# ── Report pipeline ──────────────────────────────────────────────────────────

async def advance_report(draft: dict, phone: str, language: str) -> Optional[str]:
    """Submit a draft if it is ready, then route, corroborate and maybe broadcast.

    Returns a short line for the reporter about what happened to their report, or
    None when the draft is not yet substantial enough to file. Called after every
    piece of evidence arrives, and safe to call repeatedly — submission and
    forwarding are each guarded so a second photo does not re-file or re-send.
    """
    if not draft.get("category") or not (draft.get("has_image") or draft.get("summary")):
        return None

    report_id = draft["report_id"]

    if draft.get("status") == reports.STATUS_DRAFT:
        report = reports.submit_report(report_id) or draft
    else:
        report = reports.get_report(report_id) or draft

    # A photo usually arrives before the location, so the first forward often
    # carries no coordinates — which leaves the agency with a report it cannot
    # respond to. Send it again, once, when the location finally lands.
    has_location = bool((report.get("location") or {}).get("lat") is not None)
    already_sent = bool(report.get("forwarded_at"))
    location_still_missing_at_agency = has_location and not report.get("forwarded_with_location")

    routing_key = "routing_held"
    if not already_sent or location_still_missing_at_agency:
        if await alerts.forward_to_authority(report, is_update=already_sent):
            routing_key = "routing_sent"
        report = reports.get_report(report_id) or report
    elif report.get("forward_ok"):
        routing_key = "routing_sent"

    # Corroboration needs a location; without one this is a no-op that returns
    # "not alertable", which is the correct answer rather than an error.
    decision = reports.evaluate_corroboration(report)
    report = reports.get_report(report_id) or report

    if decision.get("alertable"):
        outcome = await alerts.maybe_broadcast(report, send_message, translate_out)
        logger.info(f"📢 Broadcast decision for {report_id}: {outcome}")

    return ui("report_filed", language,
              report_id=report_id,
              routing=ui(routing_key, language))


async def handle_image(message: dict, draft: dict, language: str) -> tuple:
    """Download, strip metadata, analyse. Returns (context_for_agent, patch).

    Metadata stripping happens before analysis and before storage, so the bytes
    carrying GPS coordinates exist only inside this function.
    """
    caption = message.get("image", {}).get("caption", "")
    media_id = message["image"]["id"]

    image_bytes = None
    for attempt in range(1, 4):
        try:
            image_bytes = await download_media(media_id)
            if image_bytes:
                break
        except Exception as e:
            logger.warning(f"Image download attempt {attempt} failed: {e}")
            if attempt < 3:
                await asyncio.sleep(1.5 * attempt)

    if not image_bytes:
        raise RuntimeError("could not download the image")

    clean_bytes, strip_report = strip_image_metadata(image_bytes)
    if not strip_report.get("stripped"):
        # We keep the report but must never forward pixels we could not clean.
        logger.error(
            f"⚠️ Image metadata could not be stripped ({strip_report.get('error')}) — "
            f"the image will be analysed but never stored or forwarded"
        )

    analysis = await vision.analyse_image(clean_bytes, caption)

    patch = {
        "has_image": True,
        "image_metadata_stripped": bool(strip_report.get("stripped")),
        "image_had_gps": bool(strip_report.get("had_gps")),
    }
    if analysis.get("ok") and analysis.get("is_relevant"):
        patch.update({
            "category": analysis["category"],
            "severity": analysis["severity"],
            "ocr_text": analysis.get("ocr_text", ""),
            "image_authenticity": analysis["authenticity"],
            "summary": draft.get("summary") or analysis.get("description", ""),
        })

    context = vision.analysis_for_prompt(analysis)
    if caption:
        context += f'\n\nThe person captioned the photo: "{scrub_text(caption)}"'

    return context, patch


def merge_severity(existing: Optional[str], incoming: Optional[str]) -> str:
    """Keep the worst severity seen on a report.

    A photo of ankle-deep water followed by a voice note saying someone is
    trapped must not leave the report at the photo's lower reading.
    """
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    if not existing:
        return incoming or "medium"
    if not incoming:
        return existing
    return existing if order.get(existing, 1) >= order.get(incoming, 1) else incoming


# ── Webhook ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("🛡️  ComGuard starting")
    logger.info("=" * 60)

    _find_ffmpeg()
    await unified_agent.initialize()

    logger.info(f"🌍 Languages available: {', '.join(l.label for l in LANGUAGES.values())}")
    logger.info("=" * 60)
    logger.info("✅ ComGuard ready")
    logger.info("=" * 60)

    yield

    logger.info("👋 ComGuard shutting down")


app = FastAPI(title="ComGuard", lifespan=lifespan)

if DASHBOARD_ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DASHBOARD_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )


@app.api_route("/whatsapp", methods=["GET", "POST"])
async def whatsapp_handler(request: Request) -> Response:
    """Verify the webhook, then acknowledge fast and work in the background."""
    if request.method == "GET":
        params = request.query_params
        if params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN:
            return Response(content=params.get("hub.challenge"), status_code=200)
        return Response(content="Verification token mismatch", status_code=403)

    await validate_whatsapp_hmac(request)

    try:
        data = await request.json()
    except Exception:
        logger.warning("Malformed JSON on webhook")
        return Response(status_code=400)

    try:
        value = data["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
        if not messages:
            return Response(status_code=200)  # delivery receipt, nothing to do
        message = messages[0]
        from_number = message["from"]
    except (KeyError, IndexError):
        logger.warning("Unexpected webhook payload shape")
        return Response(status_code=200)

    task = asyncio.create_task(process_message(message, from_number))

    def _log_failure(finished):
        try:
            finished.result()
        except Exception as e:
            logger.error(f"❌ Background task failed for {from_number}: {e}", exc_info=True)

    task.add_done_callback(_log_failure)

    logger.info(f"⚡ Acknowledged {message.get('id')} from {from_number}")
    return Response(status_code=200)


async def process_message(message: dict, from_number: str):
    """The full pipeline for one inbound message."""
    thread_id = from_number.replace("+", "")
    message_type = message.get("type", "unknown")
    message_id = message.get("id")
    language = store.get_user_language(thread_id)

    logger.info(f"[bg] {message_type} from {from_number} ({language})")

    try:
        # ── Interactive replies ──────────────────────────────────────────
        if message_type == "interactive":
            handled = await handle_interactive(message, from_number, thread_id, language)
            if handled:
                return

        # ── Terms gate ───────────────────────────────────────────────────
        # Everything is blocked until the person has seen how their data is
        # handled. The Approve button is processed above and returns before
        # reaching here, so this cannot lock anyone out.
        if not store.has_accepted_terms(thread_id):
            logger.info(f"[bg] 📄 Sending terms to {from_number}")
            await send_buttons(from_number, ui("terms", language), [BUTTON_APPROVE_TERMS])
            return

        await mark_read_and_typing(message_id)

        # ── Location ─────────────────────────────────────────────────────
        if message_type == "location":
            await handle_location(message, from_number, thread_id, language)
            return

        agent_context = ""
        user_text = ""
        reply_with_voice = False
        draft = None
        patch: Dict[str, Any] = {}

        # ── Photo ────────────────────────────────────────────────────────
        if message_type == "image":
            # The reply matches how they contacted us. A photo sent on its own
            # usually means typing is not an option — hands full, panicking, or
            # unable to read — so that answer is spoken. A photo with a caption
            # shows they can and do type, so the answer is written.
            caption = (message.get("image", {}) or {}).get("caption", "") or ""
            reply_with_voice = not caption.strip()

            draft = reports.open_draft(thread_id, language)
            try:
                agent_context, patch = await handle_image(message, draft, language)
            except Exception as e:
                logger.error(f"[bg] Image handling failed: {e}")
                await send_message(from_number, ui("photo_failed", language))
                return
            user_text = agent_context

        # ── Voice note ───────────────────────────────────────────────────
        elif message_type == "audio":
            reply_with_voice = True
            try:
                transcript = await transcribe(message, language)
            except NoSpeechRecogniser:
                logger.info(f"[bg] No recogniser for {language} — asking for text")
                await send_message(from_number, ui("no_voice_input", language))
                return
            except Exception as e:
                logger.error(f"[bg] Transcription failed: {e}")
                await send_message(from_number, ui("voice_failed", language))
                return

            if is_distress_signal(transcript, language):
                await send_message(from_number, ui("distress", language))
                return

            user_text = transcript
            classification = await vision.classify_text(transcript)
            if classification.get("is_report"):
                draft = reports.open_draft(thread_id, language)
                patch = {
                    "has_audio": True,
                    "category": draft.get("category") or classification["category"],
                    "severity": merge_severity(draft.get("severity"), classification["severity"]),
                    "summary": classification["summary"] or draft.get("summary", ""),
                    "location_name": draft.get("location_name") or classification.get("place_mentioned", ""),
                }

        # ── Text ─────────────────────────────────────────────────────────
        elif message_type == "text":
            raw = message["text"]["body"].strip()

            if raw.upper() in ("STOP", "STOP ALERTS", "UNSUBSCRIBE"):
                store.set_alert_optin(thread_id, False)
                await send_message(from_number, ui("alerts_off", language))
                return

            if is_distress_signal(raw, language):
                await send_message(from_number, ui("distress", language))
                return

            user_text = raw
            classification = await vision.classify_text(raw)
            if classification.get("is_report"):
                draft = reports.open_draft(thread_id, language)
                patch = {
                    "category": draft.get("category") or classification["category"],
                    "severity": merge_severity(draft.get("severity"), classification["severity"]),
                    "summary": classification["summary"] or draft.get("summary", ""),
                    "location_name": draft.get("location_name") or classification.get("place_mentioned", ""),
                }

        else:
            await send_buttons(from_number, ui("unsupported", language), language_buttons(language))
            return

        if draft and patch:
            reports.update_report(draft["report_id"], patch)
            draft = reports.get_report(draft["report_id"]) or draft

        # ── Ask the agent ────────────────────────────────────────────────
        agent_input = agent_context or user_text
        try:
            response = await unified_agent.get_response(
                content=agent_input,
                thread_id=thread_id,
                message_type="voice" if reply_with_voice else "text",
                language=language,
                original_content=user_text if not agent_context else None,
            )
        except Exception as e:
            logger.error(f"[bg] Agent call failed: {e}", exc_info=True)
            await send_message(from_number, ui("error", language))
            return

        # Only languages with a translation endpoint take this path. Arabic is
        # written in Arabic by the model itself; translating it would be a round
        # trip through English at best, and a silent fallback to English at
        # worst — which is what happened before.
        if get_language(language).needs_translation:
            response = await asyncio.to_thread(translate_out, response, language)

        response = clean_for_whatsapp(response)

        # ── File the report and tell them what happened to it ────────────
        follow_ups: List[str] = []
        if draft:
            filed = await advance_report(draft, from_number, language)
            if filed:
                follow_ups.append(filed)
            refreshed = reports.get_report(draft["report_id"]) or draft
            if not (refreshed.get("location") or {}).get("lat"):
                follow_ups.append(ui("need_location", language))

        store.store_conversation(
            user_id=thread_id,
            question=user_text,
            response=response,
            message_type=message_type,
            report_id=(draft or {}).get("report_id", ""),
        )

        await deliver(from_number, response, language, reply_with_voice)

        for extra in follow_ups:
            await send_message(from_number, extra)

    except Exception:
        logger.exception(f"[bg] Unhandled error processing message from {from_number}")
        try:
            await send_message(from_number, ui("error", language))
        except Exception:
            pass


async def deliver(to_number: str, response: str, language: str, as_voice: bool):
    """Send the reply — as audio plus text when it was a voice note.

    The text always goes too. A voice note that fails to play, or arrives where
    somebody cannot listen, would otherwise be a reply they never receive.
    """
    if as_voice:
        try:
            audio = await synthesise(response, language)
            if audio:
                media_id = await upload_media(audio, "audio/mpeg")
                if media_id:
                    await asyncio.sleep(0.5)  # let WhatsApp finish processing it
                    await send_message(to_number, media_id=media_id, media_type="audio")
        except Exception as e:
            logger.error(f"❌ Voice reply failed, falling back to text: {e}")

    if len(response) > 1024:
        await send_message(to_number, text=response)
        await send_buttons(to_number, "Change language:", language_buttons(language))
    else:
        await send_buttons(to_number, response, language_buttons(language))


async def handle_interactive(message: dict, from_number: str,
                             thread_id: str, language: str) -> bool:
    """Handle a button tap. Returns True when the message is fully dealt with."""
    interactive = message.get("interactive", {})
    if interactive.get("type") != "button_reply":
        return False

    button_id = interactive.get("button_reply", {}).get("id", "")

    if button_id == BUTTON_APPROVE_TERMS["id"]:
        store.set_terms_accepted(thread_id)
        logger.info(f"[bg] ✅ Terms accepted by {from_number}")
        await send_buttons(from_number, ui("ready", language), language_buttons(language))
        return True

    # Language switching is handled before anything else that could consume the
    # message, so somebody who picked the wrong language is never stuck being
    # answered in it.
    if button_id in language_button_ids():
        chosen = language_by_button_id(button_id)
        if chosen:
            store.set_user_language(thread_id, chosen.key)
            confirmation = ui("ready", chosen.key)
            await send_buttons(from_number, confirmation, language_buttons(chosen.key))
        return True

    if button_id == BUTTON_ALERTS_ON["id"]:
        settings = store.get_user_settings(thread_id) or {}
        home = settings.get("alert_location")
        if home:
            store.set_alert_optin(thread_id, True, home["lat"], home["lon"])
            await send_message(from_number, ui("alerts_on", language))
        else:
            await send_message(from_number, ui("alerts_need_location", language))
        return True

    if button_id == BUTTON_ALERTS_OFF["id"]:
        store.set_alert_optin(thread_id, False)
        await send_message(from_number, ui("alerts_off", language))
        return True

    return False


async def handle_location(message: dict, from_number: str,
                          thread_id: str, language: str):
    """A shared location attaches to the open report and enables area alerts.

    One location serves both purposes, so people are not asked for it twice.
    Opting in is still a separate, explicit tap — sharing a location to route a
    report is not consent to be messaged later.
    """
    location = message.get("location", {})
    lat, lon = location.get("latitude"), location.get("longitude")
    if lat is None or lon is None:
        return

    place = location.get("name") or location.get("address") or ""
    logger.info(f"[bg] 📍 Location from {from_number}: {lat:.4f},{lon:.4f} {place}")

    # Acknowledge it straight away. Sharing a location is an act of trust, and
    # silence afterwards reads as the message having gone nowhere — which is
    # what happened before: the next thing the person saw was an unexplained
    # opt-in prompt, or nothing at all if no report was open yet.
    await send_message(
        from_number,
        ui("location_received", language, place=f" — {place}" if place else ""),
    )

    draft = reports.open_draft(thread_id, language)
    reports.update_report(draft["report_id"], {
        "location": {"lat": float(lat), "lon": float(lon)},
        "location_name": place or draft.get("location_name", ""),
    })
    draft = reports.get_report(draft["report_id"]) or draft

    filed = await advance_report(draft, from_number, language)
    if filed:
        await send_message(from_number, filed)

    # Someone already subscribed has just told us where they are — refresh the
    # location their alerts are matched against and say nothing more about it.
    if store.is_alert_subscriber(thread_id):
        store.set_alert_optin(thread_id, True, lat, lon)
        return

    # Not subscribed. Remember the location so tapping "Yes" does not require a
    # second share, but leave them opted OUT until they actually tap it —
    # sharing a location to route a report is not consent to be messaged later.
    store.remember_location(thread_id, lat, lon)

    await send_buttons(
        from_number,
        ui("alerts_offer", language),
        [BUTTON_ALERTS_ON, BUTTON_ALERTS_OFF],
    )


# ── Dispatcher dashboard ─────────────────────────────────────────────────────

_auth = HTTPBasic(realm="ComGuard")


def require_login(credentials: HTTPBasicCredentials = Depends(_auth)) -> str:
    """Guard the dashboard.

    With no password configured the endpoint refuses to serve rather than
    defaulting to open — these rows describe where incidents were reported, and
    an unprotected default would publish them.

    Both halves are compared with compare_digest, and every configured password
    is checked without an early break, so response timing does not reveal which
    part matched.
    """
    if not DASHBOARD_PASSWORDS or not DASHBOARD_USER:
        raise HTTPException(
            status_code=503,
            detail="Dashboard not configured — set DASHBOARD_USER and DASHBOARD_PASSWORD",
        )

    user_ok = secrets.compare_digest(credentials.username, DASHBOARD_USER)
    password_ok = False
    for candidate in DASHBOARD_PASSWORDS:
        if secrets.compare_digest(credentials.password, candidate):
            password_ok = True

    if not (user_ok and password_ok):
        logger.warning(f"⚠️ Failed dashboard login for {credentials.username!r}")
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": 'Basic realm="ComGuard"'},
        )
    return credentials.username


@app.get("/reports/data")
async def reports_data(status: str = "", category: str = "", limit: int = 200,
                       user: str = Depends(require_login)):
    """Reports and headline counts for the dispatcher view.

    These rows are already anonymised in storage — there is no phone number to
    withhold here, only a pseudonym.
    """
    return {
        "rows": reports.recent_reports(limit=limit, status=status, category=category),
        "stats": reports.report_stats(),
        "alerts": reports.recent_alerts(limit=25),
    }


@app.post("/reports/{report_id}/verify")
async def verify_report(report_id: str, user: str = Depends(require_login)):
    """Confirm a report directly — the second route to a community alert.

    A dispatcher who can see the incident does not need two members of the
    public to agree before neighbours are warned.
    """
    report = reports.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    reports.mark_verified(report_id, dispatcher=user)
    report = reports.get_report(report_id)

    outcome = await alerts.maybe_broadcast(report, send_message, translate_out)
    return {"ok": True, "report_id": report_id, "broadcast": outcome}


@app.post("/reports/{report_id}/dismiss")
async def dismiss_report(report_id: str, note: str = "", user: str = Depends(require_login)):
    if not reports.get_report(report_id):
        raise HTTPException(status_code=404, detail="Report not found")
    reports.mark_dismissed(report_id, dispatcher=user, note=note)
    return {"ok": True, "report_id": report_id}


@app.get("/reports/export.csv")
async def export_reports(user: str = Depends(require_login)):
    """Download the report log as CSV."""
    import csv
    import io

    rows = reports.recent_reports(limit=5000)
    fields = [
        "report_id", "created_at", "status", "category", "severity",
        "summary", "location_name", "corroboration_count", "language",
        "has_image", "has_audio", "forwarded_at", "alerted_at",
    ]

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="comguard-reports-{stamp}.csv"',
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@app.get("/")
async def root():
    return {
        "service": "ComGuard",
        "status": "running",
        "languages": [lang.label for lang in LANGUAGES.values()],
    }


@app.get("/health")
async def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5001")))
