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

from core import groq

log = logging.getLogger("news")


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
    try:
        return groq.chat(system, user_content, model=getattr(settings, "NEWS_MODEL", "") or None,
                         max_tokens=max_tokens, temperature=temperature, timeout=timeout, json_mode=True)
    except (groq.GroqError, requests.RequestException) as exc:
        raise WriterError(str(exc)) from exc


def _json(raw):
    """JSON kutoka kwenye jibu la model — hata likiwa na ``` au maneno kabla/baada."""
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise WriterError(f"model did not return JSON: {exc}") from exc


def _parse(raw, min_words=80):
    data = _json(raw)
    if not isinstance(data, dict):
        raise WriterError("model did not return a JSON object")
    out = {}
    for key, limit in (("title", 200), ("excerpt", 300), ("body", 20000), ("keywords", 300)):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            if key == "keywords":
                value = ""
            else:
                raise WriterError(f"missing field: {key}")
        out[key] = value.strip()[:limit]
    if len(out["body"].split()) < min_words:
        raise WriterError("body too short")
    return out


def _notes(notes):
    notes = (notes or "").strip()
    return [f"MWONGOZO WA MHARIRI (fuata, bila kuvunja SHERIA KALI): {notes[:1200]}"] if notes else []


def write_news(category_label, item, related=(), notes=""):
    lines = [
        f"Kundi: {category_label}",
        f"Chanzo kikuu: {item.publisher}",
        f"Kichwa cha chanzo: {item.title}",
        f"Muhtasari wa chanzo: {item.summary or '(hakuna)'}",
    ]
    if item.published:
        lines.append(f"Tarehe ya chanzo: {item.published:%Y-%m-%d}")
    for r in related[:3]:
        lines.append(f"Taarifa ya habari hiyo hiyo ({r.publisher}): {r.title} — {r.summary[:300]}")
    lines += _notes(notes)
    return _parse(_call(NEWS_SYSTEM, "\n".join(lines)))


COMMISSION_RULES = """

KAZI MAALUM: mhariri ameagiza habari kuhusu MADA iliyotajwa. Andika makala moja kuhusu mada hiyo
ukitumia taarifa za vyanzo vilivyotolewa PEKEE (unaweza kuunganisha vyanzo kadhaa vya tukio moja).
Kama vyanzo HAVIHUSU mada hiyo, au havina ukweli wa kutosha kuandika bila kubuni, jibu JSON hii tu:
{"skip": "sababu fupi kwa Kiswahili"}
Kwa michezo: taja matokeo, wafungaji na dakika TU kama vimo kwenye vyanzo."""


def write_commissioned(category_label, topic, items, instructions="", notes=""):
    """Makala iliyoagizwa: mada + vyanzo kadhaa. Inarudisha dict au WriterError."""
    lines = [f"Kundi: {category_label}", f"MADA ILIYOAGIZWA: {topic}"]
    if instructions:
        lines.append(f"Maelekezo ya mhariri kwa makala hii: {instructions[:800]}")
    lines.append("VYANZO:")
    for n, it in enumerate(items[:6], 1):
        when = f" ({it.published:%Y-%m-%d %H:%M} UTC)" if it.published else ""
        lines.append(f"{n}. [{it.publisher}]{when} {it.title} — {it.summary[:400] or '(hakuna muhtasari)'}")
    lines += _notes(notes)
    raw = _call(NEWS_SYSTEM + COMMISSION_RULES, "\n".join(lines), max_tokens=2400)
    try:
        data = _json(raw)
    except WriterError:
        data = None
    if isinstance(data, dict) and data.get("skip"):
        raise WriterError(f"AI: {str(data['skip'])[:300]}")
    return _parse(raw)


def write_service(service):
    lines = [
        f"Huduma: {service.name}",
        f"Kauli fupi: {service.tagline}",
        f"Maelezo: {service.description}",
        "Faida:", *[f"- {b}" for b in service.benefit_list()],
        "Fursa / inafaa kwa:", *[f"- {o}" for o in service.opportunity_list()],
    ]
    return _parse(_call(SERVICE_SYSTEM, "\n".join(lines)))


PROMO_SYSTEM = """Wewe ni mhariri wa wILife (Kiswahili, Tanzania). Mhariri amekuletea TANGAZO kutoka Instagram
(maelezo ya chapisho). Liandike upya kama tangazo safi la Kiswahili kwa tovuti.

SHERIA KALI:
1. Tumia taarifa zilizomo kwenye maelezo PEKEE. Usibuni bei, tarehe, mahali, namba za simu, ofa wala ahadi.
2. Kama maelezo yana bei, tarehe, mahali au mawasiliano — yahifadhi SAWASAWA kama yalivyo.
3. Ondoa hashtag nyingi, emoji za kujirudia na maneno ya "link in bio".
4. Lugha safi, fupi, ya kuvutia lakini isiyotia chumvi. Maneno 60–250 kwa body.

Jibu kwa JSON PEKEE: {"title": "kichwa herufi 40–80", "excerpt": "≤ 155 herufi", "body": "aya zilizotenganishwa na mstari mtupu", "keywords": "maneno 3–6 kwa koma"}"""


def write_promo(caption, account="", notes=""):
    """Tangazo la Instagram -> makala fupi. Inarudisha dict au WriterError."""
    lines = [f"Akaunti ya Instagram: {account or '(haijatajwa)'}", "MAELEZO YA CHAPISHO:", caption[:3000]]
    lines += _notes(notes)
    return _parse(_call(PROMO_SYSTEM, "\n".join(lines), max_tokens=1400), min_words=30)
