import os
import sys
import logging

# Add AI_engine to path for regional services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "AI_engine")))

from speech_to_text import SpeechToText
from text_to_speech import TextToSpeech
from local_stt import LocalSpeechToText
from local_tts import LocalTextToSpeech
from nllb_translator import NLLBTranslator, HelpMumTranslator
from config import (
    HAUSA_STT_API_URL, HAUSA_STT_FALLBACK_URL, HAUSA_TTS_BASE_URL, HAUSA_TTS_FALLBACK_URL, HAUSA_TTS_MODEL, HAUSA_NLLB_URL,
    IGBO_STT_API_URL, IGBO_STT_FALLBACK_URL, IGBO_TTS_BASE_URL, IGBO_TTS_FALLBACK_URL, IGBO_TTS_MODEL, IGBO_NLLB_URL,
    YORUBA_STT_API_URL, YORUBA_STT_FALLBACK_URL, YORUBA_TTS_BASE_URL, YORUBA_TTS_FALLBACK_URL, YORUBA_TTS_MODEL, YORUBA_NLLB_URL,
    TRANSLATOR_URL
)

logger = logging.getLogger(__name__)

# Initialize English services
english_stt = SpeechToText()
english_tts = TextToSpeech()

# Initialize Regional services
hausa_stt = LocalSpeechToText(api_url=HAUSA_STT_API_URL, fallback_url=HAUSA_STT_FALLBACK_URL)
hausa_tts = LocalTextToSpeech(base_url=HAUSA_TTS_BASE_URL, model=HAUSA_TTS_MODEL, fallback_url=HAUSA_TTS_FALLBACK_URL)

igbo_stt = LocalSpeechToText(api_url=IGBO_STT_API_URL, fallback_url=IGBO_STT_FALLBACK_URL)
igbo_tts = LocalTextToSpeech(base_url=IGBO_TTS_BASE_URL, model=IGBO_TTS_MODEL, fallback_url=IGBO_TTS_FALLBACK_URL)

yoruba_stt = LocalSpeechToText(api_url=YORUBA_STT_API_URL, fallback_url=YORUBA_STT_FALLBACK_URL)
yoruba_tts = LocalTextToSpeech(base_url=YORUBA_TTS_BASE_URL, model=YORUBA_TTS_MODEL, fallback_url=YORUBA_TTS_FALLBACK_URL)

# Use HelpMum unified translator if URL is configured, otherwise fall back to per-language NLLB
if TRANSLATOR_URL:
    translator = HelpMumTranslator(base_url=TRANSLATOR_URL)
    logger.info(f"✅ Using HelpMumTranslator: {TRANSLATOR_URL}")
else:
    translator = NLLBTranslator(
        hausa_url=HAUSA_NLLB_URL,
        igbo_url=IGBO_NLLB_URL,
        yoruba_url=YORUBA_NLLB_URL
    )
    logger.info("⚠️ TRANSLATOR_URL not set — using per-language NLLB endpoints")

def get_stt_service(language: str = "english"):
    if language == "hausa":
        return hausa_stt
    if language == "igbo":
        return igbo_stt
    if language == "yoruba":
        return yoruba_stt
    # Default is English
    return english_stt

def get_tts_service(language: str = "english"):
    if language == "hausa":
        return hausa_tts
    if language == "igbo":
        return igbo_tts
    if language == "yoruba":
        return yoruba_tts
    # Default is English
    return english_tts

def get_translator():
    return translator
