"""
Translation between English and the languages in the registry.

ComGuard reasons in English and speaks to people in their own language, so every
reply crosses this module. Two implementations with the same interface:

  PivotTranslator  one endpoint that handles every pair (set TRANSLATOR_URL)
  NLLBTranslator   one endpoint per language, from the registry's nllb_url

Both expose ``to_english(text, language)`` and ``from_english(text, language)``,
so callers never branch on which one is in use or which language is involved.

A translation mistake here is not cosmetic. "Do not go near the water" becoming
"go near the water" is a safety failure, so the safest behaviour on error is to
return the English text unchanged rather than to guess — English is at least
readable to many users and is never dangerously wrong.
"""

import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Dict

import requests
from openai import OpenAI

logger = logging.getLogger(__name__)


# ── Yoruba numerals for spoken output ────────────────────────────────────────

_YORUBA_NUMBERS = {
    "1": "ọkan", "2": "èjì", "3": "ẹta", "4": "ẹrin", "5": "àrún",
    "6": "ẹfà", "7": "èjẹ̀", "8": "ẹjọ", "9": "ẹsàn", "10": "ẹwà",
    "11": "ọkanlá", "12": "ejìlá", "13": "ẹtalá", "14": "ẹrinlá",
    "15": "ẹẹdọgún", "16": "ẹrìndínlógún", "17": "ẹtàdínlógún",
    "18": "ejìdínlógún", "19": "ọkàndínlógún", "20": "ogún",
}


def yoruba_numbers_to_words(text: str) -> str:
    """Turn "1." list markers into Yoruba words. For TTS input only.

    A TTS voice reads a bare "1." as noise or skips it, so a numbered list of
    safety steps loses its ordering exactly when ordering matters most.
    """
    def _replace(m):
        return _YORUBA_NUMBERS.get(m.group(1), m.group(1)) + ". "
    return re.sub(r"(\d{1,2})\.(?!\d)\s*", _replace, text)


def numbers_to_words(text: str, language: str) -> str:
    """Language-aware numeral handling for spoken output."""
    if language == "yoruba":
        return yoruba_numbers_to_words(text)
    return text


# ── Glossary ─────────────────────────────────────────────────────────────────
#
# Terms a general-purpose translation model reliably gets wrong in this domain,
# and which people must understand exactly. Substituted into the English text
# before translation so the model carries the right word through.
#
# The built-in seed is deliberately tiny and covers only everyday words. The real
# glossary belongs in glossary.json next to this file, where a native speaker can
# edit it without touching code:
#
#   {"yoruba": {"flood": "ìkún omi", "higher ground": "orí òkè"}}
#
# ⚠️ Every entry here and in that file must be reviewed by a native speaker
# before the service is used in production. A confidently wrong safety word is
# worse than an untranslated English one.

_SEED_GLOSSARY: Dict[str, Dict[str, str]] = {
    "yoruba": {
        "flood": "ìkún omi",
        "fire": "iná",
        "police": "ọlọ́pàá",
        "hospital": "ilé ìwòsàn",
        "danger": "ewu",
        "money": "owó",
        "receipt": "ìwé ẹ̀rí owó",
        "bridge": "afárá",
        "road": "ọ̀nà",
    },
}

_GLOSSARY_PATH = Path(__file__).with_name("glossary.json")


def _load_glossary() -> Dict[str, Dict[str, str]]:
    glossary = {lang: dict(terms) for lang, terms in _SEED_GLOSSARY.items()}
    if not _GLOSSARY_PATH.exists():
        return glossary
    try:
        loaded = json.loads(_GLOSSARY_PATH.read_text(encoding="utf-8"))
        for lang, terms in (loaded or {}).items():
            if isinstance(terms, dict):
                glossary.setdefault(lang, {}).update(
                    {str(k).lower(): str(v) for k, v in terms.items()}
                )
        logger.info(f"📖 Glossary loaded for: {sorted(glossary)}")
    except Exception as e:
        logger.error(f"❌ Could not read glossary.json ({e}) — using the built-in seed only")
    return glossary


GLOSSARY = _load_glossary()


def _apply_glossary(text: str, language: str) -> str:
    """Swap known terms into the target language before translating.

    Longest terms first, so "higher ground" is matched before "ground".
    """
    terms = GLOSSARY.get(language, {})
    for english in sorted(terms, key=len, reverse=True):
        text = re.sub(
            r"\b" + re.escape(english) + r"\b",
            terms[english],
            text,
            flags=re.IGNORECASE,
        )
    return text


# ── Shared text handling ─────────────────────────────────────────────────────

