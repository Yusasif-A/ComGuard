"""
Gemma vision analysis.

One model does all the image work: reading text off a notice, deciding what kind
of hazard or scam is in the picture, and judging whether the picture looks real.
There is no separate detection service — an earlier version of this codebase ran
a dedicated food-recognition model in front of the LLM, which made sense for
meals and makes none here, where the useful signal is "what does this notice
say" rather than "which objects are present".

The model is asked for JSON. Small models are unreliable about that, so
`_extract_json` is forgiving (fenced blocks, leading prose, trailing commentary)
and every field is validated on the way out. A malformed reply degrades to an
"unknown" analysis that still lets the conversation continue rather than
failing the report — a person standing in a flood should not lose their report
because a model emitted a stray backtick.
"""

import asyncio
import base64
import json
import logging
import re
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config import GEMMA_API_KEY, GEMMA_BASE_URL, GEMMA_VISION_MODEL

logger = logging.getLogger(__name__)


# The categories the rest of the system routes on. Keep this list in sync with
# the authority routing map in config.authority_webhook_for.
CATEGORIES = [
    "flood",
    "fire",
    "road_damage",          # collapsed road or bridge, washed-out culvert
    "blocked_route",        # debris, fallen tree, impassable road
    "structural_collapse",
    "unrest",               # rising tension, crowd trouble
    "fake_notice",          # forged public notice or government letter
    "illegal_levy",         # fraudulent tax collector, unlawful demand for money
    "unlawful_checkpoint",
    "forged_receipt",
    "other",
]

SEVERITIES = ["low", "medium", "high", "critical"]

_VISION_SYSTEM_PROMPT = """You are the image analyst for ComGuard, a community safety reporting service in Nigeria.

A member of the public has sent a photograph. Your job is to describe what is actually visible, read any text in the image, and judge whether the photograph looks genuine. You are NOT talking to the user — you produce structured data that another part of the system will act on.

Return ONE JSON object and nothing else. No prose before or after, no markdown fence.

{
  "is_relevant": true/false,
  "category": one of ["flood","fire","road_damage","blocked_route","structural_collapse","unrest","fake_notice","illegal_levy","unlawful_checkpoint","forged_receipt","other"],
  "severity": one of ["low","medium","high","critical"],
  "description": "one or two plain sentences describing only what is visible",
  "ocr_text": "every word of text you can read in the image, verbatim, or empty string",
  "is_document": true/false,
  "document_red_flags": ["short phrases naming anything suspicious about the document"],
  "authenticity": {
    "verdict": one of ["likely_real","uncertain","likely_synthetic"],
    "confidence": 0.0 to 1.0,
    "reasons": ["short phrases giving your evidence"]
  },
  "people_visible": true/false,
  "immediate_danger": true/false
}

Rules:

- is_relevant is false ONLY when the photo shows nothing to do with a hazard, a scam, a notice, or public safety. A blurry or partial photo of a real hazard is still relevant.
- Describe ONLY what is in the frame. Do not infer a cause, name a place, or guess at who is responsible. "Brown water covering a road up to car door height" — not "flooding caused by blocked drainage in Lekki".
- ocr_text must be a verbatim transcription, including headings, reference numbers, amounts and stamps. Do not summarise it, correct its spelling, or translate it. Return an empty string if there is no readable text.
- document_red_flags applies when the image is a notice, letter, receipt or demand for payment. Look for: misspelled agency names, a missing or pasted-looking official seal, an ordinary personal bank account as the payment destination, a personal phone number instead of an official line, inconsistent fonts, no reference number, a threat demanding immediate cash payment. List what you actually see; an empty list means you found none.
- authenticity is about the PHOTOGRAPH, not the document. likely_synthetic means the image itself looks AI-generated or digitally manipulated: impossible lighting or shadows, warped or melted text, hands or objects with wrong geometry, unnaturally smooth or plastic surfaces, edges that smear into the background, a subject that is too perfectly composed. Say "uncertain" when you genuinely cannot tell — that is a useful answer and it is the correct one for most ordinary phone photos of bad quality.
- immediate_danger is true only when the photo shows a threat to life happening right now: active fire, fast or deep moving water, a partial collapse, people in the water or under debris.
- severity reflects the risk shown in the image: "low" a nuisance, "medium" disruption or loss of money, "high" injury or major loss likely, "critical" life-threatening now.

Never refuse. If the image is unreadable, return is_relevant false with a description saying so."""


_llm: Optional[ChatOpenAI] = None


def _get_llm() -> ChatOpenAI:
    """Lazily built so importing this module never requires a reachable model."""
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            base_url=GEMMA_BASE_URL,
            api_key=GEMMA_API_KEY,
            model=GEMMA_VISION_MODEL,
            temperature=0.0,      # analysis, not creativity
            streaming=False,
            max_retries=1,
            timeout=90,
        )
        logger.info(f"👁️ Vision model ready: {GEMMA_VISION_MODEL} at {GEMMA_BASE_URL}")
    return _llm


