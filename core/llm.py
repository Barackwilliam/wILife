"""
Mwandishi wa AI mmoja kwa mfumo mzima — Gemini au Groq, kwa chaguo la mhariri.

Chaguo linahifadhiwa kwenye EditorSettings.ai_model ("gemini:gemini-2.5-flash",
"groq:openai/gpt-oss-120b", ...) na linabadilishwa kwa kitufe kwenye ukurasa wa
"Agiza habari" (au Django admin). Mtoa huduma aliyechaguliwa akishindwa
(kikomo cha bure, mtandao, key haipo), ombi linajaribiwa kwa mwingine mwenye key
— habari hazikwami kwa sababu ya mtoa huduma mmoja.
"""

import logging
import re
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

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
    for i, (provider, model) in enumerate(plan):
        try:
            if provider == "gemini":
                text = gemini.chat(system, user_content, model=model, max_tokens=max_tokens,
                                   temperature=temperature, timeout=max(timeout, 30), json_mode=json_mode)
            else:
                text = groq.chat(system, user_content, model=model, max_tokens=max_tokens,
                                 temperature=temperature, timeout=timeout, json_mode=json_mode)
        except (gemini.GeminiError, groq.GroqError, requests.RequestException) as exc:
            log.warning("llm %s failed: %s", provider, exc)
            errors.append((provider, exc))
            continue
        if i:
            _record_fail(errors[0], used=provider)
        else:
            _record_ok()
        return text
    _record_fail(errors[0], used="")
    raise LLMError(" | ".join(f"{p}: {str(e)[:200]}" for p, e in errors))


# --- Hali ya AI: onyo kwenye dashboard ----------------------------------------

PROVIDER_NAMES = {"gemini": "Gemini", "groq": "Groq"}


def reason(exc):
    """Sababu fupi, inayoeleweka, ya kushindwa (bila key wala data nyeti)."""
    text = str(exc)
    low = text.lower()
    m = re.search(r"HTTP (\d{3})", text)
    code = int(m.group(1)) if m else 0
    if code == 429 or "resource_exhausted" in low or "quota" in low:
        return "Kikomo cha bure kimeisha kwa sasa (429)"
    if code in (401, 403) or "api key" in low or "api_key" in low:
        return "Key haikubaliki — angalia key kwenye Render → Environment"
    if code >= 500:
        return f"Seva za mtoa huduma zina tatizo kwa sasa ({code})"
    if isinstance(exc, requests.Timeout) or "timed out" in low:
        return "Muda umeisha — mtoa huduma hakujibu"
    if isinstance(exc, requests.ConnectionError):
        return "Mtandao — imeshindwa kumfikia mtoa huduma"
    return text[:200]


def _settings_qs():
    from news.models import EditorSettings
    return EditorSettings.objects.filter(pk=EditorSettings.load().pk)


def _record_fail(error, used):
    provider, exc = error
    try:
        qs = _settings_qs()
        row = qs.values("ai_fail_day", "ai_fail_count").first() or {}
        today = timezone.localdate()
        count = (row.get("ai_fail_count") or 0) + 1 if row.get("ai_fail_day") == today else 1
        qs.update(ai_fail_at=timezone.now(), ai_fail_used=used, ai_fail_day=today, ai_fail_count=count,
                  ai_fail_reason=f"{PROVIDER_NAMES.get(provider, provider)}: {reason(exc)}"[:300])
    except Exception:   # hali ni ya taarifa tu — isizuie habari
        log.exception("llm health record failed")


def _record_ok():
    """AI kuu imefanya kazi — ondoa onyo (andika tu kama kulikuwa na onyo)."""
    try:
        qs = _settings_qs()
        row = qs.values("ai_fail_at", "ai_ok_at").first() or {}
        if row.get("ai_fail_at") and (not row.get("ai_ok_at") or row["ai_ok_at"] < row["ai_fail_at"]):
            qs.update(ai_ok_at=timezone.now())
    except Exception:
        log.exception("llm health record failed")


def health():
    """
    Hali ya AI kwa onyo la dashboard. None kama kila kitu kiko sawa; vinginevyo
    {'level': 'fallback'|'down', 'title', 'reason', 'at', 'count'}.
    """
    from news.models import EditorSettings
    cfg = EditorSettings.load()
    if not enabled():
        return {"level": "down", "title": "Hakuna AI iliyowekwa — AI haiwezi kuandika",
                "reason": "Weka GEMINI_API_KEY (na GROQ_API_KEY ya akiba) kwenye Render → Environment.",
                "at": None, "count": 0}
    choice = cfg.ai_model if cfg.ai_model else DEFAULT
    primary = PROVIDER_NAMES.get(choice.split(":", 1)[0], choice)
    if not provider_ready(choice.split(":", 1)[0]):
        other = "Groq" if primary == "Gemini" else "Gemini"
        key = "GEMINI_API_KEY" if primary == "Gemini" else "GROQ_API_KEY"
        return {"level": "fallback", "title": f"{primary} haijawekwa — {other} ndiyo inaandika",
                "reason": f"{key} haipo kwenye Render → Environment.", "at": None, "count": 0}
    if not cfg.ai_fail_at or (cfg.ai_ok_at and cfg.ai_ok_at >= cfg.ai_fail_at):
        return None
    if timezone.now() - cfg.ai_fail_at > timedelta(hours=24):
        return None   # tukio la zamani bila matumizi mapya — lisisumbue
    count = cfg.ai_fail_count if cfg.ai_fail_day == timezone.localdate() else 0
    used = PROVIDER_NAMES.get(cfg.ai_fail_used, cfg.ai_fail_used)
    title = (f"{primary} imeshindwa — {used} ndiyo inaandika kwa sasa" if used
             else "AI zote zimeshindwa — rasimu hazikuandikwa")
    return {"level": "fallback" if used else "down", "title": title, "reason": cfg.ai_fail_reason,
            "at": cfg.ai_fail_at, "count": count}


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
        if choice == selected():
            _record_ok()
        return True, text, round(time.monotonic() - started, 1)
    except (gemini.GeminiError, groq.GroqError, requests.RequestException) as exc:
        return False, str(exc)[:300], round(time.monotonic() - started, 1)
