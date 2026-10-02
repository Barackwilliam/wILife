"""
Turn feed items into original Swahili articles with the LLM (Groq).

Rules the model is held to:
- Use only facts present in the source snippets. No invented quotes, numbers,
  names or dates. Shorter is better than made-up.
- Original wording — never copy sentences from the source.
- Explain why it matters to a Tanzanian reader.

The output is JSON, validated here. Anything malformed raises WriterError and
the slot is retried on the next tick with a different story.
"""

import json
import logging
import re

import requests
from django.conf import settings

log = logging.getLogger("news")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class WriterError(Exception):
    pass


NEWS_SYSTEM = """Wewe ni mhariri mkuu wa wILife, chombo cha habari cha Kiswahili kinacholenga wasomaji wa Tanzania.

Kazi: andika makala ASILI ya habari kwa Kiswahili sanifu, kutokana na taarifa za chanzo ulizopewa.

SHERIA KALI:
1. Tumia UKWELI uliomo kwenye taarifa za chanzo PEKEE. Usibuni nukuu, takwimu, majina, tarehe wala matukio.
   Kama taarifa ni chache, andika makala fupi — ni bora kuliko kubuni.
2. Usinakili sentensi za chanzo. Andika kwa maneno yako mwenyewe.
3. Anza na kiini cha habari (nani, nini, wapi, lini) katika aya ya kwanza.
4. Ongeza sehemu "## Kwa nini ni muhimu" inayoeleza umuhimu kwa Mtanzania — bila kubuni ukweli mpya;
   tumia maarifa ya jumla tu yasiyo na utata.
5. Lugha rasmi, wazi, isiyo na upendeleo. Hakuna maoni ya kisiasa.
6. Maneno 250–450 kwa body.

Jibu kwa JSON PEKEE (hakuna maandishi mengine, hakuna ```), kwa muundo huu:
{"title": "kichwa cha habari herufi 55–85: kitaje WAZI nani/nini/wapi (majina halisi ya watu, nchi, taasisi), kiwe sahihi na kisichotia chumvi — hakuna clickbait, hakuna maswali ya kuvutia tu, hakuna HERUFI KUBWA zote",
 "excerpt": "muhtasari wa sentensi 1–2, ≤ 155 herufi, kwa meta description",
 "body": "aya zilizotenganishwa na mstari mtupu; vichwa vidogo vianze na '## '",
 "keywords": "mada 4–6 fupi (neno 1–3 kila moja) zinazoeleza habari: majina ya mahali, taasisi, watu au sekta — mfano: Dodoma, Bunge, bajeti, kilimo — zikitenganishwa na koma"}"""

SERVICE_SYSTEM = """Wewe ni mwandishi wa maudhui wa JamiiTek Digital Agency (Dar es Salaam), ukiandika kwa wILife.

Kazi: andika makala ya Kiswahili inayoeleza HUDUMA MOJA ya JamiiTek: ni nini, faida zake, na fursa zilizopo kwa
wafanyabiashara na taasisi za Tanzania.

SHERIA:
1. Tumia taarifa za huduma ulizopewa PEKEE. Usibuni bei, wateja, takwimu, tuzo wala ahadi.
2. Toni ya kitaalamu na ya kuelimisha — si tangazo la kelele. Msomaji ajifunze kitu hata asiponunua.
3. Muundo: utangulizi wa tatizo/fursa → "## Huduma hii ni nini" → "## Faida kuu" (orodha kwa '- ') →
   "## Fursa zilizopo" → hitimisho fupi linalomkaribisha msomaji kuwasiliana na JamiiTek.
4. Maneno 350–550 kwa body.

Jibu kwa JSON PEKEE, muundo:
{"title": "...", "excerpt": "≤ 155 herufi", "body": "...", "keywords": "..."}"""


def enabled():
    return bool(getattr(settings, "GROQ_API_KEY", ""))


def _call(system, user_content, max_tokens=1800, temperature=0.4, timeout=45):
    if not enabled():
        raise WriterError("GROQ_API_KEY is not set")
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": getattr(settings, "NEWS_MODEL", "") or getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        raise WriterError(f"groq HTTP {response.status_code}: {response.text[:200]}")
    return response.json()["choices"][0]["message"]["content"]


def _parse(raw):
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WriterError(f"model did not return JSON: {exc}") from exc
    out = {}
    for key, limit in (("title", 200), ("excerpt", 300), ("body", 20000), ("keywords", 300)):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            if key == "keywords":
                value = ""
            else:
                raise WriterError(f"missing field: {key}")
        out[key] = value.strip()[:limit]
    if len(out["body"].split()) < 80:
        raise WriterError("body too short")
    return out


def write_news(category_label, item, related=()):
    lines = [
        f"Kundi: {category_label}",
        f"Chanzo kikuu: {item.publisher}",
        f"Kichwa cha chanzo: {item.title}",
        f"Muhtasari wa chanzo: {item.summary or '(hakuna)'}",
    ]
    if item.published:
        lines.append(f"Tarehe ya chanzo: {item.published:%Y-%m-%d}")
    for r in related[:2]:
        lines.append(f"Taarifa inayohusiana ({r.publisher}): {r.title} — {r.summary[:300]}")
    return _parse(_call(NEWS_SYSTEM, "\n".join(lines)))


def write_service(service):
    lines = [
        f"Huduma: {service.name}",
        f"Kauli fupi: {service.tagline}",
        f"Maelezo: {service.description}",
        "Faida:", *[f"- {b}" for b in service.benefit_list()],
        "Fursa / inafaa kwa:", *[f"- {o}" for o in service.opportunity_list()],
    ]
    return _parse(_call(SERVICE_SYSTEM, "\n".join(lines)))