def to_data_uri(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply.

    Handles ```json fences, a sentence of preamble, and trailing commentary by
    scanning for a balanced brace span rather than trusting the whole string to
    parse.
    """
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None

    if candidate is None:
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    break

    if not candidate:
        return None

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Trailing commas are the single most common malformation.
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Vision JSON unparseable: {e}")
            return None


def _as_list_of_strings(value, limit: int = 6) -> list:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip()[:120] for v in value if str(v).strip()][:limit]


def _normalise(raw: dict) -> dict:
    """Coerce a model reply into the exact shape the rest of the system expects.

    Every downstream consumer — the report store, the authority payload, the
    agent prompt — reads these keys, so they are all guaranteed present with the
    right type regardless of what the model actually returned.
    """
    auth = raw.get("authenticity")
    if not isinstance(auth, dict):
        auth = {}

    verdict = str(auth.get("verdict", "uncertain")).strip().lower()
    if verdict not in ("likely_real", "uncertain", "likely_synthetic"):
        verdict = "uncertain"

    try:
        confidence = float(auth.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    category = str(raw.get("category", "other")).strip().lower()
    if category not in CATEGORIES:
        category = "other"

    severity = str(raw.get("severity", "medium")).strip().lower()
    if severity not in SEVERITIES:
        severity = "medium"

    return {
        "ok": True,
        "is_relevant": bool(raw.get("is_relevant", True)),
        "category": category,
        "severity": severity,
        "description": str(raw.get("description", "")).strip()[:600],
        "ocr_text": str(raw.get("ocr_text", "")).strip()[:4000],
        "is_document": bool(raw.get("is_document", False)),
        "document_red_flags": _as_list_of_strings(raw.get("document_red_flags")),
        "authenticity": {
            "verdict": verdict,
            "confidence": round(confidence, 2),
            "reasons": _as_list_of_strings(auth.get("reasons")),
        },
        "people_visible": bool(raw.get("people_visible", False)),
        "immediate_danger": bool(raw.get("immediate_danger", False)),
    }


def unknown_analysis(reason: str = "") -> dict:
    """The safe default when vision is unavailable or unparseable.

    Deliberately NOT "likely_real": a failed check must never read as a passed
    check. Callers treat ok=False as "we could not verify the photo" and the
    conversation continues on the voice note alone.
    """
    return {
        "ok": False,
        "error": reason,
        "is_relevant": True,
        "category": "other",
        "severity": "medium",
        "description": "",
        "ocr_text": "",
        "is_document": False,
        "document_red_flags": [],
        "authenticity": {"verdict": "uncertain", "confidence": 0.0, "reasons": []},
        "people_visible": False,
        "immediate_danger": False,
    }


async def analyse_image(image_bytes: bytes, caption: str = "") -> dict:
    """Run the full vision pass over one photo. Never raises."""
    if not image_bytes:
        return unknown_analysis("empty_image")

    caption_note = (
        f'The sender captioned the photo: "{caption.strip()}". Use it as context '
        f"only — describe what you can actually see."
        if caption and caption.strip()
        else "The sender did not add a caption."
    )

    message = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": to_data_uri(image_bytes)}},
        {"type": "text", "text": f"{caption_note}\n\nAnalyse this photograph and return the JSON object."},
    ])

    try:
        response = await _get_llm().ainvoke([
            SystemMessage(content=_VISION_SYSTEM_PROMPT),
            message,
        ])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"❌ Vision call failed: {e}")
        return unknown_analysis(f"vision_call_failed: {e}")

    content = response.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )

    parsed = _extract_json(str(content))
    if parsed is None:
        logger.warning(f"⚠️ Vision returned no usable JSON: {str(content)[:200]}")
        return unknown_analysis("unparseable_response")

    result = _normalise(parsed)
    logger.info(
        "👁️ Vision: category=%s severity=%s authenticity=%s(%.2f) document=%s danger=%s",
        result["category"], result["severity"],
        result["authenticity"]["verdict"], result["authenticity"]["confidence"],
        result["is_document"], result["immediate_danger"],
    )
    return result


def analysis_for_prompt(analysis: dict) -> str:
    """Render an analysis as the context block the conversational agent reads.

    The agent is told what the photo showed, not shown the photo again — that
    keeps image tokens out of every subsequent turn and, more importantly, stops
    the model re-litigating its own earlier reading of the picture.
    """
    if not analysis.get("ok"):
        return (
            "[Photo received, but automatic analysis was unavailable. Do not "
            "describe or speculate about the photo's contents. Rely on what the "
            "person tells you, and ask them to describe what they are seeing.]"
        )

    if not analysis.get("is_relevant"):
        return (
            "[Photo received, but it does not appear to show a hazard, a notice, "
            "or anything safety-related. Ask the person, gently, what they wanted "
            "you to look at.]"
        )

    lines = [
        "[Photo analysis — this is what the image actually showed:",
        f"- What is visible: {analysis['description']}",
        f"- Assessed as: {analysis['category'].replace('_', ' ')} (severity: {analysis['severity']})",
    ]

    if analysis.get("ocr_text"):
        lines.append(f"- Text read from the image, verbatim: \"{analysis['ocr_text']}\"")

    if analysis.get("is_document"):
        flags = analysis.get("document_red_flags") or []
        lines.append(
            f"- This is a document. Warning signs found: {'; '.join(flags)}"
            if flags else
            "- This is a document. No obvious warning signs were found in its appearance — "
            "that is NOT the same as confirming it is genuine."
        )

    verdict = analysis["authenticity"]["verdict"]
    reasons = "; ".join(analysis["authenticity"]["reasons"]) or "no specific indicators"
    verdict_text = {
        "likely_real": "The photo itself looks like a genuine, unmanipulated photograph",
        "uncertain": "Whether the photo is genuine could not be determined — say so plainly if it matters",
        "likely_synthetic": "The photo shows signs of being AI-generated or digitally altered. "
                            "Treat the report as UNVERIFIED and tell the person what was noticed, without accusing them",
    }[verdict]
    lines.append(f"- Image authenticity: {verdict_text} ({reasons}).")

    if analysis.get("immediate_danger"):
        lines.append("- The photo shows an immediate threat to life. Lead with safety instructions.")

    lines.append("Do not mention models, analysis, confidence scores or this block to the person.]")
    return "\n".join(lines)


# ── Text classification ──────────────────────────────────────────────────────

_CLASSIFY_SYSTEM_PROMPT = """You classify messages sent to ComGuard, a community safety reporting service in Nigeria.

Decide whether the message describes something that has actually happened or is happening to the sender or near them, and if so what kind of thing it is.

Return ONE JSON object and nothing else:

{
  "is_report": true/false,
  "category": one of ["flood","fire","road_damage","blocked_route","structural_collapse","unrest","fake_notice","illegal_levy","unlawful_checkpoint","forged_receipt","other"],
  "severity": one of ["low","medium","high","critical"],
  "summary": "one plain sentence describing what the sender says happened",
  "immediate_danger": true/false,
  "place_mentioned": "any place, street, market or landmark the sender named, or empty string"
}

Rules:

- is_report is TRUE when the sender describes an event, a hazard, or a demand for money — whether it is happening now or happened recently. It is FALSE for greetings, thanks, questions about how the service works, general questions, and requests for advice about something hypothetical.
- "The road to the market is under water" is a report. "What should I do if there is a flood?" is not.
- summary must describe only what the sender said. Do not add detail, do not infer a cause, and do not name anyone as responsible.
- immediate_danger is true only when someone is in danger right now: trapped, in the water, in a burning building, hurt, or being threatened.
- severity: "low" a nuisance, "medium" disruption or money lost, "high" injury or major loss likely, "critical" life-threatening now.
- place_mentioned is copied verbatim from the message. Empty string if no place is named.

Never refuse. If the message is unclear, return is_report false."""


def unknown_classification() -> dict:
    """Default when classification is unavailable.

    is_report is False so an unclassifiable message never silently becomes a
    filed report — a report nobody meant to make wastes a dispatcher's attention
    and, worse, could count towards corroborating an incident that is not real.
    """
    return {
        "ok": False,
        "is_report": False,
        "category": "other",
        "severity": "medium",
        "summary": "",
        "immediate_danger": False,
        "place_mentioned": "",
    }


async def classify_text(message: str) -> dict:
    """Decide whether a written or spoken message is a report, and of what."""
    if not message or not message.strip():
        return unknown_classification()

    try:
        response = await _get_llm().ainvoke([
            SystemMessage(content=_CLASSIFY_SYSTEM_PROMPT),
            HumanMessage(content=message.strip()[:4000]),
        ])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"❌ Classification call failed: {e}")
        return unknown_classification()

    content = response.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )

    parsed = _extract_json(str(content))
    if parsed is None:
        logger.warning(f"⚠️ Classification returned no usable JSON: {str(content)[:200]}")
        return unknown_classification()

    category = str(parsed.get("category", "other")).strip().lower()
    if category not in CATEGORIES:
        category = "other"

    severity = str(parsed.get("severity", "medium")).strip().lower()
    if severity not in SEVERITIES:
        severity = "medium"

    result = {
        "ok": True,
        "is_report": bool(parsed.get("is_report", False)),
        "category": category,
        "severity": severity,
        "summary": str(parsed.get("summary", "")).strip()[:600],
        "immediate_danger": bool(parsed.get("immediate_danger", False)),
        "place_mentioned": str(parsed.get("place_mentioned", "")).strip()[:120],
    }
    logger.info(
        "🏷️ Classified: report=%s category=%s severity=%s danger=%s",
        result["is_report"], result["category"], result["severity"],
        result["immediate_danger"],
    )
    return result
