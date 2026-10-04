"""
Presentation for messages the agent sends to William.

Every job writes one plain text in WhatsApp markup (*bold*, _italic_). This
module turns that same text into:

    email_html(text)  a designed HTML email: header card, sections, lists,
                      quoted drafts, reply codes and link buttons
    for_chat(text)    a tidier WhatsApp/Telegram version: title rule, bullets,
                      signature

The structure is read from conventions the jobs already follow, so no job has
to know which channel it is sent on:

    first line                      title  ("⏰ *Kumbusho*")
    "📅 *Ratiba ya leo*"            section heading (emoji + bold line)
    "*Kwenda kwa:* Asha"            label / value
    "  09:00  Kikao"                list item (indented, or "-", "•", "1.")
    "Jibu *OK 1234* kutuma"         reply code
    "Soma: https://..."             link button
    "────" ... "────"               quoted block (a draft)
    "_maelezo_"                     muted note
"""

import html
import re

from django.conf import settings
from django.utils import timezone

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, MUTED, LINE, PAPER, PAGE = "#17151d", "#6f6a78", "#ece7df", "#ffffff", "#f4f1ec"
BRAND = "#e2603f"

# Accent colour by the title's leading emoji.
ACCENTS = {
    "⏰": "#e9a020", "☀": "#e2603f", "📰": "#e2603f", "✍": "#7b5cd6", "🚨": "#d9466b",
    "⏳": "#e9a020", "🎯": "#7b5cd6", "🧾": "#3d7bd9", "📊": "#1f9bb0", "💰": "#2f9e7a",
    "✅": "#2f9e7a", "⚠": "#e9a020", "🔔": "#3d7bd9", "🧪": "#1f9bb0",
}

RULE_RE = re.compile(r"^\s*[─━\-=_]{4,}\s*$")
REPLY_RE = re.compile(r"^Jibu \*(OK|NO) (\w+)\*\s*(.*)$")
# Absolute links, or site paths like "/habari-admin/rasimu/" (made absolute with SITE_URL).
URL_RE = re.compile(r"https?://\S+|(?<![\w/])/[a-z][\w\-./?=&]*")
SECTION_RE = re.compile(r"^(?P<emoji>[^\w\s*_(]{1,3})\s*\*(?P<title>[^*]+)\*\s*(?P<rest>.*)$")
BOLD_LINE_RE = re.compile(r"^\*(?P<title>[^*]+)\*\s*(?P<rest>.*)$")
LABEL_RE = re.compile(r"^\*(?P<label>[^*:]{1,30}):\*\s*(?P<value>.+)$")
ITEM_RE = re.compile(r"^(?:\s{2,}|\s*[-•▸]\s+|\s*\d{1,2}[.)]\s+)(?P<body>.+)$")
KV_RE = re.compile(r"^(?P<key>[^\W\d_][^:\[\]]{1,23}):\s+(?P<value>.+)$")
NOTE_RE = re.compile(r"^_(?P<note>[^_]+)_$")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _split_title(text):
    lines = text.strip("\n").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", "", []
    first = lines[0].strip()
    match = SECTION_RE.match(first)
    if match:
        return match["emoji"], (match["title"] + (" " + match["rest"] if match["rest"] else "")).strip(), lines[1:]
    return "", first.replace("*", ""), lines[1:]


