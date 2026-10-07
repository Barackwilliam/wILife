"""
The JamiiTek AI team — William, Ibrahimu, Selvester, Grace and Diana.

The team lives inside JamiiTek (apps/wafanyakazi there), next to the data it
works on. This module is wILife's side of the link:

    JamiiTek → wILife   POST /agent/jamiitek/   (views_agent.jamiitek_inbox)
        kind=report    William's morning/evening report → sent to the owner
        kind=approval  a draft for a customer → an ApprovalRequest with an
                       OK/NO code, tool "jamiitek_task", external_ref = task id

    wILife → JamiiTek   /wafanyakazi/api/...
        approve_remote / reject_remote   the owner answered OK / NO
        status_text                      the owner typed "timu"

One shared secret both ways, in the X-Workers-Token header:
JAMIITEK_TOKEN here = WORKERS_API_TOKEN on JamiiTek.
"""

import logging

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

log = logging.getLogger("core.agent")

TOOL = "jamiitek_task"
TIMEOUT = 45   # JamiiTek is on Render free and may be waking up
COMMANDS = {"timu", "team", "timu ya jamiitek", "hali ya timu", "jamiitek"}


class TeamUnavailable(Exception):
    pass


def base_url():
    return (getattr(settings, "JAMIITEK_URL", "") or "").rstrip("/")


def token():
    return getattr(settings, "JAMIITEK_TOKEN", "") or ""


def is_configured():
    return bool(base_url() and token())


def owner():
    return User.objects.filter(is_active=True).order_by("pk").first()


def _call(method, path, payload=None):
    if not is_configured():
        raise TeamUnavailable("JAMIITEK_URL / JAMIITEK_TOKEN haijawekwa")
    try:
        response = requests.request(method, f"{base_url()}{path}", json=payload, timeout=TIMEOUT,
                                    headers={"X-Workers-Token": token()})
    except requests.RequestException as exc:
        raise TeamUnavailable(f"JamiiTek haipatikani ({type(exc).__name__})") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200:
        raise TeamUnavailable(data.get("error") or f"JamiiTek HTTP {response.status_code}")
    return data


# ---------------------------------------------------------------------------
# Inbound: JamiiTek → owner
# ---------------------------------------------------------------------------

def receive(payload):
    """
    Handle one message from the team. Returns (status, body) for the view.
    A delivery failure is reported back so JamiiTek can use its own channels.
    """
    from core.agent.channels import DeliveryError, send_to_self
    from core.models import AgentRun

    user = owner()
    if user is None:
        return 503, {"ok": False, "error": "no owner account"}
    kind = payload.get("kind")

    if kind == "report":
        text = (payload.get("text") or "").strip()
        if not text:
            return 400, {"ok": False, "error": "empty report"}
        try:
            send_to_self(user, text)
        except DeliveryError as exc:
            return 502, {"ok": False, "error": str(exc)[:200]}
        AgentRun.objects.create(job="jamiitek_report", ok=True, processed=1,
                                detail=text.splitlines()[0][:200], finished_at=timezone.now())
        return 200, {"ok": True}

    if kind == "approval":
        from core.agent.approvals import request_approval
        from core.models import ApprovalRequest

        task_id = str(payload.get("task_id") or "").strip()
        body = (payload.get("body") or "").strip()
        if not task_id or not body:
            return 400, {"ok": False, "error": "task_id and body are required"}
        existing = ApprovalRequest.objects.filter(tool=TOOL, external_ref=task_id, status="pending").first()
        if existing:
            return 200, {"ok": True, "code": existing.code}

        name = payload.get("recipient_name") or ""
        to = payload.get("recipient") or ""
        subject = payload.get("subject") or ""
        draft_body = f"*Kichwa:* {subject}\n\n{body}" if subject else body
        approval = request_approval(user, TOOL, {
            "recipient_name": f"{name} <{to}>" if name and to else (name or to),
            "body": draft_body,
        }, summary=f"{payload.get('title', '')} — {payload.get('context', '')}"[:255])
        approval.external_ref = task_id
        approval.save(update_fields=["external_ref"])
        return 200, {"ok": True, "code": approval.code}

    return 400, {"ok": False, "error": f"unknown kind: {kind}"}


# ---------------------------------------------------------------------------
# Outbound: owner's decision → JamiiTek
# ---------------------------------------------------------------------------

def approve_remote(approval):
    """Ask JamiiTek to send an approved draft. Returns (ok, message)."""
    try:
        data = _call("POST", f"/wafanyakazi/api/kazi/{approval.external_ref}/idhinisha/", {})
    except TeamUnavailable as exc:
        return False, f"{exc} — iidhinishe kwenye {base_url()}/manage/wafanyakazi/"
    return bool(data.get("ok")), data.get("message") or "JamiiTek haikujibu."


def reject_remote(approval):
    try:
        data = _call("POST", f"/wafanyakazi/api/kazi/{approval.external_ref}/kataa/", {})
    except TeamUnavailable as exc:
        log.warning("jamiitek reject #%s: %s", approval.pk, exc)
        return False, str(exc)
    return bool(data.get("ok")), data.get("message", "")


def is_team_command(text):
    return (text or "").strip().lower().strip("?!. ") in COMMANDS


def status_text():
    """The team at a glance, for the owner's "timu" command."""
    try:
        data = _call("GET", "/wafanyakazi/api/hali/")
    except TeamUnavailable as exc:
        return f"⚠️ Sikuweza kufikia timu ya JamiiTek: {exc}"

    lines = ["👥 *Timu ya JamiiTek*", ""]
    for w in data.get("team", []):
        extra = []
        if w.get("awaiting"):
            extra.append(f"{w['awaiting']} zinasubiri idhini")
        if w.get("stale"):
            extra.append(f"{w['stale']} zimekwama")
        tail = f" · {', '.join(extra)}" if extra else ""
        lines.append(f"  • *{w['name']}* ({w['role']}): kazi wazi {w.get('open', 0)}{tail}")
    awaiting = data.get("awaiting") or []
    if awaiting:
        lines += ["", "✍️ *Zinasubiri idhini yako*"]
        for task in awaiting[:8]:
            code = f" — *OK {task['wilife_code']}*" if task.get("wilife_code") else ""
            lines.append(f"  • [{task['worker_name']}] {task['title']}{code}")
    lines += ["", f"Panel: {base_url()}/manage/wafanyakazi/"]
    return "\n".join(lines)
