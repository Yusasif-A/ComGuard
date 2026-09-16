"""
Hausa Text-to-Speech Service
"""
import re
from typing import Optional
from openai import OpenAI
import logging

logger = logging.getLogger(__name__)

class LocalTextToSpeechError(Exception):
    """Custom exception for local text-to-speech errors."""
    pass

class LocalTextToSpeech:
    """A class to handle regional text-to-speech conversion."""

    def __init__(self, base_url: str, model: str, fallback_url: Optional[str] = None):
        """
        Initialize the Local TTS class

        Args:
            base_url: Primary base URL for the TTS API
            model: Model name to use for TTS
            fallback_url: Fallback URL to use if primary fails
        """
        self.primary_url = base_url
        self.fallback_url = fallback_url
        self.client = OpenAI(api_key="not-needed", base_url=self.primary_url)
        self.model = model
        logger.info(f"✅ Local TTS API URL: {self.primary_url}, Model: {self.model} | fallback: {self.fallback_url or 'none'}")

    def _clean_text_for_tts(self, text: str) -> str:
        """Clean text by removing contact information and markdown formatting.
        
        Args:
            text: Raw text that may contain contact info and markdown
            
        Returns:
            Cleaned text suitable for TTS
        """
        # Strip https:// / http:// / www. prefixes — keep domain so TTS reads it
        text = re.sub(r'https?://(www\.)?', '', text)
        text = re.sub(r'\bwww\.', '', text)

        # Remove markdown links [text](url) - keep only the text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

        # Remove bold markdown **text**
        text = re.sub(r'\*\*([^\*]+)\*\*', r'\1', text)
        
        # Remove bullet points (- at start of line)
        text = re.sub(r'^\s*-\s+', '', text, flags=re.MULTILINE)
        
        # Remove numbered list formatting (1. 2. 3. etc)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        # Clean up extra whitespace and newlines
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        text = text.strip()
        
        return text

    async def synthesize(self, text: str, voice: str = "regional_voice") -> bytes:
        """Convert text to speech.

        Args:
            text: Text to convert to speech
            voice: Voice identifier for Local TTS

        Returns:
            bytes: Audio data in MP3 format

        Raises:
            ValueError: If the input text is empty or too long
            LocalTextToSpeechError: If the text-to-speech conversion fails
        """
        if not text.strip():
            raise ValueError("Input text cannot be empty")

        # Clean text
        cleaned_text = self._clean_text_for_tts(text)
        
        if not cleaned_text.strip():
            raise ValueError("Input text is empty after cleaning")

        if len(cleaned_text) > 5000:
            raise ValueError("Input text exceeds maximum length of 5000 characters")

        try:
            return self._synthesize_with_client(self.client, self.primary_url, cleaned_text, voice)
        except Exception as primary_err:
            if self.fallback_url:
                logger.warning(f"⚠️ Primary TTS failed ({primary_err}), switching to fallback: {self.fallback_url}")
                try:
                    fallback_client = OpenAI(api_key="not-needed", base_url=self.fallback_url)
                    return self._synthesize_with_client(fallback_client, self.fallback_url, cleaned_text, voice)
                except Exception as fallback_err:
                    logger.error(f"❌ Fallback TTS also failed: {fallback_err}")
                    raise LocalTextToSpeechError(f"Text-to-speech conversion failed: {fallback_err}") from fallback_err
            raise LocalTextToSpeechError(f"Text-to-speech conversion failed: {primary_err}") from primary_err

    def _synthesize_with_client(self, client: OpenAI, url: str, cleaned_text: str, voice: str) -> bytes:
        logger.info(f"🔊 Local TTS Request → {url}: {len(cleaned_text)} chars")
        with client.audio.speech.with_streaming_response.create(
            model=self.model,
            voice=voice,
            input=cleaned_text
        ) as response:
            audio_bytes = response.read()
        if not audio_bytes:
            raise LocalTextToSpeechError("Generated audio is empty")
        logger.info(f"✅ Local TTS Success: Generated {len(audio_bytes)} bytes of audio")
        return audio_bytes

    def synthesize_sync(self, text: str, voice: str = "regional_voice") -> bytes:
        """Synchronous version of synthesize method.

        Args:
            text: Text to convert to speech
            voice: Voice identifier for Local TTS

        Returns:
            bytes: Audio data in MP3 format

        Raises:
            ValueError: If the input text is empty or too long
            LocalTextToSpeechError: If the text-to-speech conversion fails
        """
        if not text.strip():
            raise ValueError("Input text cannot be empty")

        cleaned_text = self._clean_text_for_tts(text)
        
        if not cleaned_text.strip():
            raise ValueError("Input text is empty after cleaning")

        if len(cleaned_text) > 5000:
            raise ValueError("Input text exceeds maximum length of 5000 characters")

        try:
            return self._synthesize_with_client(self.client, self.primary_url, cleaned_text, voice)
        except Exception as primary_err:
            if self.fallback_url:
                logger.warning(f"⚠️ Primary TTS failed ({primary_err}), switching to fallback: {self.fallback_url}")
                try:
                    fallback_client = OpenAI(api_key="not-needed", base_url=self.fallback_url)
                    return self._synthesize_with_client(fallback_client, self.fallback_url, cleaned_text, voice)
                except Exception as fallback_err:
                    logger.error(f"❌ Fallback TTS also failed: {fallback_err}")
                    raise LocalTextToSpeechError(f"Text-to-speech conversion failed: {fallback_err}") from fallback_err
            raise LocalTextToSpeechError(f"Text-to-speech conversion failed: {primary_err}") from primary_err
