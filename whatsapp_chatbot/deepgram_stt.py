"""
Deepgram speech-to-text.

Used for languages the self-hosted Whisper endpoints do not cover. Arabic is the
case that forced it: the English recogniser is pinned to language="en" and would
turn an Arabic voice note into English-sounding nonsense, which would then be
filed as somebody's emergency report.

Verified against a WhatsApp-format voice note (ogg/opus, mono, 16kHz, 16kbps):
nova-3 with language="ar" returned the input sentence at 0.97 confidence.

The interface mirrors LocalSpeechToText (`transcribe(bytes) -> str`), so
services.py hands either one to the same call site and app.py never learns which
provider listened.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.deepgram.com/v1/listen"

# nova-3 is the only current-generation model with Arabic, and it carries 17
# regional variants (ar-EG, ar-SD, ar-TD, ar-MA, ar-DZ among them). Plain "ar"
# matched the dialect-specific codes on test audio, so it stays the default.
DEFAULT_MODEL = "nova-3"


class DeepgramSpeechToTextError(Exception):
    pass


class DeepgramSpeechToText:
    """STT client for one language, backed by the Deepgram API."""

    def __init__(self, api_key: str, language_code: str, model: str = ""):
        self.api_key = api_key
        self.language_code = language_code
        self.model = model or DEFAULT_MODEL
        logger.info(
            f"✅ Deepgram STT ready — model: {self.model}, language: {self.language_code}"
        )

    def _params(self, language: str) -> dict:
        return {
            "model": self.model,
            # An explicit language, never "multi". Deepgram's multilingual mode
            # romanises Arabic into Latin script — which reads as a successful
            # transcription and is useless for everything downstream.
            "language": language or self.language_code,
            # Punctuation and numeral formatting, so "مئة واثني عشر" is written
            # 112 and the summary stays readable.
            "smart_format": "true",
        }

    def _extract(self, payload: dict) -> str:
        try:
            alternative = payload["results"]["channels"][0]["alternatives"][0]
        except (KeyError, IndexError) as e:
            raise DeepgramSpeechToTextError(f"Unexpected Deepgram response shape: {e}")

        transcript = (alternative.get("transcript") or "").strip()
        if not transcript:
            # Deepgram returns 200 with an empty transcript for silence or audio
            # it cannot make out. That is a failed transcription, not an empty
            # report, so it has to raise rather than file a blank summary.
            raise DeepgramSpeechToTextError("Deepgram returned an empty transcript")

        confidence = alternative.get("confidence", 0.0)
        logger.info(f"✅ Deepgram transcribed ({confidence:.2f}): {transcript[:120]}")
        return transcript

    async def transcribe(self, audio_data: bytes, language: str = None) -> str:
        """Transcribe raw audio bytes.

        The bytes go up exactly as WhatsApp delivered them. Deepgram detects the
        container itself — ogg/opus, mpeg and octet-stream all returned the same
        result on test audio — so nothing needs to re-encode first, and no
        temporary file is written.
        """
        if not audio_data:
            raise ValueError("Audio data cannot be empty")

        logger.info(
            f"🎤 Deepgram STT: {len(audio_data)} bytes "
            f"({language or self.language_code}, {self.model})"
        )

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    API_URL,
                    headers={
                        "Authorization": f"Token {self.api_key}",
                        "Content-Type": "application/octet-stream",
                    },
                    params=self._params(language),
                    content=audio_data,
                )
        except Exception as e:
            raise DeepgramSpeechToTextError(f"Deepgram request failed: {e}") from e

        if response.status_code != 200:
            raise DeepgramSpeechToTextError(
                f"Deepgram returned {response.status_code}: {response.text[:300]}"
            )

        return self._extract(response.json())
