"""
ComGuard configuration.

Everything language-specific lives in the LANGUAGES registry below rather than
being spread through if/elif chains across the codebase. Adding a language
(Arabic is the next one planned) means adding ONE entry here plus its env vars —
no changes to app.py, services.py or the agent.
"""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


# ElevenLabs serves any language its models cover from a single endpoint, so it
# is configured once here rather than per language. A language opts into it with
# <PREFIX>_TTS_PROVIDER=elevenlabs.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# Deepgram does the same job for speech-to-text: one key, one endpoint, many
# languages. A language opts in with <PREFIX>_STT_PROVIDER=deepgram.
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")


# ── Language registry ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Language:
    """Everything ComGuard needs to speak and listen in one language.

    `enabled` is computed from whether the speech endpoints are configured, so a
    language can be committed to the code before its API exists: it stays
    invisible to users until the env vars are filled in. That is how Arabic is
    staged below — the entry is real, it just has no endpoints yet.
    """

    key: str            # internal id, e.g. "yoruba"
    label: str          # what the user sees on the button, e.g. "Yoruba"
    button_id: str      # WhatsApp interactive button id, e.g. "lang_yo"
    whisper_code: str   # ISO code passed to the STT service, e.g. "yo"
    stt_url: str
    stt_fallback_url: str
    tts_url: str
    tts_fallback_url: str
    tts_model: str
    tts_voice: str
    nllb_url: str = ""               # per-language NLLB endpoint (unused when TRANSLATOR_URL is set)
    needs_translation: bool = False  # True => Gemma answers in English, NLLB renders the reply
    rtl: bool = False                # right-to-left script (Arabic)
    forced_on: bool = False          # <PREFIX>_ENABLED=true — text-only, no speech endpoints
    tts_provider: str = "openai"     # "openai" (self-hosted) or "elevenlabs"
    stt_provider: str = "openai"     # "openai" (self-hosted Whisper) or "deepgram"
    stt_model: str = ""              # provider-specific, e.g. "nova-3"

    @property
    def enabled(self) -> bool:
        """Offer this language if we can either speak or hear it.

        Derived from the capabilities rather than from a URL, because a provider
        like ElevenLabs or Deepgram has no per-language endpoint — configuring
        its key is what makes the language work. `forced_on` remains for a
        text-only language with no speech support at all.
        """
        return bool(self.can_speak or self.can_listen or self.forced_on)

    @property
    def can_listen(self) -> bool:
        """The service can transcribe a voice note in this language.

        Deepgram needs no per-language URL — one API covers every language it
        supports — so for that provider the key is what makes a language
        listenable.
        """
        if self.stt_provider == "deepgram":
            return bool(DEEPGRAM_API_KEY)
        return bool(self.stt_url) or self.key == "english"

    @property
    def can_speak(self) -> bool:
        """The service has a voice that actually pronounces this language.

        Checked before ever synthesising. A TTS model with no adapter for a
        language does not fail loudly — measured against the self-hosted
        deployment, it produces fluent-sounding audio of text the user never
        wrote. Sending that to somebody in an emergency is worse than sending
        nothing, so a language with no voice is text-only by construction.

        ElevenLabs needs no per-language URL: one API serves every language it
        supports, so having the key is what makes the language speakable.
        """
        if self.tts_provider == "elevenlabs":
            return bool(ELEVENLABS_API_KEY)
        return bool(self.tts_url)


def _lang(key, label, button_id, whisper_code, env_prefix, *,
          default_tts_model="", default_tts_voice="female",
          default_tts_provider="openai",
          default_stt_provider="openai", default_stt_model="",
          needs_translation=False, rtl=False) -> Language:
    """Build a Language from the <PREFIX>_* environment variables."""
    def g(suffix, default=""):
        return os.getenv(f"{env_prefix}_{suffix}", default)

    return Language(
        key=key,
        label=label,
        button_id=button_id,
        whisper_code=whisper_code,
        stt_url=g("STT_API_URL"),
        stt_fallback_url=g("STT_FALLBACK_URL"),
        tts_url=g("TTS_BASE_URL"),
        tts_fallback_url=g("TTS_FALLBACK_URL"),
        tts_model=g("TTS_MODEL", default_tts_model),
        tts_voice=g("TTS_VOICE", default_tts_voice),
        nllb_url=g("NLLB_URL"),
        needs_translation=needs_translation,
        rtl=rtl,
        forced_on=g("ENABLED", "").strip().lower() in ("1", "true", "yes"),
        tts_provider=g("TTS_PROVIDER", default_tts_provider).strip().lower(),
        stt_provider=g("STT_PROVIDER", default_stt_provider).strip().lower(),
        stt_model=g("STT_MODEL", default_stt_model),
    )


# English is the pivot language: Gemma reasons in it, and every other language's
# reply is produced by translating that English out. It is therefore always
# present even if its speech endpoints are unset (text still works).
ENGLISH = _lang(
    "english", "English", "lang_en", "en", "ENGLISH",
    default_tts_model="nigerian-english-xtts", default_tts_voice="female2",
)

YORUBA = _lang(
    "yoruba", "Yoruba", "lang_yo", "yo", "YORUBA",
    default_tts_model="yoruba-tts-model",
    needs_translation=True,
)

