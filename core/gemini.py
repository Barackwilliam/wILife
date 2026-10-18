"""
Google Gemini (generativelanguage API) — Kiswahili kizuri, na free tier.

Key: GEMINI_API_KEY (aistudio.google.com → Get API key). Model ikiondolewa na
Google (HTTP 404), tunauliza orodha ya model zilizopo na kuchagua "flash" bora.
"""

import logging
import re

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


def _version(name):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def replacement_models(current, error_text=""):
    """
    Model za kujaribu badala ya iliyoondolewa: kwanza ile Google inayoipendekeza
    kwenye ujumbe wa 404 ("... use models/gemini-3.8-flash"), kisha za familia
    ileile (flash/pro) kutoka orodha ya Google, mpya zaidi kwanza.
    """
    out = []
    m = re.search(r"use (?:models/)?(gemini-[\w.\-]+)", error_text or "")
    if m:
        out.append(m.group(1).rstrip("."))
    try:
        ids = available_models()
    except (GeminiError, requests.RequestException):
        ids = []
    family = "pro" if "pro" in current else "flash"
    special = ("tts", "image", "audio", "live", "embedding", "thinking", "exp", "lite")
    same = [n for n in ids if family in n and not any(x in n for x in special)]
    same.sort(key=lambda n: ("preview" not in n, _version(n)), reverse=True)
    out += same + [n for n in PREFERRED if n in ids]
    seen = set()
    return [n for n in out if not (n in seen or seen.add(n)) and n != current]


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
    tried = {current}
    for _ in range(3):   # model imeondolewa (404) -> jaribu iliyopendekezwa na Google / mpya zaidi
        if r.status_code != 404:
            break
        replacement = next((n for n in replacement_models(current, r.text) if n not in tried), None)
        if not replacement:
            break
        log.warning("gemini model %s is unavailable; trying %s", current, replacement)
        tried.add(replacement)
        r = post(replacement)
        if r.status_code == 200:
            _replacements[wanted] = replacement
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