def parse(text):
    """Return (emoji, title, blocks). Blocks are (kind, data) tuples."""
    emoji, title, lines = _split_title(text)
    blocks, quote, replies = [], None, []
    for raw in lines:
        line = raw.rstrip()
        if RULE_RE.match(line):
            if quote is None:
                quote = []
            else:
                blocks.append(("quote", quote))
                quote = None
            continue
        if quote is not None:
            quote.append(line)
            continue
        if not line.strip():
            blocks.append(("space", None))
            continue
        reply = REPLY_RE.match(line.strip())
        if reply:
            replies.append((reply[1], reply[2], reply[3]))
            continue
        url = URL_RE.search(line)
        href = url and (url.group(0) if url.group(0).startswith("http") else (_site() + url.group(0) if _site() else ""))
        if href:
            label = line[:url.start()].strip().rstrip(":").strip() or "Fungua"
            blocks.append(("link", (label.replace("*", ""), href)))
            continue
        for kind, regex in (("section", SECTION_RE), ("label", LABEL_RE)):
            match = regex.match(line.strip()) if not raw.startswith("  ") else None
            if match:
                blocks.append((kind, match.groupdict()))
                break
        else:
            item = ITEM_RE.match(line)
            if item:
                body = item["body"].strip()
                kv = KV_RE.match(body) if raw.startswith("  ") else None
                blocks.append(("kv", (kv["key"], kv["value"])) if kv else ("item", body))
            elif BOLD_LINE_RE.match(line.strip()):
                blocks.append(("strong", line.strip()))
            elif NOTE_RE.match(line.strip()):
                blocks.append(("note", NOTE_RE.match(line.strip())["note"]))
            else:
                blocks.append(("text", line.strip()))
    if quote is not None:
        blocks.append(("quote", quote))
    if replies:
        blocks.append(("replies", replies))
    return emoji, title, blocks


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _inline(text):
    """Escape, then apply *bold* and _italic_."""
    out = html.escape(text)
    out = re.sub(r"\*([^*\n]+)\*", r'<strong style="color:%s;">\1</strong>' % INK, out)
    out = re.sub(r"(?<![\w/])_([^_\n]+)_(?![\w/])", r"<em>\1</em>", out)
    return out


def _accent(emoji):
    return next((color for key, color in ACCENTS.items() if emoji.startswith(key)), BRAND)


def _tint(hex_color, alpha):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    mix = lambda c: round(255 - (255 - c) * alpha)  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def _site():
    return (getattr(settings, "SITE_URL", "") or "").rstrip("/")


TAG_RE = re.compile(r"^\[(?P<tag>[^\]]{2,20})\]\s*(?P<rest>.+)$")
TIME_RE = re.compile(r"^(?P<time>\d{1,2}:\d{2})\s+(?P<rest>.+)$")
TAG_COLORS = {"dunia": "#3d7bd9", "afrika": "#e9a020", "tanzania": "#2f9e7a", "jamiitek": "#e2603f"}


def _item_html(body, accent, p):
    """One list row: a time column, a category tag, a marker emoji or a dot."""
    marker = (f'<div style="width:6px;height:6px;border-radius:3px;background:{accent};margin-top:9px;"></div>')
    lead = ""
    time_match, tag_match = TIME_RE.match(body), TAG_RE.match(body)
    if time_match:
        marker = ""
        lead = (f'<span style="display:inline-block;min-width:52px;font-family:Menlo,Consolas,monospace;'
                f'font-size:14px;font-weight:800;color:{accent};">{time_match["time"]}</span>')
        body = time_match["rest"]
    elif tag_match:
        color = TAG_COLORS.get(tag_match["tag"].lower(), accent)
        marker = ""
        lead = (f'<span style="display:inline-block;font-family:{FONT};font-size:11px;font-weight:800;'
                f'letter-spacing:.06em;text-transform:uppercase;color:{color};background:{_tint(color, .12)};'
                f'padding:3px 8px;border-radius:999px;margin-right:8px;vertical-align:1px;">'
                f'{html.escape(tag_match["tag"])}</span>')
        body = tag_match["rest"]
    elif body[:1] and not body[:1].isalnum() and body[:1] not in "_*([\"'":
        marker = ""  # already opens with a marker emoji such as 🔴
    marker_cell = f'<td valign="top" width="16" style="padding-right:8px;">{marker}</td>' if marker else ""
    return (
        f'<tr><td style="padding:9px 0;border-bottom:1px solid {LINE};">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'{marker_cell}<td style="{p}">{lead}{_inline(body)}</td></tr></table></td></tr>'
    )


