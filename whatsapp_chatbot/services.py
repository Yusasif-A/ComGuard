"""
Speech and translation services, built from the language registry.

Nothing here knows the name of a language. Services are constructed by walking
config.LANGUAGES, so turning Arabic on is an .env change and a restart — no code
in this file, app.py or the agent has to learn about it.
"""

import logging
from typing import Dict

from config import LANGUAGES, TRANSLATOR_URL, Language, get_language
from local_stt import LocalSpeechToText
from local_tts import LocalTextToSpeech
from nllb_translator import NLLBTranslator, PivotTranslator
from speech_to_text import SpeechToText
from text_to_speech import TextToSpeech

logger = logging.getLogger(__name__)

_stt_services: Dict[str, object] = {}
_tts_services: Dict[str, object] = {}


def _build_services():
    """Instantiate one STT and one TTS client per configured language.

    English uses the dedicated OpenAI-compatible clients; every other language
    goes through the generic local clients, which take their endpoints from the
    registry entry. A language whose endpoints are missing simply gets no client
    and falls back to English at call time.
    """
    for key, lang in LANGUAGES.items():
        if key == "english":
            _stt_services[key] = SpeechToText()
            _tts_services[key] = TextToSpeech()
            continue

        if lang.stt_url:
            _stt_services[key] = LocalSpeechToText(
                api_url=lang.stt_url,
                fallback_url=lang.stt_fallback_url,
                language_code=lang.whisper_code,
            )
        if lang.tts_url:
            _tts_services[key] = LocalTextToSpeech(
                base_url=lang.tts_url,
                model=lang.tts_model,
                fallback_url=lang.tts_fallback_url,
            )

    logger.info(
        "🗣️ Speech services ready — STT: %s | TTS: %s",
        sorted(_stt_services), sorted(_tts_services),
    )


_build_services()


# The unified translator is preferred when TRANSLATOR_URL is set; otherwise each
# language falls back to its own NLLB endpoint from the registry.
if TRANSLATOR_URL:
    _translator = PivotTranslator(base_url=TRANSLATOR_URL)
    logger.info(f"✅ Using PivotTranslator: {TRANSLATOR_URL}")
else:
    _translator = NLLBTranslator({
        key: lang.nllb_url for key, lang in LANGUAGES.items() if lang.nllb_url
    })
    logger.info("⚠️ TRANSLATOR_URL not set — using per-language NLLB endpoints")


def get_stt_service(language: str = "english"):
    """STT client for a language, falling back to English if it has none."""
    return _stt_services.get(language) or _stt_services.get("english")


def get_tts_service(language: str = "english"):
    """TTS client for a language, or None — callers must handle a missing voice.

    Returning None rather than the English voice is deliberate: reading a Yoruba
    reply aloud with an English voice produces something nobody can understand,
    and silently sending it would look like a working feature. The caller sends
    text instead.
    """
    return _tts_services.get(language)


def get_voice(language: str) -> str:
    return get_language(language).tts_voice


def get_translator():
    return _translator


def has_voice_support(language: str) -> bool:
    return language in _tts_services


def available_languages() -> Dict[str, Language]:
    return dict(LANGUAGES)
