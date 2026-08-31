"""
HTTP entry points for the agent.

Two endpoints, doing different jobs — do not confuse them:

  /healthz/     cheap keep-alive. Point UptimeRobot here to stop Render
                spinning the service down. Does no work.

  /agent/tick/  runs the heartbeat. Point a scheduled ping here at whatever
                cadence reminders actually need. Token protected.
"""

import secrets

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.agent.jobs import run_tick


@require_http_methods(["GET", "HEAD"])
def healthz(request):
    """Keep-alive only. Never do work here — it must stay instant."""
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_http_methods(["GET", "POST", "HEAD"])
def agent_tick(request):
    """
    Trigger one agent tick.

    Auth: send the shared secret either as an 'X-Agent-Token' header (preferred)
    or as a '?token=' query parameter (use this if your pinger cannot set custom
    headers — note that URLs with query strings tend to end up in access logs,
    so treat that token as lower-trust and rotate it if it leaks).

    The tick is bounded by AGENT_TICK_BUDGET_SECONDS so it always returns before
    a typical 30-second pinger timeout. Work that doesn't fit is left for the
    next tick, not dropped.
    """
    expected = settings.AGENT_TICK_TOKEN
    if not expected:
        return JsonResponse({"detail": "AGENT_TICK_TOKEN is not configured"}, status=503)

    provided = request.headers.get("X-Agent-Token") or request.GET.get("token", "")
    if not secrets.compare_digest(str(provided), str(expected)):
        return JsonResponse({"detail": "forbidden"}, status=403)

    if request.method == "HEAD":
        return JsonResponse({"status": "ok"})

    dry_run = request.GET.get("dry_run") in ("1", "true", "yes")
    summary = run_tick(dry_run=dry_run)
    return JsonResponse(summary, status=200 if summary["ok"] else 207)
