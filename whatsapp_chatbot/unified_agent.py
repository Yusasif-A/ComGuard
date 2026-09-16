import os
import re
import logging
import asyncio
from typing import Optional, AsyncGenerator
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.tools import tool
from memory import get_memory
from retriever import ensemble_retriever
from baby_tracker import save_weight, get_weight_history
from nutrition_prompt import (
    NUTRITION_SYSTEM_PROMPT,
    NUTRITION_VOICE_SYSTEM_PROMPT,
    MULTILINGUAL_NUTRITION_SYSTEM_PROMPT,
    MULTILINGUAL_NUTRITION_VOICE_SYSTEM_PROMPT
)

from dotenv import load_dotenv
load_dotenv()

memory = get_memory()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Cache disabled
cache_enabled = False

# Agency detection removed - not needed for nutrition advisor


# ===================== TOOLS =====================
@tool
async def nutrition_information(query: str) -> str:
    """
    Retrieve nutrition data from the Nigerian Food Composition and Micronutrient Survey (NFCMS 2021)
    and FAO INFOODS West Africa 2019 database.

    The database contains ANNEX tables with top Nigerian foods contributing to specific nutrients.
    Use targeted queries to retrieve the most relevant data. Examples:
    - "top foods contributing protein women Nigeria NFCMS"
    - "top foods contributing iron women Nigeria"
    - "top foods contributing vitamin A children Nigeria"
    - "top foods contributing calcium Nigeria"
    - "top foods contributing energy carbohydrate Nigeria"
    - "top foods contributing fat Nigeria"
    - "top protein foods by region South West Nigeria"
    - "nutrient composition garri eba cassava Nigeria"
    - "foods that boost breast milk galactagogues Nigeria"
    - "nutrient deficiency iron vitamin A zinc Nigerian children"

    Always query by specific nutrient name + "Nigeria" or "women" or "children" for best results.
    Call this tool for EVERY nutrition question before answering.
    """
    logger.info("\n" + "="*80)
    logger.info("🥗 TOOL: nutrition_information")
    logger.info(f"LLM Query: {query}")
    logger.info("="*80)

    try:
        docs = ensemble_retriever.invoke(query)
        logger.info(f"Retrieved {len(docs)} nutrition documents")

        if not docs:
            return "No specific nutrition information found in the database. Use your general knowledge about Nigerian foods and nutrition."

        formatted = []
        for doc in docs:
            meta = doc.metadata
            source = meta.get("source") or meta.get("title") or meta.get("file") or "NFCMS 2021"
            page = meta.get("page", "")
            ref = f"{source}, p.{page}" if page else source
            formatted.append(f"[Source: {ref}]\n\n{doc.page_content.strip()}")

        filtered = formatted[:2]
        result = "\n\n---\n\n".join(filtered)
        logger.info(f"✓ Returning {len(filtered)} nutrition docs to LLM\n")
        return result

    except Exception as e:
        logger.error(f"❌ Nutrition retrieval error: {e}")
        return "Could not retrieve specific data. Use your general knowledge about Nigerian foods and nutrition."

# Holds the phone number of the current request so the tool can write to the right record
_current_phone_number: str = ""

# Kept for display purposes (e.g. app.py's daily-tip prompt references this to
# name the language in its own instruction text to Gemma).
_LANGUAGE_NAMES = {
    "hausa": "Hausa",
    "igbo": "Igbo",
    "yoruba": "Yoruba",
}


# Short yes/no replies, per language. llama3-8b receives the user's message in
# their own language with no translation on the way in, and it does not reliably
# read Yoruba "Beeni" / "Ooto ni" (or Hausa "Na'am", Igbo "Ee") as agreement. The
# result was the bot re-asking "I can see moi moi and ogi. Is that correct?" over
# and over while the user kept confirming.
#
# Rather than hope the model understands, we detect these deterministically and
# hand it an explicit English gloss alongside the original words.
_AFFIRMATIONS = {
    "yoruba":  {"beeni", "bee ni", "ooto ni", "ootoni", "otooni", "ehn", "een",
                "eeni", "eni", "o daa", "odaa", "dada", "mo gba", "nitooto"},
    "hausa":   {"eh", "ee", "i", "naam", "na am", "to", "toh", "haka ne",
                "hakane", "gaskiya ne", "gaskiyane", "shi ke nan"},
    "igbo":    {"ee", "eeh", "eee", "o bu ya", "obu ya", "otu a", "ee ka",
                "eziokwu", "ọ bụ ya"},
    "english": {"yes", "yeah", "yep", "yup", "correct", "right", "true", "ok",
                "okay", "sure", "exactly", "that is correct", "thats correct"},
}

