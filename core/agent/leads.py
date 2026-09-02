"""
Lead watch — tell William about new leads worth his time.

Deliberately opinionated: it reports the leads above a score threshold and stays
quiet about the rest. A daily list of forty low-scoring leads is not information,
it is noise, and noise trains you to ignore the channel.

TIER A. Reporting only. Contacting a lead is a TIER B draft through the gate.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from core.agent.brief import already_ran_today
from core.agent.channels import DeliveryError, send_to_self
from core.agent.leadscout import LeadScoutUnavailable, fetch_new_leads, is_configured
from core.models import AgentRun

log = logging.getLogger("core.agent")


def _summary(leads, threshold):
    lines = [f"🎯 *Lead mpya* ({len(leads)})", ""]
    for lead in leads:
        score = lead.get("score")
        mark = "🔥" if score is not None and score >= threshold + 20 else "•"
        site = lead.get("website") or ""
        lines.append(f"  {mark} *{lead.get('business_name', '?')}*")
        if site:
            lines.append(f"     {site}")
        bits = []
        if score is not None:
            bits.append(f"alama {score}")
        if lead.get("contact"):
            bits.append(str(lead["contact"]))
        if bits:
            lines.append(f"     _{' · '.join(bits)}_")
    return "\n".join(lines)


def run_lead_watch(now=None, deadline=None, dry_run=False):
    now = now or timezone.now()

    if not getattr(settings, "AGENT_LEAD_WATCH_ENABLED", False):
        return {"job": "lead_watch", "sent": 0, "failed": 0, "detail": "disabled"}

    if not is_configured():
        return {"job": "lead_watch", "sent": 0, "failed": 0, "detail": "LeadScout not configured"}

    hour = getattr(settings, "AGENT_LEAD_WATCH_HOUR", 9)
    if timezone.localtime(now).hour < hour:
        return {"job": "lead_watch", "sent": 0, "failed": 0, "detail": "too early"}

    if already_ran_today("lead_watch", now=now):
        return {"job": "lead_watch", "sent": 0, "failed": 0, "detail": "already ran today"}

    try:
        leads = fetch_new_leads()
    except LeadScoutUnavailable as exc:
        log.warning("lead watch skipped: %s", exc)
        return {"job": "lead_watch", "sent": 0, "failed": 1, "detail": str(exc)[:200]}

    threshold = getattr(settings, "AGENT_LEAD_MIN_SCORE", 60)
    worth_it = [l for l in leads if (l.get("score") or 0) >= threshold]

    if not worth_it:
        if not dry_run:
            AgentRun.objects.create(job="lead_watch", finished_at=timezone.now(),
                                    ok=True, processed=0, detail="none above threshold")
        return {"job": "lead_watch", "sent": 0, "failed": 0, "detail": "none above threshold"}

    cap = getattr(settings, "AGENT_LEAD_MAX_REPORTED", 5)
    worth_it = worth_it[:cap]
    text = _summary(worth_it, threshold)

    if dry_run:
        log.info("DRY RUN — lead watch:\n%s", text)
        return {"job": "lead_watch", "sent": 1, "failed": 0, "detail": "dry run"}

    user = User.objects.filter(is_active=True).order_by("pk").first()
    if user is None:
        return {"job": "lead_watch", "sent": 0, "failed": 1, "detail": "no user"}

    try:
        send_to_self(user, text)
    except DeliveryError as exc:
        log.error("lead watch failed: %s", exc)
        return {"job": "lead_watch", "sent": 0, "failed": 1, "detail": str(exc)[:200]}

    AgentRun.objects.create(job="lead_watch", finished_at=timezone.now(), ok=True,
                            processed=len(worth_it), detail=f"{len(worth_it)} reported")
    return {"job": "lead_watch", "sent": 1, "failed": 0, "detail": f"{len(worth_it)} leads"}
