"""
Expiry watch — hosting and domains.

The most time-critical thing this agent does, and the reason it exists in a
project built two days before its owner travels:

    ManagedWebsite.auto_suspend_on_expiry defaults to True.

A client's site can be suspended automatically while William is away, and the
first he hears of it is an angry phone call. Domains are worse — an expired
.co.tz is not always recoverable.

So this job is louder than the others. It escalates as the date approaches
instead of reporting the same flat list every day, and once something has
actually expired it repeats daily rather than going quiet after one mention.

TIER A. Reporting only. Renewing, invoicing and messaging the client are all
someone else's job — a human's, or a TIER B draft through the gate.
"""

import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from core.agent.brief import already_ran_today
from core.agent.channels import DeliveryError, send_to_self
from core.agent.jamiitek import JamiiTekUnavailable, _connect
from core.models import AgentRun

log = logging.getLogger("core.agent")

DEFAULT_EXPIRY_SQL = """
    SELECT
        'hosting' AS kind,
        w.name            AS item,
        c.name            AS client_name,
        NULLIF(c.phone, '') AS client_number,
        w.hosting_end_date AS expires_on,
        (w.hosting_end_date - CURRENT_DATE) AS days_left,
        w.monthly_cost    AS amount,
        w.auto_suspend_on_expiry AS will_auto_suspend
    FROM app_managedwebsite w
    JOIN app_client c ON c.id = w.client_id
    WHERE w.status = 'active'
      AND w.hosting_end_date <= CURRENT_DATE + %(hosting_days)s

    UNION ALL

    SELECT
        'domain',
        d.domain_name,
        c.name,
        NULLIF(c.phone, ''),
        d.expiry_date,
        (d.expiry_date - CURRENT_DATE),
        d.renewal_cost,
        false
    FROM app_domainrecord d
    JOIN app_managedwebsite w ON w.id = d.website_id
    JOIN app_client c ON c.id = w.client_id
    WHERE d.status NOT IN ('transferred', 'expired')
      AND d.expiry_date <= CURRENT_DATE + %(domain_days)s

    ORDER BY days_left ASC
    LIMIT 30
"""


def fetch_expiring():
    """Read hosting and domain expiries. Read-only, like every connector here."""
    sql = getattr(settings, "JAMIITEK_EXPIRY_SQL", "") or DEFAULT_EXPIRY_SQL

    if not sql.strip().lower().startswith(("select", "with")):
        raise JamiiTekUnavailable("configured SQL is not a SELECT — refusing to run it")

    params = {
        "hosting_days": getattr(settings, "AGENT_HOSTING_WARN_DAYS", 7),
        "domain_days": getattr(settings, "AGENT_DOMAIN_WARN_DAYS", 30),
    }

    rows = []
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                columns = [c[0] for c in cur.description]
                for record in cur.fetchall():
                    rows.append(dict(zip(columns, record)))
    except JamiiTekUnavailable:
        raise
    except Exception as exc:
        raise JamiiTekUnavailable(f"expiry read failed: {exc}") from exc
    return rows


def _urgency(row):
    """
    Lower sorts first. Anything already expired outranks everything pending,
    and an expired site that auto-suspends outranks an expired one that doesn't.
    """
    days = row.get("days_left")
    days = 999 if days is None else int(days)
    if days < 0:
        return (0, days if not row.get("will_auto_suspend") else days - 100)
    return (1, days)


