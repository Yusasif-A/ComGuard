from openai import OpenAI
import logging
import re
import os
import requests

logger = logging.getLogger(__name__)

# Yoruba number words for list markers (1–20)
_YORUBA_NUMBERS = {
    '1': 'ọkan', '2': 'èjì', '3': 'ẹta', '4': 'ẹrin', '5': 'àrún',
    '6': 'ẹfà', '7': 'èjẹ̀', '8': 'ẹjọ', '9': 'ẹsàn', '10': 'ẹwà',
    '11': 'ọkanlá', '12': 'ejìlá', '13': 'ẹtalá', '14': 'ẹrinlá',
    '15': 'ẹẹdọgún', '16': 'ẹrìndínlógún', '17': 'ẹtàdínlógún',
    '18': 'ejìdínlógún', '19': 'ọkàndínlógún', '20': 'ogún',
}


def yoruba_numbers_to_words(text: str) -> str:
    """Replace numbered list markers with Yoruba words — use this before TTS only.
    e.g. '1. Mọ ìsanwó' → 'ọkan. Mọ ìsanwó', '2.Gba' → 'èjì. Gba'
    Matches 1-2 digit number + dot not followed by another digit (avoids decimals).
    """
    def _replace(m):
        return _YORUBA_NUMBERS.get(m.group(1), m.group(1)) + '. '
    return re.sub(r'(\d{1,2})\.(?!\d)\s*', _replace, text)