_NEGATIONS = {
    "yoruba":  {"rara", "beeko", "bee ko", "ko", "ko ri be", "kii se"},
    "hausa":   {"aa", "a a", "ba haka ba", "babu", "ba"},
    "igbo":    {"mba", "mba nu", "o bughi ya", "obughi ya", "ọ bụghị ya"},
    "english": {"no", "nope", "nah", "wrong", "incorrect", "not correct",
                "that is wrong", "thats wrong"},
}


def _normalise_reply(text: str) -> str:
    """Lowercase, drop accents and punctuation, so 'Bẹ́ẹ̀ni!' matches 'beeni'."""
    import unicodedata
    stripped = unicodedata.normalize("NFD", text.strip().lower())
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    # Apostrophes become spaces so Hausa "Na'am"/"A'a" normalise to the
    # same form as the entries below ("na am" / "a a").
    stripped = re.sub(r"[^\w\s]", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _annotate_short_reply(user_message: str, language: str) -> str:
    """Add an English gloss when the user sends a bare yes/no in their language.

    Only fires on SHORT messages, so a real sentence that happens to contain
    "ko" or "to" is never rewritten.
    """
    m = re.match(r"^(\[User profile:[^\]]*\]\s*)(.*)$", user_message, re.DOTALL)
    prefix, body = (m.group(1), m.group(2)) if m else ("", user_message)

    key = _normalise_reply(body)
    if not key or len(key) > 24:
        return user_message

    yes = _AFFIRMATIONS.get(language, set()) | _AFFIRMATIONS["english"]
    no = _NEGATIONS.get(language, set()) | _NEGATIONS["english"]

    if key in yes:
        gloss = (
            f'[The user replied "{body.strip()}". This means YES — they are confirming '
            f'what you just said. Do NOT repeat or re-ask that question. Continue to '
            f'the next step of the conversation.]\n\n'
        )
        return prefix + gloss + body
    if key in no:
        gloss = (
            f'[The user replied "{body.strip()}". This means NO — they are disagreeing '
            f'with what you just said. Ask them to tell you what is correct instead of '
            f'repeating your previous message.]\n\n'
        )
        return prefix + gloss + body
    return user_message


def _build_multilingual_user_message(language: str, text_content: str, original_content: Optional[str], is_voice: bool) -> str:
    """
    Build the human message for multilingual mode. Gemma receives the user's
    message directly in their own language (no NLLB translation on the way in —
    Gemma understands Yoruba/Hausa/Igbo natively), but per the system prompt it
    replies in English by default; app.py then runs Gemma's English reply
    through NLLB to produce the final native-language response, since NLLB's
    translation quality currently reads better than Gemma's own native
    generation in these languages.

    Bare yes/no replies get an English gloss (see _annotate_short_reply) because
    the model does not reliably read them in these languages on its own.
    """
    user_message = original_content if original_content else text_content
    user_message = _annotate_short_reply(user_message, language)

    if is_voice:
        return f"[Voice Message]: {user_message}"
    return user_message


@tool
async def log_baby_weight(baby_index: int, age_months: int, weight_kg: float) -> str:
    """
    Log a baby's weight and check it against WHO growth standards.

    Call this tool whenever the mother reports her baby's weight, e.g.:
    - "my baby is 4 months and weighs 5.2kg"
    - "I weighed my baby today, she is 3 months, 4.8kg"
    - "baby weight: 7kg, age 6 months"

    Args:
        baby_index: Which baby (1, 2, or 3). Use 1 if the mother has not specified.
        age_months: Baby's age in whole months.
        weight_kg: Baby's weight in kilograms.

    Returns a WHO growth assessment and any growth velocity warnings.
    """
    global _current_phone_number
    if not _current_phone_number:
        return "Could not save weight — phone number not available."

    try:
        result = save_weight(
            phone_number=_current_phone_number,
            baby_index=baby_index,
            age_months=age_months,
            weight_kg=weight_kg,
        )
        if "error" in result:
            return f"Could not save weight: {result['error']}"

        assessment = result["assessment"]
        velocity = result.get("velocity_warning")
        previous = result.get("previous")

        response_parts = [assessment["message"]]

        if velocity:
            response_parts.append(velocity)

        if previous:
            gain = weight_kg - previous["weight_kg"]
            response_parts.append(
                f"Since last record ({previous['weight_kg']}kg at {previous['age_months']} months), "
                f"baby has {'gained' if gain >= 0 else 'lost'} {abs(gain):.2f}kg."
            )

        logger.info(f"✅ Baby weight logged: baby#{baby_index} {weight_kg}kg at {age_months}mo — status: {assessment['alert_level']}")
        return " ".join(response_parts)

    except Exception as e:
        logger.error(f"❌ log_baby_weight error: {e}")
        return "Could not save baby weight at this time."


# ── Off-topic / prompt-probing refusal ───────────────────────────────────────
# Shown when someone probes for the system prompt or tries a jailbreak, and when
# the model's own output looks like it is leaking its instructions.
#
# These previously said "I only answer questions about Nigerian public services"
# — left over from the codebase this project was adapted from. That text told
# users (and anyone probing) that this bot used to be something else, which is
# both confusing for mothers and a small information leak in itself.
#
# Tone is a warm redirect rather than an accusation: nearly everyone who trips
# this is just curious, and the audience is mothers asking about food.
OFF_TOPIC_REFUSAL = {
    "english": (
        "I'm Chop Beta, your nutrition helper 🥦 I can only help with food and "
        "nutrition for you and your baby. What would you like to know?"
    ),
    "hausa": (
        "Ni ne Chop Beta, mai taimaka miki kan abinci mai gina jiki 🥦 Zan iya "
        "taimakawa ne kawai kan abinci da gina jiki gare ki da jaririnki. "
        "Me kike son sani?"
    ),
    "igbo": (
        "Abu m Chop Beta, onye na-enyere gi aka na nri 🥦 Enwere m ike inye aka "
        "naani na nri na ahuike nri maka gi na nwa gi. Gini ka i choro ima?"
    ),
    "yoruba": (
        "Emi ni Chop Beta, oluranlowo ijeunje re 🥦 Mo le ran o lowo nikan lori "
        "ounje ati ijeunje fun iwo ati omo re. Kini o fe mo?"
    ),
}
# NOTE: the Hausa/Igbo/Yoruba lines are best-effort and unaccented; please have a
# native speaker review them, same caveat as the tips in tips.py.


def off_topic_refusal(language: str = "english") -> str:
    """Refusal text in the user's language, falling back to English."""
    return OFF_TOPIC_REFUSAL.get(language, OFF_TOPIC_REFUSAL["english"])


# Patterns that indicate someone is probing for the system prompt or trying to
# re-role the assistant. These are deliberately CONTEXT-AWARE rather than bare
# substring checks.
#
# The original version blocked any message containing "instructions" or "act as"
# anywhere, which refused ordinary nutrition questions from mothers:
#   "What are the instructions for preparing pap for my baby?"
#   "Does honey act as a sweetener for babies?"
#   "I forgot the instructions on the tin of milk"
# All were rejected. Those words only signal an attack when aimed at the
# assistant's own configuration, so each pattern below requires that context.
#
# Note "the instructions" is deliberately NOT a trigger on its own — it is
# everyday language when talking about preparing food. An attacker has to say
# whose instructions ("your", "previous", "system") or ask to be shown them.
_INJECTION_PATTERNS = [
    # "system prompt", "your instructions", "previous rules" — but not "the instructions"
    r'\b(system|initial|original|previous|prior|your)\s+(prompt|instructions?|rules?)\b',
    # "reveal / show me / repeat ... prompt|instructions"
    r'\b(reveal|show|repeat|print|output|display|tell\s+me|give\s+me|what\s+is)\b.{0,40}'
    r'\b(prompt|instructions?|system\s+message|rules?|guidelines)\b',
    r'\bsystem\s+message\b',
    r'\bignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|your|instructions?|rules?|prompt)\b',
    r'\bdisregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|your|instructions?|rules?|prompt)\b',
    r'\bforget\s+(your|all|everything|previous|prior|the\s+above)\b',
    r'\byou\s+are\s+now\b',
    r'\bpretend\s+(you|to\s+be|that\s+you)\b',
    # "act as" only when aimed at the assistant, so "honey acts as a sweetener"
    # and "beans act as a replacement for meat" stay allowed.
    r'(?:^|\b(?:you|now|please)\s+(?:should\s+|must\s+|will\s+|can\s+)?)act\s+as\b',
    r'\bwhat\s+were\s+you\s+(told|given|instructed|programmed|trained)\b',
    r'\b(developer|debug|god)\s+mode\b',
    r'\bjailbreak\b',
    r'\brepeat\s+(the\s+)?(text|words|everything)\s+above\b',
]


def is_prompt_injection_attempt(user_input: str) -> bool:
    """True if the message is probing for the system prompt or trying to re-role
    the assistant. Tuned to avoid false positives on ordinary food questions."""
    clean = re.sub(r'\[.*?\]:\s*', '', user_input.lower()).strip()
    return any(re.search(p, clean) for p in _INJECTION_PATTERNS)


def contains_system_prompt_leak(text: str) -> bool:
    """Check if response contains actual system prompt leaks or model name reveals.
    Only blocks for serious leaks — NOT for phrases like 'the documents show'."""
    text_lower = text.lower()

    # System prompt leaks
    prompt_leaks = ["finetuned knowledge", "reference document", "system prompt", "query rewriting assistant"]

    # Model name and maker reveals
    model_reveals = [
        "gemma", "google gemma", "gemma-4", "gemma 4",
        "google model", "google ai", "google's model",
        "i am gemma", "i'm gemma", "my name is gemma",
        "powered by google", "created by google", "made by google",
    ]

    forbidden = prompt_leaks + model_reveals
    return any(term in text_lower for term in forbidden)


def normalize_query_for_cache(query: str) -> str:
    normalized = re.sub(r'\[name:[^\]]*\]\s*', '', query)
    normalized = re.sub(r'\[Voice Message\]:\s*', '', normalized)
    normalized = re.sub(r'\[voice\]:\s*', '', normalized)
    return normalized.strip()


def check_if_cacheable(user_input: str) -> bool:
    greetings = [
        "good morning", "good afternoon", "good evening",
        "thank you", "thanks", "good", "bye", "welldone",
        "goodbye", "yes", "no", "ok", "okay"
    ]
    return normalize_query_for_cache(user_input).lower() not in greetings

def search_redis_cache(query: str) -> Optional[str]:
    """Cache disabled - always return None"""
    return None


def _summarise_image_turn(text: str) -> str:
    """Collapse a past image turn into a neutral summary for history.

    The text sent alongside a food photo ends with an instruction:

        Look at the image and confirm what foods you can see. Respond
        naturally: 'I can see [food1], [food2], and [food3] in your meal.
        Is that correct?'

    Stripping only the image left that instruction sitting in history, so on
    every following turn the model read it again and re-ran it — asking
    "Is that correct?" over and over even after the user said yes. It also
    invented a third food to fill the [food3] slot when the detector had
    only found two, which is where the phantom "beans" came from.

    We keep what was actually established (the foods) and drop the
    instruction, so the model has context without being re-instructed.
    """
    profile = ""
    m = re.match(r'^(\[User profile:[^\]]*\]\s*)', text)
    if m:
        profile = m.group(1)

    foods = ""
    m = re.search(r'may be in the image:\s*(.*?)\.\s', text)
    if m:
        foods = m.group(1).strip()

    caption = ""
    m = re.search(r'\[Food Image\] Caption:\s*([^\n\[]*)', text)
    if m and m.group(1).strip():
        caption = f' They captioned it: "{m.group(1).strip()}".'

    detail = f" The foods identified were: {foods}." if foods else ""
    return (
        f"{profile}[Earlier in this conversation the user sent a photo of a meal."
        f"{detail}{caption} That photo has already been discussed — do NOT ask them "
        f"to confirm the foods again.]"
    )


def _strip_images_from_history(history: list) -> list:
    """Remove image data from old messages to prevent token overflow, and
    replace the instruction that accompanied them (see _summarise_image_turn)."""
    cleaned = []
    for msg in history:
        if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
            had_image = any(
                isinstance(p, dict) and p.get("type") == "image_url"
                for p in msg.content
            )
            new_parts = []
            for part in msg.content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    continue  # drop the image data entirely
                if had_image and isinstance(part, dict) and part.get("type") == "text":
                    new_parts.append(
                        {"type": "text", "text": _summarise_image_turn(part.get("text", ""))}
                    )
                else:
                    new_parts.append(part)
            cleaned.append(HumanMessage(content=new_parts or [{"type": "text", "text": "[image sent earlier]"}]))
        else:
            cleaned.append(msg)
    return cleaned


def _trim_history(history: list) -> list:
    """Keep only the last 2 complete turns from history (current turn will be the 3rd)."""
    human_indices = [i for i, m in enumerate(history) if isinstance(m, HumanMessage)]
    if len(human_indices) > 2:
        history = history[human_indices[-2]:]
        logger.info(f" Trimmed history to last 2 turns ({len(history)} messages kept)")
    return _strip_images_from_history(history)


def _parse_raw_tool_call(text: str) -> Optional[dict]:
    """
    Detect and parse raw tool call strings the model emits as plain text.
    e.g. call:log_baby_weight{age_months:6,weight_kg:7,baby_index:1}
         call:nutrition_information{query:iron foods Nigeria}
    Returns dict with 'name' and 'args', or None if no match.
    """
    import json
    m = re.search(r'call\s*:\s*(\w+)\s*\{([^}]*)\}', text, re.IGNORECASE)
    if not m:
        return None
    name = m.group(1)
    raw_args = m.group(2).strip()
    args = {}
    # Try JSON first (quoted keys/values)
    try:
        args = json.loads('{' + raw_args + '}')
    except Exception:
        # Fall back to key:value parsing
        for pair in re.split(r',(?![^{]*})', raw_args):
            kv = pair.strip().split(':', 1)
            if len(kv) == 2:
                k = kv[0].strip().strip('"')
                v = kv[1].strip().strip('"')
                try:
                    v = json.loads(v)
                except Exception:
                    pass
                args[k] = v
    return {"name": name, "args": args, "id": "raw_tool_call"}


def _clean_text(text: str) -> str:
    # Remove model tokens
    text = re.sub(r'<\|im_end\|>|<\|im_start\|>\w*', '', text)
    # Strip <think>...</think> reasoning blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Strip bare "thought" or "Thought" on its own line at the start
    text = re.sub(r'^(thought|Thought)\s*\n', '', text.lstrip())
    return text.lstrip()


class UnifiedAgent:
    def __init__(self):
        self.llm = None
        self.llm_with_tools = None
        self.agent = None
        self.memory = memory
        self.tools = [nutrition_information, log_baby_weight]

    async def initialize(self):
        logger.info("🥗 Initializing Beta Food Nutrition Agent")

        # Load Gemma configuration from environment
        gemma_base_url = os.getenv("GEMMA_BASE_URL", "")
        gemma_api_key = os.getenv("API_KEY", "")
        gemma_model = os.getenv("GEMMA_MODEL", "google/gemma-4-E4B-it")

        self.llm = ChatOpenAI(
            base_url=gemma_base_url,
            api_key=gemma_api_key,
            model=gemma_model,
            temperature=0.1,
            streaming=True,
            tags=["jeun_daada_nutrition"],
            stop=["<|eot_id|>"],
        )

        logger.info(f"✅ Gemma configured: {gemma_model} at {gemma_base_url}")

        self.llm_with_tools = self.llm.bind_tools(self.tools)

        self.agent = create_agent(
            model=self.llm,
            tools=self.tools,
            checkpointer=self.memory,
        )

        logger.info("✅ Beta Food agent initialized with nutrition_information tool")

    def _load_history(self, config: dict) -> list:
        """Load and trim message history from checkpoint."""
        try:
            state = self.agent.get_state(config)
            history = state.values.get("messages", []) if state and state.values else []
            return _trim_history(list(history))
        except Exception as e:
            logger.warning(f" Could not load history: {e}")
            return []

    def _save_to_checkpoint(self, config: dict, new_messages: list):
        """Append new messages for this turn to the checkpoint."""
        try:
            self.agent.update_state(config, {"messages": new_messages})
            logger.info(f" Saved {len(new_messages)} messages to checkpoint")
        except Exception as e:
            logger.error(f" Checkpoint save failed: {e}")

    async def _execute_tools(self, tool_calls: list) -> list:
        """Execute all tool calls and return ToolMessages."""
        results = []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {})
            tc_id = tc.get("id", "unknown")
            logger.info(f"🔧 Executing tool: {name}({args})")
            try:
                if name == "nutrition_information":
                    result = await nutrition_information.ainvoke(args)
                elif name == "log_baby_weight":
                    result = await log_baby_weight.ainvoke(args)
                else:
                    result = f"Unknown tool: {name}"
            except Exception as e:
                result = f"Tool error: {e}"
                logger.error(f"❌ Tool error: {e}")
                logger.error(f"Error details: Nutrition retrieval tool error - tool: {name}")
            results.append(ToolMessage(content=result, tool_call_id=tc_id))
        return results

    async def get_response_stream(
        self, content, thread_id: str, message_type: str = "text",
        language: str = "english", original_content: str = None
    ) -> AsyncGenerator[str, None]:
        global _current_phone_number
        _current_phone_number = thread_id
        if not self.agent:
            await self.initialize()

        is_image = isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        )

        if is_image:
            text_content = next(
                (p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"),
                "[food image]"
            )
        elif isinstance(content, list):
            text_content = next(
                (p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"),
                str(content)
            )
        else:
            text_content = str(content)

        if is_prompt_injection_attempt(text_content):
            yield off_topic_refusal(language)
            return

        normalized = normalize_query_for_cache(text_content)
        should_cache = check_if_cacheable(text_content)

        config = {"configurable": {"thread_id": thread_id}}

        if should_cache and not is_image:
            cached = search_redis_cache(normalized)
            if cached:
                self._save_to_checkpoint(config, [
                    HumanMessage(content=text_content),
                    AIMessage(content=cached),
                ])
                words = cached.split(" ")
                for word in words:
                    yield word + " "
                    await asyncio.sleep(0.01)
                return

        is_voice = message_type == "voice"
        is_multilingual = language not in ("english", None, "")
        logger.info(f" AGENT MODE: {'🎙️ VOICE' if is_voice else '⌨️ TEXT'} | Language: {language}")

        if is_voice:
            prompt = MULTILINGUAL_NUTRITION_VOICE_SYSTEM_PROMPT if is_multilingual else NUTRITION_VOICE_SYSTEM_PROMPT
            logger.info(f"📋 NUTRITION PROMPT: {'MULTILINGUAL_VOICE' if is_multilingual else 'ENGLISH_VOICE'}")
        else:
            prompt = MULTILINGUAL_NUTRITION_SYSTEM_PROMPT if is_multilingual else NUTRITION_SYSTEM_PROMPT
            logger.info(f"📋 NUTRITION PROMPT: {'MULTILINGUAL_TEXT' if is_multilingual else 'ENGLISH_TEXT'}")

        if is_image:
            logger.info(f"🖼️ IMAGE MODE — sending image + text to LLM")
            user_message = content
        elif is_multilingual:
            logger.info(f"🌍 MULTILINGUAL MODE ({language}): {str(original_content or text_content)[:80]}")
            user_message = _build_multilingual_user_message(language, text_content, original_content, is_voice)
        else:
            logger.info(f"🇬🇧 ENGLISH MODE — sending directly: {text_content[:80]}")
            user_message = f"[Voice Message]: {text_content}" if is_voice else text_content

        log_msg = "[multipart message with image]" if is_image else str(user_message)[:200]
        logger.info(f" FINAL PROMPT TO LLM: {log_msg}")

        history = self._load_history(config)
        human_msg = HumanMessage(content=user_message)
        system_msg = SystemMessage(content=prompt)
        messages = [system_msg] + history + [human_msg]

        logger.info(" Step 1: LLM call with tools")
        try:
            ai_response = await self.llm_with_tools.ainvoke(messages)
        except Exception as e:
            logger.error(f" Step 1 LLM error: {e}", exc_info=True)
            logger.error(f"Error details: Step 1 - Gemma LLM call with tools failed (streaming)")
            yield "I am currently not available, please try again later."
            return

        new_checkpoint_msgs = [human_msg]

        if ai_response.tool_calls:
            logger.info(f"✓ Tool call detected: {[tc['name'] for tc in ai_response.tool_calls]}")
            new_checkpoint_msgs.append(ai_response)

            tool_results = await self._execute_tools(ai_response.tool_calls)
            new_checkpoint_msgs.extend(tool_results)

            # ── Step 2: Final LLM call WITHOUT tools (streaming) ──
            logger.info(" Step 2: Final LLM call without tools")
            final_messages = messages + [ai_response] + tool_results + [
                SystemMessage(content="You have the nutrition data above. Now write your final response to the user. Do NOT call any tool.")
            ]
            stream_source = self.llm.astream(final_messages)
            direct_response = None
        else:
            # Check if model emitted a raw tool call string instead of using tool_calls
            raw_tc = _parse_raw_tool_call(ai_response.content or "")
            if raw_tc:
                logger.info(f"⚠️ Raw tool call detected in text (stream): {raw_tc['name']} — executing manually")
                new_checkpoint_msgs.append(ai_response)
                tool_results = await self._execute_tools([raw_tc])
                new_checkpoint_msgs.extend(tool_results)
                final_messages = messages + [ai_response] + tool_results + [
                    SystemMessage(content="You have the data above. Now write your final response to the user. Do NOT call any tool.")
                ]
                stream_source = self.llm.astream(final_messages)
                direct_response = None
            else:
                logger.info(" No tool call — LLM answering directly")
                stream_source = None
                direct_response = ai_response.content or ""

        # ── Yield the final answer ──
        full_response = ""
        word_buffer = ""
        try:
            if stream_source is not None:
                async for chunk in stream_source:
                    if not chunk.content:
                        continue
                    raw = chunk.content
                    text = (
                        "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
                        if isinstance(raw, list) else str(raw)
                    )
                    if not text:
                        continue
                    text = _clean_text(text)
                    full_response += text
                    word_buffer += text
                    if any(d in word_buffer for d in [' ', '\n', '.', ',', '!', '?', ';', ':']):
                        last = max(
                            (word_buffer.rfind(d) for d in [' ', '\n', '.', ',', '!', '?', ';', ':']),
                            default=-1
                        )
                        if last > 0:
                            to_yield = word_buffer[:last + 1]
                            if contains_system_prompt_leak(to_yield):
                                yield off_topic_refusal(language)
                                return
                            yield to_yield.replace("###", "")
                            word_buffer = word_buffer[last + 1:]
            else:
                # Direct response from Step 1 — yield word by word for streaming feel
                if contains_system_prompt_leak(direct_response):
                    yield off_topic_refusal(language)
                    return
                direct_response = _clean_text(direct_response).replace("###", "")
                full_response = direct_response
                words = direct_response.split(" ")
                for i, word in enumerate(words):
                    chunk = word + (" " if i < len(words) - 1 else "")
                    yield chunk
                    await asyncio.sleep(0)

            if word_buffer:
                word_buffer = _clean_text(word_buffer)
                if not contains_system_prompt_leak(word_buffer):
                    yield word_buffer.replace("###", "")

        except Exception as e:
            logger.error(f" Streaming error: {e}", exc_info=True)
            logger.error(f"Error details: Step 2 - Gemma LLM streaming error")
            yield "I am currently not available, please try again later."
            return

        # ── Save complete turn to checkpoint ──
        if full_response.strip():
            new_checkpoint_msgs.append(AIMessage(content=full_response))
            self._save_to_checkpoint(config, new_checkpoint_msgs)

    async def get_response(
        self, content, thread_id: str, message_type: str = "text",
        language: str = "english", original_content: str = None
    ) -> str:
        global _current_phone_number
        _current_phone_number = thread_id
        if not self.agent:
            await self.initialize()

        is_image = isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        )

        # Extract readable text for cache/injection checks (never serialize base64)
        if is_image:
            text_content = next(
                (p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"),
                "[food image]"
            )
        elif isinstance(content, list):
            text_content = next(
                (p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"),
                str(content)
            )
        else:
            text_content = str(content)

        if is_prompt_injection_attempt(text_content):
            return off_topic_refusal(language)

        normalized = normalize_query_for_cache(text_content)
        should_cache = check_if_cacheable(text_content)

        config = {"configurable": {"thread_id": thread_id}}

        # Skip cache for image messages
        if should_cache and not is_image:
            cached = search_redis_cache(normalized)
            if cached:
                self._save_to_checkpoint(config, [
                    HumanMessage(content=text_content),
                    AIMessage(content=cached),
                ])
                return cached

        is_voice = message_type == "voice"
        is_multilingual = language not in ("english", None, "")
        logger.info(f" AGENT MODE (Non-streaming): {'🎙️ VOICE' if is_voice else '⌨️ TEXT'} | Language: {language}")

        if is_voice:
            prompt = MULTILINGUAL_NUTRITION_VOICE_SYSTEM_PROMPT if is_multilingual else NUTRITION_VOICE_SYSTEM_PROMPT
            logger.info(f"📋 NUTRITION PROMPT: {'MULTILINGUAL_VOICE' if is_multilingual else 'ENGLISH_VOICE'}")
        else:
            prompt = MULTILINGUAL_NUTRITION_SYSTEM_PROMPT if is_multilingual else NUTRITION_SYSTEM_PROMPT
            logger.info(f"📋 NUTRITION PROMPT: {'MULTILINGUAL_TEXT' if is_multilingual else 'ENGLISH_TEXT'}")

        # For image messages: pass the full list (image_url + text parts) as HumanMessage content
        if is_image:
            logger.info(f"🖼️ IMAGE MODE — sending image + text to LLM")
            user_message = content  # list with image_url and text parts
        elif is_multilingual:
            logger.info(f"🌍 MULTILINGUAL MODE ({language}): {str(original_content or text_content)[:80]}")
            user_message = _build_multilingual_user_message(language, text_content, original_content, is_voice)
        else:
            logger.info(f"🇬🇧 ENGLISH MODE — sending directly: {text_content[:80]}")
            user_message = f"[Voice Message]: {text_content}" if is_voice else text_content

        log_msg_ns = "[multipart message with image]" if is_image else str(user_message)[:200]
        logger.info(f" FINAL PROMPT TO LLM (Non-streaming): {log_msg_ns}")

        history = self._load_history(config)
        human_msg = HumanMessage(content=user_message)
        system_msg = SystemMessage(content=prompt)
        messages = [system_msg] + history + [human_msg]

        logger.info(" Step 1: LLM call with tools (non-streaming)")
        try:
            ai_response = await self.llm_with_tools.ainvoke(messages)
        except Exception as e:
            logger.error(f" Step 1 LLM error: {e}", exc_info=True)
            logger.error(f"Error details: Step 1 - Gemma LLM call with tools failed (non-streaming)")
            return "I am currently not available, please try again later."

        new_checkpoint_msgs = [human_msg]

        if ai_response.tool_calls:
            logger.info(f"✓ Tool call: {[tc['name'] for tc in ai_response.tool_calls]}")
            new_checkpoint_msgs.append(ai_response)

            tool_results = await self._execute_tools(ai_response.tool_calls)
            new_checkpoint_msgs.extend(tool_results)

            logger.info(" Step 2: Final LLM call without tools")
            final_messages = messages + [ai_response] + tool_results + [
                SystemMessage(content="You have the nutrition data above. Now write your final response to the user. Do NOT call any tool.")
            ]
        else:
            # Check if model emitted a raw tool call string instead of using tool_calls
            raw_tc = _parse_raw_tool_call(ai_response.content or "")
            if raw_tc:
                logger.info(f"⚠️ Raw tool call detected in text: {raw_tc['name']} — executing manually")
                new_checkpoint_msgs.append(ai_response)
                tool_results = await self._execute_tools([raw_tc])
                new_checkpoint_msgs.extend(tool_results)
                final_messages = messages + [ai_response] + tool_results + [
                    SystemMessage(content="You have the data above. Now write your final response to the user. Do NOT call any tool.")
                ]
            else:
                logger.info(" No tool call — LLM answering directly")
                final_messages = messages

        try:
            final_response = await self.llm.ainvoke(final_messages)
            response = final_response.content if hasattr(final_response, "content") else str(final_response)
        except Exception as e:
            logger.error(f" Step 2 LLM error: {e}", exc_info=True)
            logger.error(f"Error details: Step 2 - Gemma LLM final call failed (non-streaming)")
            return "I am currently not available, please try again later."

        if not response.strip():
            return "I've processed that. How else can I assist you?"

        if contains_system_prompt_leak(response):
            return off_topic_refusal(language)

        # Guard: model leaked a raw tool call string instead of answering — execute it
        if re.search(r'call\s*:\s*\w+\s*\{', response, re.IGNORECASE) or \
           re.search(r'<tool_call>', response, re.IGNORECASE):
            raw_tc = _parse_raw_tool_call(response)
            if raw_tc:
                logger.info(f"⚠️ Raw tool call in final response: {raw_tc['name']} — executing and retrying")
                tool_results2 = await self._execute_tools([raw_tc])
                retry_messages = final_messages + [AIMessage(content=response)] + tool_results2 + [
                    SystemMessage(content="You have the data above. Now write your final response to the user. Do NOT call any tool.")
                ]
                try:
                    retry_response = await self.llm.ainvoke(retry_messages)
                    response = retry_response.content if hasattr(retry_response, "content") else str(retry_response)
                except Exception:
                    return "I am currently not available, please try again later."
            else:
                logger.error(f"❌ Model returned unparseable tool call: {response[:120]}")
                return "I am currently not available, please try again later."

        response = _clean_text(response).replace("###", "")

        new_checkpoint_msgs.append(AIMessage(content=response))
        self._save_to_checkpoint(config, new_checkpoint_msgs)

        return response

    async def cleanup(self):
        self.agent = None
        self.llm = None
        self.llm_with_tools = None

unified_agent = UnifiedAgent()