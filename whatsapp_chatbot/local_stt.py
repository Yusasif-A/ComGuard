"""
Hausa Speech-to-Text Service (OpenAI-compatible)
"""
import os
import tempfile
from openai import OpenAI
from typing import Optional
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

class LocalSpeechToTextError(Exception):
    """Custom exception for local speech-to-text errors."""
    pass

class LocalSpeechToText:
    """A class to handle speech-to-text conversion using OpenAI-compatible Whisper API for regional languages."""

    def __init__(self, api_url: str, fallback_url: Optional[str] = None):
        """
        Initialize the Local STT class

        Args:
            api_url: Primary API URL for STT service
            fallback_url: Fallback URL to use if primary fails
        """
        self.primary_url = self._normalize(api_url)
        self.fallback_url = self._normalize(fallback_url) if fallback_url else None
        self.client = self._make_client(self.primary_url)
        logger.info(f"✅ Local STT initialized: {self.primary_url} | fallback: {self.fallback_url or 'none'}")

    @staticmethod
    def _normalize(url: str) -> str:
        url = url.rstrip('/')
        if not url.endswith('/v1'):
            url = url + '/v1'
        return url

    @staticmethod
    def _make_client(base_url: str) -> OpenAI:
        return OpenAI(base_url=base_url, api_key="dummy")

    async def transcribe(self, audio_data: bytes, language: str = "ha") -> str:
        """Convert speech to text using OpenAI-compatible Whisper API.

        Args:
            audio_data: Binary audio data
            language: Language code (e.g., 'ha' for Hausa, 'ig' for Igbo, 'yo' for Yoruba)

        Returns:
            str: Transcribed text

        Raises:
            ValueError: If the audio file is empty or invalid
            LocalSpeechToTextError: If the transcription fails
        """
        logger.info(f"🎤 Local STT: Transcribing audio ({len(audio_data)} bytes, language: {language})")
        
        if not audio_data:
            logger.error("❌ Local STT ERROR: Audio data is empty!")
            raise ValueError("Audio data cannot be empty")
        
        if len(audio_data) < 100:
            logger.warning(f"⚠️ Local STT WARNING: Audio data is very small ({len(audio_data)} bytes)")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio_data)
            temp_file_path = temp_file.name

        try:
            return await self._transcribe_with_client(self.client, self.primary_url, temp_file_path, language)
        except Exception as primary_err:
            if self.fallback_url:
                logger.warning(f"⚠️ Primary STT failed ({primary_err}), switching to fallback: {self.fallback_url}")
                try:
                    fallback_client = self._make_client(self.fallback_url)
                    return await self._transcribe_with_client(fallback_client, self.fallback_url, temp_file_path, language)
                except Exception as fallback_err:
                    logger.error(f"❌ Fallback STT also failed: {fallback_err}")
                    raise LocalSpeechToTextError(f"Speech-to-text conversion failed: {fallback_err}") from fallback_err
            raise LocalSpeechToTextError(f"Speech-to-text conversion failed: {primary_err}") from primary_err
        finally:
            os.unlink(temp_file_path)

    async def _transcribe_with_client(self, client: OpenAI, url: str, temp_file_path: str, language: str) -> str:
        logger.info(f"📤 Local STT: Sending to {url}")
        try:
            with open(temp_file_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text",
                    language=language
                )
            if not transcription or not transcription.strip():
                raise LocalSpeechToTextError("Transcription result is empty")
            logger.info(f"✅ Local STT: Transcribed: '{transcription}'")
            return transcription.strip()
        except LocalSpeechToTextError:
            raise
        except Exception as e:
            logger.error(f"❌ Local STT ERROR ({url}): {type(e).__name__}: {str(e)}")
            raise