class NLLBTranslator:
    """Translation service using NLLB model for Hausa, Igbo, Yoruba ↔ English"""
    
    def __init__(self, hausa_url=None, igbo_url=None, yoruba_url=None):
        """Initialize NLLB clients for Hausa, Igbo, and Yoruba"""
        
        # Hausa NLLB client
        self.hausa_url = hausa_url or os.getenv("HAUSA_NLLB_URL", "")
        if not self.hausa_url.endswith('/v1'):
            self.hausa_url = self.hausa_url.rstrip('/') + '/v1'
        self.hausa_client = OpenAI(
            base_url=self.hausa_url, 
            api_key="fake_key"  # API key is ignored
        )
        self.hausa_model = "nllb-hausa"
        
        # Igbo NLLB client
        self.igbo_url = igbo_url or os.getenv("IGBO_NLLB_URL", "")
        if not self.igbo_url.endswith('/v1'):
            self.igbo_url = self.igbo_url.rstrip('/') + '/v1'
        self.igbo_client = OpenAI(
            base_url=self.igbo_url,
            api_key="fake_key"
        )
        self.igbo_model = "nllb-igbo"
        
        # Yoruba NLLB client
        self.yoruba_url = yoruba_url or os.getenv("YORUBA_NLLB_URL", "")
        if not self.yoruba_url.endswith('/v1'):
            self.yoruba_url = self.yoruba_url.rstrip('/') + '/v1'
        self.yoruba_client = OpenAI(
            base_url=self.yoruba_url,
            api_key="fake_key"
        )
        self.yoruba_model = "nllb-yoruba"
        
        logger.info(f"✅ NLLB Translator initialized")
        logger.info(f"   Hausa: {self.hausa_url} (model: {self.hausa_model})")
        logger.info(f"   Igbo: {self.igbo_url} (model: {self.igbo_model})")
        logger.info(f"   Yoruba: {self.yoruba_url} (model: {self.yoruba_model})")
    
    def hausa_to_english(self, hausa_text: str) -> str:
        if not hausa_text or not hausa_text.strip():
            logger.warning("⚠️ Empty Hausa text provided")
            return ""
        try:
            logger.info(f"🔄 Translating Hausa→English: '{hausa_text[:100]}...'")
            response = self.hausa_client.chat.completions.create(
                model=self.hausa_model,
                messages=[{"role": "user", "content": hausa_text}],
                temperature=0.1,
                max_tokens=4096,
                extra_body={"direction": "hausa_to_english", "max_tokens": 4096}
            )
            english_text = response.choices[0].message.content.strip()
            logger.info(f"✅ Translation complete: '{english_text[:100]}...'")
            return english_text
        except Exception as e:
            logger.error(f"❌ Hausa→English translation failed: {e}")
            raise Exception(f"Translation failed: {str(e)}")
    
    def english_to_hausa(self, english_text: str) -> str:
        if not english_text or not english_text.strip():
            logger.warning("⚠️ Empty English text provided")
            return ""
        try:
            english_text = self._preprocess_english_for_translation(english_text, "ha")
            logger.info(f"🔄 Translating English→Hausa: '{english_text[:100]}...'")
            response = self.hausa_client.chat.completions.create(
                model=self.hausa_model,
                messages=[{"role": "user", "content": english_text}],
                temperature=0.1,
                max_tokens=4096,
                extra_body={"direction": "english_to_hausa", "max_tokens": 4096}
            )
            hausa_text = self._postprocess_common(response.choices[0].message.content.strip())
            logger.info(f"✅ Translation complete: '{hausa_text[:100]}...'")
            return hausa_text
        except Exception as e:
            logger.error(f"❌ English→Hausa translation failed: {e}")
            raise Exception(f"Translation failed: {str(e)}")

    def igbo_to_english(self, igbo_text: str) -> str:
        if not igbo_text or not igbo_text.strip():
            logger.warning("⚠️ Empty Igbo text provided")
            return ""
        try:
            logger.info(f"🔄 Translating Igbo→English: '{igbo_text[:100]}...'")
            response = self.igbo_client.chat.completions.create(
                model=self.igbo_model,
                messages=[{"role": "user", "content": igbo_text}],
                temperature=0.1,
                max_tokens=4096,
                extra_body={"direction": "igbo_to_english", "max_tokens": 4096}
            )
            english_text = response.choices[0].message.content.strip()
            logger.info(f"✅ Translation complete: '{english_text[:100]}...'")
            return english_text
        except Exception as e:
            logger.error(f"❌ Igbo→English translation failed: {e}")
            raise Exception(f"Translation failed: {str(e)}")
    
    def english_to_igbo(self, english_text: str) -> str:
        if not english_text or not english_text.strip():
            logger.warning("⚠️ Empty English text provided")
            return ""
        try:
            english_text = self._preprocess_english_for_translation(english_text, "ig")
            logger.info(f"🔄 Translating English→Igbo: '{english_text[:100]}...'")
            response = self.igbo_client.chat.completions.create(
                model=self.igbo_model,
                messages=[{"role": "user", "content": english_text}],
                temperature=0.1,
                max_tokens=4096,
                extra_body={"direction": "english_to_igbo", "max_tokens": 4096}
            )
            igbo_text = self._postprocess_common(response.choices[0].message.content.strip())
            logger.info(f"✅ Translation complete: '{igbo_text[:100]}...'")
            return igbo_text
        except Exception as e:
            logger.error(f"❌ English→Igbo translation failed: {e}")
            raise Exception(f"Translation failed: {str(e)}")

    @staticmethod
    def _postprocess_common(text: str) -> str:
        """Format NLLB output for all languages:
        - Strip <unk> tokens (NLLB cannot translate emojis/special chars)
        - Remove mailto: artifacts injected by NLLB
        - Add blank line before each numbered list item (1. 2. 3. ...)
        - Collapse excessive blank lines
        """
        # Remove <unk> tokens (with any trailing space/punctuation glued to them)
        text = re.sub(r'<unk>\s*', '', text)
        # Remove mailto:... artifacts
        text = re.sub(r'\s*mailto:\S+', '', text)
        # Add double newline before numbered list items (not decimal numbers)
        text = re.sub(r'(?<!\n)\s*(\d{1,2})\.(?!\d)\s+', r'\n\n\1. ', text)
        # Collapse 3+ newlines to 2
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ── Food name maps: English → native name per language ──────────────────
    _FOOD_MAP_YORUBA = {
        "stew": "obẹ", "pepper stew": "obẹ ata", "tomato stew": "obẹ tomati",
        "soup": "obẹ",
        "yam": "isu", "yams": "isu",
        "pounded yam": "iyán", "pounded yam flour": "iyán",
        "cassava": "paki", "cassava flour": "paki",
        "garri": "gàárì", "eba": "eba",
        "fufu": "ìyán", "semovita": "semovita",
        "beans": "ẹ̀wà", "cowpea": "ẹ̀wà", "black eyed peas": "ẹ̀wà",
        "groundnut": "ẹ̀pà", "peanut": "ẹ̀pà", "peanuts": "ẹ̀pà", "groundnuts": "ẹ̀pà",
        "palm oil": "epo pupa",
        "palm kernel": "epo igi ope",
        "plantain": "ogede agbado", "ripe plantain": "ogede",
        "unripe plantain": "ogede ọmọ",
        "corn": "agbado", "maize": "agbado",
        "sorghum": "oka baba",
        "millet": "oka",
        "rice": "iresi",
        "egusi": "egusi", "melon seeds": "egusi",
        "crayfish": "ẹja kẹtẹ", "dried crayfish": "ẹja kẹtẹ",
        "stockfish": "panla", "dried fish": "ẹja gbígbẹ",
        "catfish": "ẹja aro",
        "tilapia": "ẹja",
        "beef": "ẹran malu", "meat": "ẹran",
        "chicken": "adie", "poultry": "adie",
        "egg": "ẹyin", "eggs": "ẹyin",
        "milk": "wara", "breast milk": "ọmu",
        "pap": "ògì", "akamu": "ògì",
        "akara": "akara",
        "moi moi": "mọ̀ínmọ̀ín",
        "ewedu": "ewedu",
        "gbegiri": "gbẹgiri",
        "ogi": "ògì",
        "tuwo": "tuwo",
        "okra": "ila", "okro": "ila",
        "bitter leaf": "ewuro",
        "waterleaf": "gbure",
        "ugu": "ugu", "pumpkin leaves": "ugu",
        "spinach": "efo tete",
        "tomato": "tomati", "tomatoes": "tomati",
        "pepper": "ata",
        "onion": "alubosa", "onions": "alubosa",
        "garlic": "alubosa ajo",
        "ginger": "ata ile",
        "locust beans": "iru",
        "ogiri": "ogiri",
        "orange": "osan", "oranges": "osan",
        "banana": "ogede", "bananas": "ogede",
        "pawpaw": "ibepe", "papaya": "ibepe",
        "mango": "mangoro",
        "avocado": "pia",
        "soybeans": "awara", "soya": "awara",
        "ede": "ẹsu", "cocoyam": "ẹsu",
        "sweet potato": "anamo",
        "irish potato": "iresi isu",
        "fish": "eja",
    }

    _FOOD_MAP_HAUSA = {
        "yam": "doya", "yams": "doya",
        "pounded yam": "tuwo doya",
        "cassava": "rogo",
        "garri": "gari",
        "fufu": "tuwo",
        "tuwo shinkafa": "tuwo shinkafa",
        "tuwo masara": "tuwo masara",
        "beans": "wake", "cowpea": "wake", "black eyed peas": "wake",
        "groundnut": "gyada", "peanut": "gyada", "peanuts": "gyada", "groundnuts": "gyada",
        "groundnut oil": "man gyada",
        "palm oil": "man tafasasshe",
        "plantain": "ayaba",
        "corn": "masara", "maize": "masara",
        "sorghum": "dawa",
        "millet": "gero",
        "rice": "shinkafa",
        "egusi": "egushi",
        "crayfish": "kifi kanana", "dried crayfish": "kifi mai bushewa",
        "stockfish": "kifi mai bushe",
        "dried fish": "kifi mai bushewa",
        "catfish": "kifi karo",
        "beef": "naman sa", "meat": "nama",
        "chicken": "kaza", "poultry": "kaza",
        "egg": "kwai", "eggs": "kwai",
        "milk": "madara", "breast milk": "nonon uwa",
        "pap": "kunu", "akamu": "kunu",
        "fura": "fura",
        "fura da nono": "fura da nono",
        "okra": "kubewa", "okro": "kubewa",
        "bitter leaf": "shuwaka",
        "spinach": "alayyahu",
        "tomato": "tumatir", "tomatoes": "tumatir",
        "pepper": "barkono",
        "onion": "albasa", "onions": "albasa",
        "garlic": "tafarnuwa",
        "ginger": "citta",
        "locust beans": "dawadawa",
        "orange": "lemu", "oranges": "lemu",
        "banana": "ayaba", "bananas": "ayaba",
        "pawpaw": "gwanda", "papaya": "gwanda",
        "mango": "mangwaro",
        "sweet potato": "dankali",
        "irish potato": "dankalin turawa",
        "soybeans": "wake soya",
        "fish": "kifi",
        "cocoyam": "gwaza",
        "watermelon": "kankana",
    }

    _FOOD_MAP_IGBO = {
        "yam": "ji", "yams": "ji",
        "pounded yam": "ji ikwe",
        "cassava": "akpu", "cassava fufu": "akpu",
        "garri": "garri",
        "fufu": "akpu",
        "beans": "agwa", "cowpea": "agwa", "black eyed peas": "agwa",
        "groundnut": "ahụekere", "peanut": "ahụekere", "peanuts": "ahụekere",
        "palm oil": "mmanu nri",
        "palm kernel": "ọkpa",
        "plantain": "ogede",
        "corn": "ọka", "maize": "ọka",
        "sorghum": "ọka ocha",
        "millet": "ọka nri",
        "rice": "osikapa",
        "egusi": "egusi",
        "crayfish": "ose oji", "dried crayfish": "ose oji",
        "stockfish": "okporoko",
        "dried fish": "azụ ọkụ",
        "catfish": "azụ nkota",
        "beef": "anụ efi", "meat": "anụ",
        "chicken": "okuko", "poultry": "okuko",
        "egg": "akwa okuko", "eggs": "akwa okuko",
        "milk": "mmiri ara", "breast milk": "ara",
        "pap": "akamu",
        "oha soup": "oha",
        "nsala soup": "nsala",
        "abacha": "abacha",
        "ukwa": "ukwa",
        "ugba": "ugba",
        "okra": "okwuru", "okro": "okwuru",
        "bitter leaf": "onugbu",
        "waterleaf": "mgbolodi",
        "ugu": "ugu", "pumpkin leaves": "ugu",
        "spinach": "ede nri",
        "tomato": "tomato", "tomatoes": "tomato",
        "pepper": "ose", "pepper soup": "ofe ose",
        "onion": "yabasị", "onions": "yabasị",
        "garlic": "tafarnuwa",
        "ginger": "jinja",
        "locust beans": "ogiri",
        "orange": "ọrọba", "oranges": "ọrọba",
        "banana": "unere", "bananas": "unere",
        "pawpaw": "ọ̀gbụ̀gbụ̀", "papaya": "ọ̀gbụ̀gbụ̀",
        "mango": "mangoro",
        "sweet potato": "anụ ji",
        "irish potato": "ji oyibo",
        "soybeans": "agwa soya",
        "fish": "azụ",
        "cocoyam": "ede",
        "breadfruit": "ukwa",
    }

    @classmethod
    def _preprocess_english_for_translation(cls, text: str, lang: str) -> str:
        """Replace English food names with correct native equivalents before NLLB translation.
        This prevents NLLB from mistranslating common Nigerian food names.
        Uses word-boundary matching and is case-insensitive.
        """
        food_map = {
            "yo": cls._FOOD_MAP_YORUBA,
            "ha": cls._FOOD_MAP_HAUSA,
            "ig": cls._FOOD_MAP_IGBO,
        }.get(lang, {})

        for english, native in sorted(food_map.items(), key=lambda x: -len(x[0])):
            text = re.sub(
                r'\b' + re.escape(english) + r'\b',
                native,
                text,
                flags=re.IGNORECASE
            )
        return text

    @staticmethod
    def _preprocess_yoruba_query(text: str) -> str:
        """Replace Yoruba colloquial phrases that NLLB struggles with.
        Strips diacritics first so STT output like 'bàwọní' still matches.
        """
        import re
        import unicodedata
        # Strip diacritics (tone marks) so STT variants like bàwọní → bawoni
        normalized = unicodedata.normalize('NFD', text)
        normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
        # "bawoni mosele" → "how do i" (more specific, must come first)
        normalized = re.sub(r'\bbawoni\s+mosele\b', 'how do i', normalized, flags=re.IGNORECASE)
        # "bawoni" alone → "how can i"
        normalized = re.sub(r'\bbawoni\b', 'how can i', normalized, flags=re.IGNORECASE)
        # "beeni" → "yes"
        normalized = re.sub(r'\bbeeni\b', 'yes', normalized, flags=re.IGNORECASE)
        return normalized

    def yoruba_to_english(self, yoruba_text: str) -> str:
        if not yoruba_text or not yoruba_text.strip():
            logger.warning("⚠️ Empty Yoruba text provided")
            return ""
        try:
            yoruba_text = self._preprocess_yoruba_query(yoruba_text)
            logger.info(f"🔄 Translating Yoruba→English: '{yoruba_text[:100]}...'")
            response = self.yoruba_client.chat.completions.create(
                model=self.yoruba_model,
                messages=[{"role": "user", "content": yoruba_text}],
                temperature=0.1,
                max_tokens=4096,
                extra_body={"direction": "yoruba_to_english", "max_tokens": 4096}
            )
            english_text = response.choices[0].message.content.strip()
            logger.info(f"✅ Translation complete: '{english_text[:100]}...'")
            return english_text
        except Exception as e:
            logger.error(f"❌ Yoruba→English translation failed: {e}")
            raise Exception(f"Translation failed: {str(e)}")
    
    def english_to_yoruba(self, english_text: str) -> str:
        if not english_text or not english_text.strip():
            logger.warning("⚠️ Empty English text provided")
            return ""
        try:
            english_text = self._preprocess_english_for_translation(english_text, "yo")
            logger.info(f"🔄 Translating English→Yoruba: '{english_text[:100]}...'")
            response = self.yoruba_client.chat.completions.create(
                model=self.yoruba_model,
                messages=[{"role": "user", "content": english_text}],
                temperature=0.1,
                max_tokens=4096,
                extra_body={"direction": "english_to_yoruba", "max_tokens": 4096}
            )
            yoruba_text = self._postprocess_common(response.choices[0].message.content.strip())
            logger.info(f"✅ Translation complete: '{yoruba_text[:100]}...'")
            return yoruba_text
        except Exception as e:
            logger.error(f"❌ English→Yoruba translation failed: {e}")
            raise Exception(f"Translation failed: {str(e)}")


