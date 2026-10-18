"""
Google Gemini (generativelanguage API) — Kiswahili kizuri, na free tier.

Key: GEMINI_API_KEY (aistudio.google.com → Get API key). Model ikiondolewa na
Google (HTTP 404), tunauliza orodha ya model zilizopo na kuchagua "flash" bora.
"""

import logging

import requests
from django.conf import settings

log = logging.getLogger("core.agent")

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
PREFERRED = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest", "gemini-2.5-flash-lite",
             "gemini-2.5-pro")
_replacements = {}


class GeminiError(Exception):
    pass


def enabled():
    return bool(getattr(settings, "GEMINI_API_KEY", ""))


def _key():
    return {"key": settings.GEMINI_API_KEY}


def available_models(timeout=10):
    r = requests.get(f"{BASE}/models", params=_key(), timeout=timeout)
    if r.status_code != 200:
        raise GeminiError(f"gemini models HTTP {r.status_code}: {r.text[:200]}")
    return [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def chat(system, user_content, model=None, max_tokens=900, temperature=0.3, timeout=30, json_mode=False):
    """Rudisha jibu la maandishi. GeminiError kwa kosa lolote."""
    if not enabled():
        raise GeminiError("GEMINI_API_KEY is not set")
    wanted = model or getattr(settings, "GEMINI_MODEL", "") or DEFAULT_MODEL
    current = _replacements.get(wanted, wanted)

    def post(name):
        config = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if "pro" in name:
            config["maxOutputTokens"] = max_tokens + 2048       # Pro "hufikiri" kabla ya kujibu
        else:
            config["thinkingConfig"] = {"thinkingBudget": 0}    # Flash: jibu moja kwa moja
        if json_mode:
            config["responseMimeType"] = "application/json"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user_content}]}],
                "generationConfig": config}
        return requests.post(f"{BASE}/models/{name}:generateContent", params=_key(), json=body, timeout=timeout)

    r = post(current)
    if r.status_code == 404:
        ids = available_models()
        replacement = next((n for n in PREFERRED if n in ids and n != current), None) or \
            next((n for n in ids if "flash" in n), None)
        if replacement:
            log.warning("gemini model %s is unavailable; using %s", current, replacement)
            _replacements[wanted] = replacement
            r = post(replacement)
    if r.status_code != 200:
        raise GeminiError(f"gemini HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise GeminiError(f"gemini returned nothing ({reason})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        raise GeminiError(f"gemini empty reply ({candidates[0].get('finishReason', '?')})")
    return text
