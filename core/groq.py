"""
Groq chat calls, shared by the agent brain and the news writer.

Groq retires models from time to time. When the configured model is gone
(HTTP 404 model_not_found) the call asks Groq which models this key can still
use, switches to the best one and retries — so a retirement degrades nothing
instead of silently stopping the news and the briefs. Set GROQ_MODEL to choose
the model explicitly.
"""

import logging

import requests
from django.conf import settings

log = logging.getLogger("core.agent")

CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"

# Best first. Only models that write good Swahili and support JSON mode.
PREFERRED = (
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "moonshotai/kimi-k2-instruct-0905",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "openai/gpt-oss-20b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
)
NOT_CHAT = ("whisper", "guard", "tts", "playai", "orpheus", "compound", "distil")

# Retired model -> the replacement found for it, kept for the process lifetime.
_replacements = {}


class GroqError(Exception):
    pass


def _headers():
    return {"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"}


def available_models(timeout=10):
    response = requests.get(MODELS_URL, headers=_headers(), timeout=timeout)
    if response.status_code != 200:
        raise GroqError(f"groq models HTTP {response.status_code}: {response.text[:200]}")
    return [m["id"] for m in response.json().get("data", [])
            if m.get("active", True) and not any(word in m["id"] for word in NOT_CHAT)]


def pick_model(ids, exclude=()):
    usable = [i for i in ids if i not in exclude]
    for name in PREFERRED:
        if name in usable:
            return name
    return usable[0] if usable else None


def _model_gone(response):
    return response.status_code == 404 and "model_not_found" in response.text


def chat(system, user_content, model=None, max_tokens=900, temperature=0.3, timeout=12, json_mode=False):
    """Return the assistant's reply text. Raises GroqError on any failure."""
    wanted = model or getattr(settings, "GROQ_MODEL", "") or PREFERRED[0]
    current = _replacements.get(wanted, wanted)

    def post(name):
        payload = {
            "model": name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return requests.post(CHAT_URL, headers=_headers(), json=payload, timeout=timeout)

    response = post(current)
    if _model_gone(response):
        replacement = pick_model(available_models(), exclude={current})
        if replacement:
            log.warning("groq model %s is unavailable; using %s (set GROQ_MODEL to choose)", current, replacement)
            _replacements[wanted] = replacement
            response = post(replacement)
    if response.status_code != 200:
        raise GroqError(f"groq HTTP {response.status_code}: {response.text[:200]}")
    return response.json()["choices"][0]["message"]["content"].strip()