def _block_html(kind, data, accent):
    p = f"margin:0;font-family:{FONT};font-size:15px;line-height:1.6;color:{INK};"
    if kind == "space":
        return '<tr><td style="height:10px;line-height:10px;font-size:0;">&nbsp;</td></tr>'
    if kind == "section":
        rest = f' <span style="color:{MUTED};font-weight:600;">{_inline(data["rest"])}</span>' if data["rest"] else ""
        return (
            '<tr><td style="padding:14px 0 6px;">'
            f'<p style="margin:0;font-family:{FONT};font-size:12px;font-weight:800;letter-spacing:.08em;'
            f'text-transform:uppercase;color:{accent};">{html.escape(data["emoji"])}&nbsp; '
            f'{html.escape(data["title"])}{rest}</p>'
            f'<div style="height:2px;width:36px;background:{accent};border-radius:2px;margin-top:6px;"></div>'
            "</td></tr>"
        )
    if kind == "label":
        return (
            f'<tr><td style="padding:3px 0;"><p style="{p}"><span style="color:{MUTED};">'
            f'{html.escape(data["label"])}:</span> <strong>{_inline(data["value"])}</strong></p></td></tr>'
        )
    if kind == "item":
        return _item_html(data, accent, p)
    if kind == "kv":
        key, value = data
        return (
            '<tr><td style="padding:8px 0;border-bottom:1px solid %s;">'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="{p}color:{MUTED};">{_inline(key)}</td>'
            f'<td align="right" style="{p}font-weight:700;white-space:nowrap;">{_inline(value)}</td>'
            "</tr></table></td></tr>" % LINE
        )
    if kind == "strong":
        return f'<tr><td style="padding:4px 0;"><p style="{p}font-size:17px;">{_inline(data)}</p></td></tr>'
    if kind == "note":
        return f'<tr><td style="padding:2px 0;"><p style="{p}font-size:13px;color:{MUTED};"><em>{html.escape(data)}</em></p></td></tr>'
    if kind == "quote":
        body = "<br>".join(_inline(line) for line in data) or "&nbsp;"
        return (
            f'<tr><td style="padding:10px 0;"><div style="background:{PAGE};border-left:4px solid {accent};'
            f'border-radius:10px;padding:14px 16px;"><p style="{p}">{body}</p></div></td></tr>'
        )
    if kind == "link":
        label, url = data
        return (
            '<tr><td style="padding:12px 0 4px;">'
            f'<a href="{html.escape(url)}" style="display:inline-block;background:{INK};color:#ffffff;'
            f'font-family:{FONT};font-size:14px;font-weight:700;text-decoration:none;padding:12px 22px;'
            f'border-radius:999px;">{html.escape(label)} &rarr;</a></td></tr>'
        )
    if kind == "replies":
        chips = []
        for word, code, hint in data:
            ok = word == "OK"
            chips.append(
                '<tr><td style="padding:5px 0;">'
                f'<span style="display:inline-block;min-width:92px;text-align:center;font-family:Menlo,Consolas,monospace;'
                f'font-size:15px;font-weight:800;letter-spacing:.06em;padding:8px 14px;border-radius:8px;'
                f'background:{"#2f9e7a" if ok else "#ffffff"};color:{"#ffffff" if ok else INK};'
                f'border:1px solid {"#2f9e7a" if ok else LINE};">{word} {html.escape(code)}</span>'
                f'<span style="font-family:{FONT};font-size:14px;color:{MUTED};padding-left:12px;">{_inline(hint)}</span>'
                "</td></tr>"
            )
        return (
            '<tr><td style="padding:14px 0 4px;">'
            f'<div style="background:{_tint(accent, .08)};border:1px solid {_tint(accent, .25)};border-radius:12px;padding:14px 16px;">'
            f'<p style="margin:0 0 6px;font-family:{FONT};font-size:12px;font-weight:800;letter-spacing:.08em;'
            f'text-transform:uppercase;color:{accent};">Jibu kwenye WhatsApp au Telegram</p>'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{"".join(chips)}</table>'
            "</div></td></tr>"
        )
    return f'<tr><td style="padding:3px 0;"><p style="{p}">{_inline(data)}</p></td></tr>'


def _preheader(blocks):
    for kind, data in blocks:
        if kind in ("text", "strong", "note", "item"):
            return re.sub(r"[*_]", "", data)[:110]
        if kind == "section":
            return data["title"]
    return ""