def _format(rows):
    expired = [r for r in rows if (r.get("days_left") or 0) < 0]
    soon = [r for r in rows if (r.get("days_left") or 0) >= 0]

    lines = []

    if expired:
        lines.append(f"🚨 *IMEISHA MUDA* ({len(expired)})")
        lines.append("")
        for row in expired:
            days = abs(int(row.get("days_left") or 0))
            label = "Hosting" if row.get("kind") == "hosting" else "Domain"
            lines.append(f"  *{row.get('item')}*")
            lines.append(f"  {label} · {row.get('client_name')} · siku {days} zilizopita")
            if row.get("will_auto_suspend"):
                lines.append("  ⚠️ _Itasimamishwa yenyewe_")
            if row.get("client_number"):
                lines.append(f"  📞 {row['client_number']}")
            lines.append("")

    if soon:
        lines.append(f"⏳ *Inakaribia* ({len(soon)})")
        lines.append("")
        for row in soon:
            days = int(row.get("days_left") or 0)
            label = "Hosting" if row.get("kind") == "hosting" else "Domain"
            mark = "🔴" if days <= 3 else "🟡"
            amount = row.get("amount")
            money = f" · TZS {Decimal(str(amount)):,.0f}" if amount else ""
            when = "*leo*" if days == 0 else f"siku {days}"
            lines.append(f"  {mark} {row.get('item')} — {when}")
            lines.append(f"     {label} · {row.get('client_name')}{money}")
        lines.append("")

    if expired:
        lines.append("_Domain iliyoisha muda si mara zote inarudi. Shughulikia hizi kwanza._")

    return "\n".join(lines).rstrip()


def run_expiry_watch(now=None, deadline=None, dry_run=False):
    """
    Report hosting and domain expiries once a day.

    Deliberately unlike the other daily jobs in one respect: if anything has
    already expired, the daily marker is not written, so it reports again
    tomorrow and every day after until it is dealt with. A one-time mention of
    an expired domain is not enough — that is exactly the message that gets
    scrolled past on a busy morning.
    """
    now = now or timezone.now()

    if not getattr(settings, "AGENT_EXPIRY_WATCH_ENABLED", False):
        return {"job": "expiry_watch", "sent": 0, "failed": 0, "detail": "disabled"}

    if not getattr(settings, "JAMIITEK_DB_DSN", ""):
        return {"job": "expiry_watch", "sent": 0, "failed": 0, "detail": "JamiiTek not configured"}

    hour = getattr(settings, "AGENT_EXPIRY_WATCH_HOUR", 7)
    if timezone.localtime(now).hour < hour:
        return {"job": "expiry_watch", "sent": 0, "failed": 0, "detail": "too early"}

    if already_ran_today("expiry_watch", now=now):
        return {"job": "expiry_watch", "sent": 0, "failed": 0, "detail": "already ran today"}

    try:
        rows = fetch_expiring()
    except JamiiTekUnavailable as exc:
        log.warning("expiry watch skipped: %s", exc)
        return {"job": "expiry_watch", "sent": 0, "failed": 1, "detail": str(exc)[:200]}

    if not rows:
        if not dry_run:
            AgentRun.objects.create(job="expiry_watch", finished_at=timezone.now(),
                                    ok=True, processed=0, detail="nothing expiring")
        return {"job": "expiry_watch", "sent": 0, "failed": 0, "detail": "nothing expiring"}

    rows.sort(key=_urgency)
    text = _format(rows)
    has_expired = any((r.get("days_left") or 0) < 0 for r in rows)

    if dry_run:
        log.info("DRY RUN — expiry watch:\n%s", text)
        return {"job": "expiry_watch", "sent": 1, "failed": 0,
                "detail": f"{len(rows)} item(s), expired={has_expired}"}

    user = User.objects.filter(is_active=True).order_by("pk").first()
    if user is None:
        return {"job": "expiry_watch", "sent": 0, "failed": 1, "detail": "no user"}

    try:
        send_to_self(user, text)
    except DeliveryError as exc:
        log.error("expiry watch failed: %s", exc)
        return {"job": "expiry_watch", "sent": 0, "failed": 1, "detail": str(exc)[:200]}

    # Only mark the day done when nothing is actually expired. Anything past its
    # date keeps reporting daily until it is resolved.
    if not has_expired:
        AgentRun.objects.create(job="expiry_watch", finished_at=timezone.now(), ok=True,
                                processed=len(rows), detail=f"{len(rows)} upcoming")

    return {
        "job": "expiry_watch",
        "sent": 1,
        "failed": 0,
        "detail": f"{len(rows)} item(s), expired={has_expired}"
                  + ("" if not has_expired else " — will repeat daily"),
    }
