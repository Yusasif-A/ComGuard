import os
from dotenv import load_dotenv
load_dotenv()

HAUSA_STT_API_URL       = os.getenv("HAUSA_STT_API_URL",       "")
HAUSA_STT_FALLBACK_URL  = os.getenv("HAUSA_STT_FALLBACK_URL",  "")
HAUSA_TTS_BASE_URL      = os.getenv("HAUSA_TTS_BASE_URL",      "")
HAUSA_TTS_FALLBACK_URL  = os.getenv("HAUSA_TTS_FALLBACK_URL",  "")
HAUSA_TTS_MODEL         = os.getenv("HAUSA_TTS_MODEL",         "tts-1")
HAUSA_TTS_VOICE         = os.getenv("HAUSA_TTS_VOICE",         "female")
HAUSA_NLLB_URL          = os.getenv("HAUSA_NLLB_URL",          "")

IGBO_STT_API_URL        = os.getenv("IGBO_STT_API_URL",        "")
IGBO_STT_FALLBACK_URL   = os.getenv("IGBO_STT_FALLBACK_URL",   "")
IGBO_TTS_BASE_URL       = os.getenv("IGBO_TTS_BASE_URL",       "")
IGBO_TTS_FALLBACK_URL   = os.getenv("IGBO_TTS_FALLBACK_URL",   "")
IGBO_TTS_MODEL          = os.getenv("IGBO_TTS_MODEL",          "igbo-tts-model")
IGBO_TTS_VOICE          = os.getenv("IGBO_TTS_VOICE",          "female")
IGBO_NLLB_URL           = os.getenv("IGBO_NLLB_URL",           "")

YORUBA_STT_API_URL      = os.getenv("YORUBA_STT_API_URL",      "")
YORUBA_STT_FALLBACK_URL = os.getenv("YORUBA_STT_FALLBACK_URL", "")
YORUBA_TTS_BASE_URL     = os.getenv("YORUBA_TTS_BASE_URL",     "")
YORUBA_TTS_FALLBACK_URL = os.getenv("YORUBA_TTS_FALLBACK_URL", "")
YORUBA_TTS_MODEL        = os.getenv("YORUBA_TTS_MODEL",        "yoruba-tts-model")
YORUBA_TTS_VOICE        = os.getenv("YORUBA_TTS_VOICE",        "female")
YORUBA_NLLB_URL         = os.getenv("YORUBA_NLLB_URL",         "")

ENGLISH_STT_API_URL      = os.getenv("ENGLISH_STT_API_URL",      "")
ENGLISH_STT_FALLBACK_URL = os.getenv("ENGLISH_STT_FALLBACK_URL", "")
ENGLISH_TTS_BASE_URL     = os.getenv("ENGLISH_TTS_BASE_URL",     "")
ENGLISH_TTS_FALLBACK_URL = os.getenv("ENGLISH_TTS_FALLBACK_URL", "")
ENGLISH_TTS_MODEL        = os.getenv("ENGLISH_TTS_MODEL",        "nigerian-english-xtts")
ENGLISH_TTS_VOICE        = os.getenv("ENGLISH_TTS_VOICE",        "female2")

FOOD_RECOGNITION_URL = os.getenv("FOOD_RECOGNITION_URL", "")
TRANSLATOR_URL       = os.getenv("TRANSLATOR_URL",        "")
QWEN_EMBED_URL       = os.getenv("QWEN_EMBED_URL",        "")
