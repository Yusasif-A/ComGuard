import os
import tempfile
import logging
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LINGUACENTER_URL = "https://inference.linguacenter.net/v1"


class SpeechToTextError(Exception):
    pass

class SpeechToText:
    """English speech-to-text.
    Primary: OpenAI-compatible endpoint (Lightning AI /en/v1).
    Fallback: OpenAI-compatible endpoint (inference.linguacenter.net/v1).
    """

    def __init__(self):
        primary_raw = os.getenv("ENGLISH_STT_API_URL", "")
        self.primary_url = self._normalize(primary_raw) if primary_raw else None
        self.fallback_url = os.getenv("ENGLISH_STT_FALLBACK_URL", LINGUACENTER_URL)
        logger.info(f"✅ English STT initialized: {self.primary_url} | fallback: {self.fallback_url}")

    @staticmethod
    def _normalize(url: str) -> str:
        url = url.rstrip('/')
        if not url.endswith('/v1'):
            url = url + '/v1'
        return url

    async def transcribe(self, audio_data: bytes) -> str:
        if not audio_data:
            raise ValueError("Audio data cannot be empty")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_data)
            temp_file_path = tmp.name

        try:
            if self.primary_url:
                try:
                    return self._transcribe_openai(self.primary_url, temp_file_path)
                except Exception as primary_err:
                    logger.warning(f"⚠️ Primary English STT failed ({primary_err}), switching to fallback: {self.fallback_url}")

            # Fallback — both primary and fallback are OpenAI-compatible, so call through
            # the same transport. (The old "linguacenter direct" POST /transcribe endpoint
            # is gone; inference.linguacenter.net now serves OpenAI-style /v1/audio/transcriptions.)
            return self._transcribe_openai(self._normalize(self.fallback_url), temp_file_path)

        except SpeechToTextError:
            raise
        except Exception as e:
            raise SpeechToTextError(f"Speech-to-text conversion failed: {e}") from e
        finally:
            os.unlink(temp_file_path)

    def _transcribe_openai(self, base_url: str, temp_file_path: str) -> str:
        logger.info(f"📤 English STT: Sending to {base_url}")
        client = OpenAI(base_url=base_url, api_key="dummy")
        with open(temp_file_path, "rb") as audio_file:
            # Default response_format is "json", which the Lightning AI endpoint and
            # inference.linguacenter.net return as {"text": ...}. Fall back to str() in
            # case a server replies with a bare text body instead.
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="en"
            )
        result = getattr(transcription, "text", str(transcription)).strip()
        if not result:
            raise SpeechToTextError("Transcription result is empty")
        logger.info(f"✅ English STT transcribed: '{result}'")
        return result