# ──────────────────────────────────────────────────────────────────────────────
# HelpMum unified translator — single endpoint, all 3 languages
# Old NLLBTranslator above is kept as fallback reference
# ──────────────────────────────────────────────────────────────────────────────
class HelpMumTranslator:
    """
    Translation via the HelpMum unified /translate endpoint.
    Supports en↔yo, en↔ig, en↔ha in one service.
    Falls back to NLLBTranslator if the endpoint is unavailable.
    """

    # Valid 2-letter code pairs
    _VALID_PAIRS = {
        ("en", "yo"), ("yo", "en"),
        ("en", "ig"), ("ig", "en"),
        ("en", "ha"), ("ha", "en"),
    }

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or os.getenv("TRANSLATOR_URL", "")).rstrip("/")
        logger.info(f"✅ HelpMumTranslator initialised — endpoint: {self.base_url or '(not set)'}")

    def _translate(self, text: str, src: str, tgt: str) -> str:
        if not text or not text.strip():
            return ""
        if (src, tgt) not in self._VALID_PAIRS:
            raise ValueError(f"Unsupported translation pair: {src} → {tgt}")
        # Preprocess English food names before sending to translation model
        if src == "en":
            text = NLLBTranslator._preprocess_english_for_translation(text, tgt)
        url = f"{self.base_url}/translate"
        logger.info(f"🔄 HelpMum {src}→{tgt}: '{text[:80]}...'")
        resp = requests.post(url, json={"text": text, "src_lang": src, "tgt_lang": tgt}, timeout=60)
        resp.raise_for_status()
        result = resp.json().get("output", "").strip()
        logger.info(f"✅ HelpMum translation done: '{result[:80]}...'")
        return NLLBTranslator._postprocess_common(result)

    # ── Public interface matches NLLBTranslator exactly ──
    def english_to_yoruba(self, text: str) -> str:
        return self._translate(text, "en", "yo")

    def yoruba_to_english(self, text: str) -> str:
        return self._translate(text, "yo", "en")

    def english_to_igbo(self, text: str) -> str:
        return self._translate(text, "en", "ig")

    def igbo_to_english(self, text: str) -> str:
        return self._translate(text, "ig", "en")

    def english_to_hausa(self, text: str) -> str:
        return self._translate(text, "en", "ha")

    def hausa_to_english(self, text: str) -> str:
        return self._translate(text, "ha", "en")