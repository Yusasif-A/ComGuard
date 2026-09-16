import os
import re
from typing import Literal
from openai import OpenAI



class TextToSpeechError(Exception):
    """Custom exception for text-to-speech errors."""
    pass

class TextToSpeech:
    """A class to handle text-to-speech conversion using OpenAI-compatible TTS API."""

    def __init__(self):
        """Initialize the TextToSpeech class with OpenAI client."""
        self.primary_url = os.getenv("ENGLISH_TTS_BASE_URL", "")
        self.fallback_url = os.getenv("ENGLISH_TTS_FALLBACK_URL", "")
        self.client = OpenAI(api_key="not-needed", base_url=self.primary_url)
        self.model = "nigerian-english-xtts"

    def _preprocess_text(self, text: str) -> str:
        """Preprocess text by removing markdown formatting.
        
        Args:
            text: Raw text that may contain contact info and markdown
            
        Returns:
            str: Cleaned text suitable for TTS
        """
        # Remove https:// and http:// and www. prefixes from URLs
        text = re.sub(r'https?://(www\.)?', '', text)
        text = re.sub(r'\bwww\.', '', text)

        # Remove parentheses but keep the text inside: (some text) → some text
        text = re.sub(r'\(([^)]*)\)', r'\1', text)

        # Remove markdown links [text](url) - keep only the text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        
        # Remove markdown bold **text** 
        text = re.sub(r'\*\*([^\*]+)\*\*', r'\1', text)
        
        # Remove numbered list markers: "1. " "2. " etc at start of lines
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

        # Remove bullet points at the start of lines (- or * followed by space)
        text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
        
        # Clean up extra whitespace and newlines
        text = re.sub(r'\n{3,}', '\n\n', text)  # Max 2 consecutive newlines
        text = re.sub(r' {2,}', ' ', text)  # Remove multiple spaces
        text = text.strip()
        
        return text

    async def synthesize(
        self, 
        text: str, 
        voice: Literal["male", "female2"] = "female2"
    ) -> bytes:
        """Convert text to speech using OpenAI-compatible TTS API.

        Args:
            text: Text to convert to speech
            voice: Voice type - either "male" or "female2" (default: "female2")

        Returns:
            bytes: Audio data in MP3 format (audio/mpeg) for WhatsApp compatibility

        Raises:
            ValueError: If the input text is empty or too long
            TextToSpeechError: If the text-to-speech conversion fails
        """
        if not text.strip():
            raise ValueError("Input text cannot be empty")

        if len(text) > 5000:  
            raise ValueError("Input text exceeds maximum length of 5000 characters")

        try:
            # Show original text
            print("=" * 80)
            print("📄 ORIGINAL TEXT RECEIVED BY TTS:")
            print("=" * 80)
            print(text)
            print("=" * 80)
            
            # Preprocess text to remove contact info and markdown
            cleaned_text = self._preprocess_text(text)
            
            if not cleaned_text.strip():
                raise ValueError("Text is empty after preprocessing")
            
            # Show cleaned text
            print("🧹 CLEANED TEXT (AFTER PREPROCESSING):")
            print("=" * 80)
            print(cleaned_text)
            print("=" * 80)
            print(f"📊 Stats: Original={len(text)} chars → Cleaned={len(cleaned_text)} chars (removed {len(text) - len(cleaned_text)} chars)")
            print(f"🎤 Voice: {voice}")
            print("=" * 80)
            
            try:
                audio_bytes = self._tts_with_client(self.client, self.primary_url, cleaned_text, voice)
            except Exception as primary_err:
                if self.fallback_url:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"⚠️ Primary English TTS failed ({primary_err}), switching to fallback: {self.fallback_url}"
                    )
                    fallback_client = OpenAI(api_key="not-needed", base_url=self.fallback_url)
                    audio_bytes = self._tts_with_client(fallback_client, self.fallback_url, cleaned_text, voice)
                else:
                    raise TextToSpeechError(f"Text-to-speech conversion failed: {primary_err}") from primary_err

            print(f"TTS Success: Generated {len(audio_bytes)} bytes of audio")
            return audio_bytes

        except TextToSpeechError:
            raise
        except Exception as e:
            if os.path.exists("temp_tts_output.mp3"):
                os.remove("temp_tts_output.mp3")
            raise TextToSpeechError(f"Text-to-speech conversion failed: {str(e)}") from e

    def _tts_with_client(self, client: OpenAI, url: str, cleaned_text: str, voice: str) -> bytes:
        print(f"🔊 English TTS Request → {url}")
        with client.audio.speech.with_streaming_response.create(
            model=self.model,
            voice=voice,
            input=cleaned_text
        ) as response:
            audio_bytes = response.read()
        if not audio_bytes:
            raise TextToSpeechError("Generated audio is empty")
        return audio_bytes

    def synthesize_sync(
        self, 
        text: str, 
        voice: Literal["male", "female2"] = "female2"
    ) -> bytes:
        """Synchronous version of synthesize method.

        Args:
            text: Text to convert to speech
            voice: Voice type - either "male" or "female2" (default: "female2")

        Returns:
            bytes: Audio data in MP3 format (audio/mpeg) for WhatsApp compatibility

        Raises:
            ValueError: If the input text is empty or too long
            TextToSpeechError: If the text-to-speech conversion fails
        """
        if not text.strip():
            raise ValueError("Input text cannot be empty")

        if len(text) > 5000:  
            raise ValueError("Input text exceeds maximum length of 5000 characters")

        try:
            # Show original text
            print("=" * 80)
            print("📄 ORIGINAL TEXT RECEIVED BY TTS (SYNC):")
            print("=" * 80)
            print(text)
            print("=" * 80)
            
            # Preprocess text to remove contact info and markdown
            cleaned_text = self._preprocess_text(text)
            
            if not cleaned_text.strip():
                raise ValueError("Text is empty after preprocessing")
            
            # Show cleaned text
            print("🧹 CLEANED TEXT (AFTER PREPROCESSING):")
            print("=" * 80)
            print(cleaned_text)
            print("=" * 80)
            print(f"📊 Stats: Original={len(text)} chars → Cleaned={len(cleaned_text)} chars (removed {len(text) - len(cleaned_text)} chars)")
            print(f"🎤 Voice: {voice}")
            print("=" * 80)
            
            try:
                audio_bytes = self._tts_with_client(self.client, self.primary_url, cleaned_text, voice)
            except Exception as primary_err:
                if self.fallback_url:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"⚠️ Primary English TTS (sync) failed ({primary_err}), switching to fallback: {self.fallback_url}"
                    )
                    fallback_client = OpenAI(api_key="not-needed", base_url=self.fallback_url)
                    audio_bytes = self._tts_with_client(fallback_client, self.fallback_url, cleaned_text, voice)
                else:
                    raise TextToSpeechError(f"Text-to-speech conversion failed: {primary_err}") from primary_err

            print(f"TTS Success: Generated {len(audio_bytes)} bytes of audio")
            return audio_bytes
            
        except Exception as e:
            # Clean up temp file in case of error
            if os.path.exists("temp_tts_output_sync.mp3"):
                os.remove("temp_tts_output_sync.mp3")
            raise TextToSpeechError(f"Text-to-speech conversion failed: {str(e)}") from e