def _postprocess(text: str) -> str:
    """Tidy raw translation output.

    <unk> appears wherever the model met an emoji or symbol it has no token for;
    left in, it reads aloud as nonsense. The mailto: artefact is a known NLLB
    quirk on text containing an address.
    """
    text = re.sub(r"<unk>\s*", "", text)
    text = re.sub(r"\s*mailto:\S+", "", text)
    text = re.sub(r"(?<!\n)\s*(\d{1,2})\.(?!\d)\s+", r"\n\n\1. ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# Speech-to-text output arrives without tone marks and with spellings that vary
# by speaker, so these are matched on the stripped form. They are short function
# words the translator mishandles in isolation, which matters because a one-word
# reply is often the whole message ("ìkún omi" — "flood").
_INBOUND_FIXUPS = {
    "yoruba": [
        (r"\bbawoni\s+mose\b", "how do i"),
        (r"\bbawoni\b", "how can i"),
        (r"\bbeeni\b", "yes"),
        (r"\brara\b", "no"),
        (r"\bengbe\b", "help"),
        (r"\bgba mi\b", "help me"),
    ],
}


def _preprocess_inbound(text: str, language: str) -> str:
    fixups = _INBOUND_FIXUPS.get(language)
    if not fixups:
        return text
    normalised = _strip_diacritics(text)
    for pattern, replacement in fixups:
        normalised = re.sub(pattern, replacement, normalised, flags=re.IGNORECASE)
    return normalised


class _BaseTranslator:
    """Shared entry points. Subclasses implement _call()."""

    def _call(self, text: str, src: str, tgt: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def to_english(self, text: str, language: str) -> str:
        """Translate a user's message into English. Returns "" on empty input."""
        if not text or not text.strip() or language == "english":
            return text or ""
        try:
            prepared = _preprocess_inbound(text, language)
            return self._call(prepared, _iso(language), "en").strip()
        except Exception as e:
            logger.error(f"❌ {language}→English translation failed: {e}")
            # The raw text still carries meaning to a multilingual model, so it
            # is more useful passed through than replaced with an error string.
            return text

    def from_english(self, text: str, language: str) -> str:
        """Translate a reply out of English. Returns the English on failure.

        Falling back to English is the safe failure: the person may struggle
        with it, but they are never given a mistranslated safety instruction.
        """
        if not text or not text.strip() or language == "english":
            return text or ""
        try:
            prepared = _apply_glossary(text, language)
            return _postprocess(self._call(prepared, "en", _iso(language)))
        except Exception as e:
            logger.error(f"❌ English→{language} translation failed: {e}")
            return text


# ISO codes the translation endpoints expect. Sourced from the registry so a new
# language needs no entry here.
def _iso(language_key: str) -> str:
    from config import get_language
    return get_language(language_key).whisper_code


class PivotTranslator(_BaseTranslator):
    """One HTTP endpoint serving every language pair (TRANSLATOR_URL)."""

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or os.getenv("TRANSLATOR_URL", "")).rstrip("/")
        logger.info(f"✅ PivotTranslator ready — {self.base_url or '(not set)'}")

    def _call(self, text: str, src: str, tgt: str) -> str:
        if not self.base_url:
            raise RuntimeError("TRANSLATOR_URL is not configured")
        logger.info(f"🔄 {src}→{tgt}: '{text[:80]}'")
        response = requests.post(
            f"{self.base_url}/translate",
            json={"text": text, "src_lang": src, "tgt_lang": tgt},
            timeout=60,
        )
        response.raise_for_status()
        return response.json().get("output", "").strip()


class NLLBTranslator(_BaseTranslator):
    """One OpenAI-compatible NLLB endpoint per language.

    Built from a {language_key: url} map so adding a language is a registry
    change, not a new attribute and a new pair of methods per direction.
    """

    def __init__(self, endpoints: Dict[str, str]):
        self.clients = {}
        for language, url in (endpoints or {}).items():
            if not url:
                continue
            normalised = url.rstrip("/")
            if not normalised.endswith("/v1"):
                normalised += "/v1"
            self.clients[language] = {
                "client": OpenAI(base_url=normalised, api_key="not-needed"),
                "model": f"nllb-{language}",
                "url": normalised,
            }
        logger.info(f"✅ NLLBTranslator ready — {sorted(self.clients) or 'no endpoints'}")

    def _language_for_iso(self, iso: str) -> str:
        from config import LANGUAGES
        for key, lang in LANGUAGES.items():
            if lang.whisper_code == iso:
                return key
        return iso

    def _call(self, text: str, src: str, tgt: str) -> str:
        # Whichever side is not English identifies the endpoint to use.
        language = self._language_for_iso(tgt if src == "en" else src)
        entry = self.clients.get(language)
        if not entry:
            raise RuntimeError(f"No NLLB endpoint configured for {language}")

        direction = f"english_to_{language}" if src == "en" else f"{language}_to_english"
        logger.info(f"🔄 NLLB {direction}: '{text[:80]}'")
        response = entry["client"].chat.completions.create(
            model=entry["model"],
            messages=[{"role": "user", "content": text}],
            temperature=0.1,
            max_tokens=4096,
            extra_body={"direction": direction, "max_tokens": 4096},
        )
        return response.choices[0].message.content.strip()