def email_html(text):
    emoji, title, blocks = parse(text)
    accent = _accent(emoji)
    # Collapse runs of spacing and drop it at the edges.
    tidy = []
    for kind, data in blocks:
        if kind == "space" and (not tidy or tidy[-1][0] == "space"):
            continue
        tidy.append((kind, data))
    while tidy and tidy[-1][0] == "space":
        tidy.pop()
    body = "".join(_block_html(kind, data, accent) for kind, data in tidy)

    now = timezone.localtime()
    site = _site()
    links = ""
    if site:
        links = (
            f'<a href="{site}/dashboard/" style="color:{MUTED};text-decoration:underline;">Dashibodi</a>'
            f' &nbsp;·&nbsp; <a href="{site}/habari-admin/rasimu/" style="color:{MUTED};text-decoration:underline;">Rasimu za habari</a>'
            " &nbsp;·&nbsp; "
        )
    icon = (
        f'<td valign="top" width="56" style="padding-right:14px;"><div style="width:48px;height:48px;line-height:48px;'
        f'text-align:center;border-radius:14px;background:{_tint(accent, .14)};font-size:24px;">{html.escape(emoji)}</div></td>'
        if emoji else ""
    )
    return f"""<!doctype html>
<html lang="sw"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>{html.escape(title)}</title></head>
<body style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PAGE};">{html.escape(_preheader(tidy))}&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE};">
<tr><td align="center" style="padding:28px 14px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">
    <tr><td style="padding:0 6px 16px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="width:30px;height:30px;background:{BRAND};border-radius:9px;text-align:center;vertical-align:middle;
          font-family:{FONT};font-size:17px;font-weight:900;color:#ffffff;">W</td>
        <td style="padding-left:10px;font-family:{FONT};font-size:18px;font-weight:800;color:{INK};letter-spacing:-.01em;">wILife</td>
      </tr></table>
    </td></tr>
    <tr><td style="background:{PAPER};border-radius:18px;overflow:hidden;box-shadow:0 1px 2px rgba(23,21,29,.06),0 8px 24px rgba(23,21,29,.06);">
      <div style="height:5px;background:{accent};border-radius:18px 18px 0 0;"></div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="padding:26px 28px 8px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>{icon}
            <td valign="middle">
              <p style="margin:0;font-family:{FONT};font-size:22px;line-height:1.25;font-weight:800;color:{INK};">{html.escape(title)}</p>
              <p style="margin:4px 0 0;font-family:{FONT};font-size:13px;color:{MUTED};">{now:%d/%m/%Y · %H:%M}</p>
            </td></tr></table>
        </td></tr>
        <tr><td style="padding:6px 28px 28px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{body}</table>
        </td></tr>
      </table>
    </td></tr>
    <tr><td style="padding:18px 8px 0;text-align:center;font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};">
      {links}Msaidizi wako binafsi wa wILife
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


# ---------------------------------------------------------------------------
# WhatsApp / Telegram
# ---------------------------------------------------------------------------

CHAT_RULE = "━━━━━━━━━━━━━━"


def for_chat(text):
    """Tidy a message for WhatsApp/Telegram: a rule under the title, bullets, a signature."""
    lines = text.strip("\n").splitlines()
    if not lines:
        return text
    out = [lines[0]]
    rest = lines[1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    if not (rest and RULE_RE.match(rest[0])):
        out.append(CHAT_RULE)
    in_quote = False
    for line in rest:
        if RULE_RE.match(line):
            in_quote = not in_quote
            out.append(line)
            continue
        item = ITEM_RE.match(line) if not in_quote else None
        if item and not re.match(r"^\s*\d{1,2}[.)]\s", line):  # numbered lists keep their numbers
            body = item["body"].strip()
            # Lines that open with a marker emoji (🔴, 🕐…) or an _italic note_ keep it.
            line = f"  • {body}" if body[:1].isalnum() or body[:1] == "*" else f"  {body}"
        out.append(line)
    collapsed = []
    for line in out:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return "\n".join(collapsed) + "\n\n_— wILife_"
