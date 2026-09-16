"""
ElevenLabs text-to-speech.

Used for languages the self-hosted Chatterbox service has no adapter for.
Arabic is the case that forced it: asked for "ar" Chatterbox returns 400, and
handed Arabic text untagged it produces fluent audio of words nobody wrote.
ElevenLabs renders the same sentence correctly — verified by transcribing its
output back and getting the input sentence returned.

The interface deliberately mirrors LocalTextToSpeech (`synthesize`,
`synthesize_sync`, MP3 bytes out), so services.py can hand either one to the
same call site and nothing downstream knows which provider spoke.
"""

import logging
import re

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://api.elevenlabs.io/v1"

# eleven_multilingual_v2 gave the cleanest Arabic of the three models tested and
# is the safe default. eleven_flash_v2_5 is quicker and was also accurate, so it
# is a reasonable swap if latency ever matters more than fidelity.
DEFAULT_MODEL = "eleven_multilingual_v2"

# "Sarah — mature, reassuring, confident". A calm voice is the right register
# for somebody being told how to get away from rising water.
DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"

MAX_CHARS = 5000


class ElevenLabsTextToSpeechError(Exception):
    pass


class ElevenLabsTextToSpeech:
    """TTS client for one language, backed by the ElevenLabs API."""

    def __init__(self, api_key: str, model: str = "", voice: str = "",
                 language_code: str = ""):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.default_voice = voice or DEFAULT_VOICE
        # ElevenLabs infers the language from the text on multilingual models.
        # The code is passed only where the model accepts it, as a hint.
        self.language_code = language_code
        logger.info(
            f"✅ ElevenLabs TTS ready — model: {self.model}, "
            f"voice: {self.default_voice}, lang hint: {self.language_code or 'auto'}"
        )

    def _clean(self, text: str) -> str:
        """Strip markup that would otherwise be read aloud.

        Mirrors LocalTextToSpeech._clean_text_for_tts so a reply sounds the same
        whichever provider speaks it.
        """
        text = re.sub(r"https?://(www\.)?", "", text)
        text = re.sub(r"\bwww\.", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # markdown links
        text = re.sub(r"\*+([^*]+)\*+", r"\1", text)           # bold/italic
        text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        text = re.sub(r" +", " ", text)
        return text.strip()

    def _payload(self, text: str) -> dict:
        body = {
            "text": text,
            "model_id": self.model,
            # Stability high enough that an emergency instruction is read evenly
            # rather than performed; similarity keeps the voice consistent
            # between the sentences of one reply.
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }
        if self.language_code and self.model.startswith("eleven_flash"):
            # Only the flash models accept an explicit language code; sending it
            # to the others is rejected.
            body["language_code"] = self.language_code
        return body

    def _prepare(self, text: str) -> str:
        if not text or not text.strip():
            raise ValueError("Input text cannot be empty")
        cleaned = self._clean(text)
        if not cleaned:
            raise ValueError("Input text is empty after cleaning")
        if len(cleaned) > MAX_CHARS:
            raise ValueError(f"Input text exceeds {MAX_CHARS} characters")
        return cleaned

    def _url(self, voice: str) -> str:
        return f"{API_ROOT}/text-to-speech/{voice or self.default_voice}"

    def _headers(self) -> dict:
        return {"xi-api-key": self.api_key, "Content-Type": "application/json"}

    def _check(self, response: httpx.Response) -> bytes:
        if response.status_code != 200:
            # The body carries the reason (quota exhausted, bad voice id, an
            # unsupported language for this model). Without it a failure is
            # indistinguishable from a network blip.
            raise ElevenLabsTextToSpeechError(
                f"ElevenLabs returned {response.status_code}: {response.text[:300]}"
            )
        if not response.content:
            raise ElevenLabsTextToSpeechError("ElevenLabs returned empty audio")
        return response.content

    async def synthesize(self, text: str, voice: str = "") -> bytes:
        cleaned = self._prepare(text)
        logger.info(f"🔊 ElevenLabs TTS: {len(cleaned)} chars ({self.model})")
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                self._url(voice), headers=self._headers(), json=self._payload(cleaned)
            )
        audio = self._check(response)
        logger.info(f"✅ ElevenLabs TTS: {len(audio)} bytes")
        return audio

    def synthesize_sync(self, text: str, voice: str = "") -> bytes:
        """Blocking variant — app.py calls this inside asyncio.to_thread, one
        task per sentence, so replies are synthesised in parallel."""
        cleaned = self._prepare(text)
        logger.info(f"🔊 ElevenLabs TTS (sync): {len(cleaned)} chars ({self.model})")
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                self._url(voice), headers=self._headers(), json=self._payload(cleaned)
            )
        audio = self._check(response)
        logger.info(f"✅ ElevenLabs TTS: {len(audio)} bytes")
        return audio