# Arabic speaks through ElevenLabs rather than the self-hosted Chatterbox
# service, which has adapters for en/yo/ha/ig only: asked for "ar" it returns
# 400, and handed Arabic text untagged it emits confident audio of words the
# user never wrote. ElevenLabs renders the same sentence correctly — both were
# checked by transcribing the generated audio back and comparing.
#
# Arabic listens through Deepgram for the mirror-image reason: the self-hosted
# English recogniser is pinned to language="en", so an Arabic voice note run
# through it comes back as English-sounding nonsense and gets filed as the
# person's report. Deepgram nova-3 returned the input sentence at 0.97
# confidence from a WhatsApp-format voice note (ogg/opus, mono, 16kHz).
ARABIC = _lang(
    "arabic", "العربية", "lang_ar", "ar", "ARABIC",
    default_tts_model="eleven_multilingual_v2",
    default_tts_voice="EXAVITQu4vr4xnSDxMaL",   # Sarah — calm, reassuring
    default_tts_provider="elevenlabs",
    default_stt_provider="deepgram",
    default_stt_model="nova-3",
    needs_translation=True,
    rtl=True,
)

_ALL_LANGUAGES = [ENGLISH, YORUBA, ARABIC]

#: Only languages whose speech endpoints are configured. English is forced in so
#: the service is never left with an empty language menu.
LANGUAGES = {
    lang.key: lang
    for lang in _ALL_LANGUAGES
    if lang.enabled or lang.key == "english"
}

DEFAULT_LANGUAGE = "english"


def get_language(key: Optional[str]) -> Language:
    """Look up a language, falling back to English for unknown/disabled keys."""
    return LANGUAGES.get(key or "", LANGUAGES[DEFAULT_LANGUAGE])


def language_by_button_id(button_id: str) -> Optional[Language]:
    for lang in LANGUAGES.values():
        if lang.button_id == button_id:
            return lang
    return None


def language_button_ids() -> set:
    return {lang.button_id for lang in LANGUAGES.values()}


# ── Model + retrieval endpoints ──────────────────────────────────────────────

# Gemma serves double duty: the conversational agent AND the vision model that
# reads notices, classifies hazards and flags synthetic images. There is no
# separate image-detection service.
GEMMA_BASE_URL = os.getenv("GEMMA_BASE_URL", "")
GEMMA_API_KEY = os.getenv("API_KEY", "")
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "google/gemma-4-E4B-it")
GEMMA_VISION_MODEL = os.getenv("GEMMA_VISION_MODEL", GEMMA_MODEL)

QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "")
QWEN_EMBED_API_KEY = os.getenv("QWEN_EMBED_API_KEY", "")
EMBED_MODEL = os.getenv("EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")

TRANSLATOR_URL = os.getenv("TRANSLATOR_URL", "")

# Corpus of municipal tax codes, state gazettes, penal law and SEMA bulletins.
CHROMA_PATH = os.getenv("COMGUARD_CHROMA_PATH", "comguard_chroma_store")
CHROMA_COLLECTION = os.getenv("COMGUARD_CHROMA_COLLECTION", "COMGUARD_CORPUS")


# ── Reporting, anonymisation and alerting ────────────────────────────────────

# Reporter identity is never stored in the clear on a report. The phone number is
# HMAC'd with this salt to produce a stable pseudonym, so repeat reports from the
# same person can be recognised (needed for "two INDEPENDENT reports") without
# the report itself carrying a number. Losing or rotating the salt breaks that
# link, which is the intended failure mode — it degrades to less corroboration,
# never to disclosure.
REPORT_SALT = os.getenv("REPORT_SALT", "")

# A community-wide alert goes out only when this many independent reporters
# describe the same thing inside CORROBORATION_RADIUS_KM within
# CORROBORATION_WINDOW_MINUTES — or when a dispatcher verifies it directly.
CORROBORATION_THRESHOLD = int(os.getenv("CORROBORATION_THRESHOLD", "2"))
CORROBORATION_RADIUS_KM = float(os.getenv("CORROBORATION_RADIUS_KM", "3"))
CORROBORATION_WINDOW_MINUTES = int(os.getenv("CORROBORATION_WINDOW_MINUTES", "180"))

# How close to a confirmed incident a resident must be to receive the broadcast.
ALERT_BROADCAST_RADIUS_KM = float(os.getenv("ALERT_BROADCAST_RADIUS_KM", "5"))

# Never re-broadcast the same cluster inside this window, so a stream of reports
# about one flood does not turn into a stream of identical alerts.
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "120"))

# Where anonymised reports are forwarded. AUTHORITY_WEBHOOK_URL is the default
# sink; per-category overrides let flooding go to SEMA and scams to the revenue
# service, e.g. AUTHORITY_WEBHOOK_FLOOD=https://sema.example/intake
AUTHORITY_WEBHOOK_URL = os.getenv("AUTHORITY_WEBHOOK_URL", "")
AUTHORITY_WEBHOOK_TOKEN = os.getenv("AUTHORITY_WEBHOOK_TOKEN", "")


def authority_webhook_for(category: str) -> str:
    """Route a category to its authority endpoint, falling back to the default."""
    specific = os.getenv(f"AUTHORITY_WEBHOOK_{(category or '').upper()}", "")
    return specific or AUTHORITY_WEBHOOK_URL


# Basic-auth login for the read-only dispatcher dashboard at /reports/*.
# Comma-separated passwords so credentials can be rotated without a gap.
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "")
DASHBOARD_PASSWORDS = [
    p.strip() for p in os.getenv("DASHBOARD_PASSWORD", "").split(",") if p.strip()
]

DASHBOARD_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("DASHBOARD_ALLOWED_ORIGINS", "").split(",") if o.strip()
]

# Emergency numbers surfaced when a report is life-threatening. Kept in config so
# a deployment in another state or country can change them without a code edit.
EMERGENCY_NUMBERS = os.getenv(
    "EMERGENCY_NUMBERS",
    "112 (national emergency) or 767 (Lagos LASEMA)",
)
