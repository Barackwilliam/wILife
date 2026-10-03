"""
The brain — Step 5.

Groq enters here, and its job is narrow on purpose: turn already-decided facts
into better sentences, and rank an already-decided list. It does not choose
actions. It never calls a tool. See spec Section 4.4.

Every function here falls back to the deterministic version on any failure —
timeout, rate limit, bad key, malformed response. The agent must keep working
when the model is unavailable, because a heartbeat that depends on a third
party is not a heartbeat.
"""

import json
import logging

from django.conf import settings

from core import groq

log = logging.getLogger("core.agent")

PHRASE_SYSTEM = """You rewrite a personal daily brief for William, a solo software developer and agency owner in Dar es Salaam.

ABSOLUTE RULES:
- Use ONLY the facts given. Never add, estimate, infer or invent anything.
- Never remove a fact. Every item given must still appear.
- Keep all numbers exactly as provided.
- Write in Swahili, the way a competent assistant speaks: direct, warm, no flattery.
- Keep WhatsApp formatting: *bold*, _italic_, emoji already present.
- Maximum 2 sentences of your own framing at the top. No closing pep talk.
- Output the message only. No preamble, no explanation, no markdown fences."""

RANK_SYSTEM = """You order a list of work items by what deserves attention first.

Return ONLY a JSON array of the given ids in your chosen order, e.g. [3,1,2].
No prose, no explanation, no markdown fences.
Consider: how late something is, stated priority, and whether it blocks other people.
Never invent an id that was not given. Never omit one."""


def _enabled():
    return bool(getattr(settings, "GROQ_API_KEY", "")) and getattr(settings, "AGENT_BRAIN_ENABLED", True)


def _call(system, user_content, max_tokens=900, temperature=0.3, timeout=12):
    return groq.chat(system, user_content, max_tokens=max_tokens, temperature=temperature, timeout=timeout)


def phrase_brief(fallback_text, facts=None):
    """
    Improve the wording of an already-built brief.

    `fallback_text` is the deterministic version and is returned unchanged on
    any problem. This is not a degraded mode — it is a perfectly good brief.
    """
    if not _enabled():
        return fallback_text

    try:
        payload = fallback_text
        if facts:
            payload = f"{fallback_text}\n\n---\nStructured facts:\n{json.dumps(facts, default=str, ensure_ascii=False)}"

        result = _call(PHRASE_SYSTEM, payload)

        # Guard against a model that returns nothing useful or runs away.
        if not result or len(result) < 40:
            log.warning("brain returned suspiciously short output — using template")
            return fallback_text
        if len(result) > len(fallback_text) * 3:
            log.warning("brain returned bloated output — using template")
            return fallback_text
        return result

    except Exception as exc:
        log.warning("brain unavailable (%s) — using template", exc)
        return fallback_text


def rank_items(items, fallback_order=None):
    """
    Order work items by importance.

    `items` is a list of dicts with at least 'id' and 'label'.
    Returns a list of ids. Falls back to the order given.
    """
    default = fallback_order or [i["id"] for i in items]
    if not _enabled() or len(items) < 2:
        return default

    try:
        result = _call(
            RANK_SYSTEM,
            json.dumps(items, ensure_ascii=False, default=str),
            max_tokens=200,
            temperature=0.0,
        )
        cleaned = result.replace("```json", "").replace("```", "").strip()
        order = json.loads(cleaned)

        given = {i["id"] for i in items}
        # Reject any answer that invented or dropped an id, rather than
        # silently trusting a partially valid ordering.
        if not isinstance(order, list) or set(order) != given:
            log.warning("brain returned an invalid ranking — using default order")
            return default
        return order

    except Exception as exc:
        log.warning("brain ranking unavailable (%s) — using default order", exc)
        return default


def polish_draft(body, context=""):
    """
    Improve the wording of a client message draft.

    The result still goes through the approval gate — the model's output is
    never closer to being sent than the template's was.
    """
    if not _enabled():
        return body

    system = (
        "You improve the wording of a short business WhatsApp message written by "
        "William of JamiiTek Digital Agency in Tanzania.\n"
        "RULES: keep the language of the original (usually Swahili). Keep every "
        "fact, number, reference and name exactly. Do not add promises, prices, "
        "dates or apologies that are not already there. Stay polite and brief. "
        "Output the message only."
    )
    try:
        payload = f"Context: {context}\n\nMessage:\n{body}" if context else body
        result = _call(system, payload, max_tokens=500)
        if not result or len(result) > len(body) * 3:
            return body
        return result
    except Exception as exc:
        log.warning("brain polish unavailable (%s) — using original draft", exc)
        return body
