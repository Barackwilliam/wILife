"""
Mwandishi wa AI mmoja kwa mfumo mzima — Gemini au Groq, kwa chaguo la mhariri.

Chaguo linahifadhiwa kwenye EditorSettings.ai_model ("gemini:gemini-2.5-flash",
"groq:openai/gpt-oss-120b", ...) na linabadilishwa kwa kitufe kwenye ukurasa wa
"Agiza habari" (au Django admin). Mtoa huduma aliyechaguliwa akishindwa
(kikomo cha bure, mtandao, key haipo), ombi linajaribiwa kwa mwingine mwenye key
— habari hazikwami kwa sababu ya mtoa huduma mmoja.
"""

import logging
import time

import requests
from django.conf import settings

from core import gemini, groq

log = logging.getLogger("core.agent")

MODELS = [
    ("gemini:gemini-2.5-flash", "Gemini 2.5 Flash", "Kiswahili bora, haraka — free tier ya Google"),
    ("gemini:gemini-2.5-pro", "Gemini 2.5 Pro", "Ubora wa juu zaidi; kikomo kidogo cha bure, polepole"),
    ("groq:openai/gpt-oss-120b", "Groq · GPT-OSS 120B", "Bure, haraka sana; Kiswahili kizuri"),
    ("groq:llama-3.3-70b-versatile", "Groq · Llama 3.3 70B", "Bure; Kiswahili cha wastani"),
]
DEFAULT = MODELS[0][0]


class LLMError(Exception):
    pass


def provider_ready(provider):
    return gemini.enabled() if provider == "gemini" else bool(getattr(settings, "GROQ_API_KEY", ""))


def enabled():
    return provider_ready("gemini") or provider_ready("groq")


def selected():
    """Chaguo la mhariri ('gemini:...'), au DEFAULT."""
    try:
        from news.models import EditorSettings
        choice = EditorSettings.load().ai_model
    except Exception:   # jedwali halijaundwa bado (migration), n.k.
        choice = ""
    return choice if choice in {m[0] for m in MODELS} or ":" in (choice or "") else DEFAULT


def label(choice):
    return next((name for value, name, _ in MODELS if value == choice), choice)


def _plan(choice):
    """Mpangilio wa kujaribu: chaguo la mhariri kwanza, kisha mtoa huduma mwingine."""
    provider, model = choice.split(":", 1)
    plan = [(provider, model)]
    other = "groq" if provider == "gemini" else "gemini"
    plan.append((other, None))   # model ya kawaida ya mtoa huduma huyo
    return [(p, m) for p, m in plan if provider_ready(p)]


def chat(system, user_content, max_tokens=900, temperature=0.3, timeout=30, json_mode=False, choice=None):
    """Jibu la maandishi kutoka model iliyochaguliwa (au akiba). LLMError kama zote zimeshindwa."""
    plan = _plan(choice or selected())
    if not plan:
        raise LLMError("Hakuna AI iliyowekwa — weka GEMINI_API_KEY au GROQ_API_KEY")
    errors = []
    for provider, model in plan:
        try:
            if provider == "gemini":
                return gemini.chat(system, user_content, model=model, max_tokens=max_tokens,
                                   temperature=temperature, timeout=max(timeout, 30), json_mode=json_mode)
            return groq.chat(system, user_content, model=model, max_tokens=max_tokens,
                             temperature=temperature, timeout=timeout, json_mode=json_mode)
        except (gemini.GeminiError, groq.GroqError, requests.RequestException) as exc:
            log.warning("llm %s failed: %s", provider, exc)
            errors.append(f"{provider}: {str(exc)[:200]}")
    raise LLMError(" | ".join(errors))


def test(choice):
    """Jaribio fupi la Kiswahili kwa kitufe cha 'Jaribu'. Inarudisha (ok, maandishi, sekunde)."""
    provider = choice.split(":", 1)[0]
    if not provider_ready(provider):
        key = "GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY"
        return False, f"{key} haijawekwa kwenye Render → Environment.", 0.0
    started = time.monotonic()
    try:
        provider, model = choice.split(":", 1)
        fn = gemini.chat if provider == "gemini" else groq.chat
        text = fn("Wewe ni mhariri wa habari wa Kitanzania. Jibu kwa Kiswahili sanifu.",
                  "Andika sentensi mbili fupi kuhusu umuhimu wa habari sahihi kwa jamii.",
                  model=model, max_tokens=200, temperature=0.4, timeout=30)
        return True, text, round(time.monotonic() - started, 1)
    except (gemini.GeminiError, groq.GroqError, requests.RequestException) as exc:
        return False, str(exc)[:300], round(time.monotonic() - started, 1)
