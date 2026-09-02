"""
Goals and milestones.

I have not seen your Goal / GoalMilestone models, so this module does not assume
their field names. It looks them up at runtime and picks the first field it
recognises from a list of common names.

That is a guess, and a guess that goes into your morning brief is worth
verifying before you trust it:

    python manage.py check_goals

That command prints exactly which fields it matched and what it would report.
Read it once. If it picked the wrong field, set the override in settings rather
than editing this file.

If the models do not exist at all, every function here returns empty and the
brief simply omits the section.
"""

import logging
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.utils import timezone

log = logging.getLogger("core.agent")

# Candidate field names, in priority order.
TITLE_FIELDS = ["title", "name", "goal", "description"]
DATE_FIELDS = ["due_date", "target_date", "deadline", "end_date", "date", "target"]
DONE_FIELDS = ["is_completed", "completed", "is_done", "done", "achieved"]
STATUS_FIELDS = ["status", "state"]
DONE_VALUES = {"done", "completed", "complete", "achieved", "finished"}


def _model(name):
    try:
        return apps.get_model("core", name)
    except LookupError:
        return None


def _field_names(model):
    return {f.name for f in model._meta.get_fields() if hasattr(f, "attname")}


def _pick(model, candidates, override=None):
    """Choose a field: an explicit settings override wins, else first match."""
    available = _field_names(model)
    if override and override in available:
        return override
    for name in candidates:
        if name in available:
            return name
    return None


def describe():
    """
    Report what this module detected. Used by check_goals and worth reading
    before the brief starts quoting these numbers at you every morning.
    """
    out = {}
    for model_name in ("GoalMilestone", "Goal"):
        model = _model(model_name)
        if model is None:
            out[model_name] = {"found": False}
            continue
        out[model_name] = {
            "found": True,
            "fields": sorted(_field_names(model)),
            "title_field": _pick(model, TITLE_FIELDS,
                                 getattr(settings, "AGENT_GOAL_TITLE_FIELD", None)),
            "date_field": _pick(model, DATE_FIELDS,
                                getattr(settings, "AGENT_GOAL_DATE_FIELD", None)),
            "done_field": _pick(model, DONE_FIELDS,
                                getattr(settings, "AGENT_GOAL_DONE_FIELD", None)),
            "status_field": _pick(model, STATUS_FIELDS),
            "has_user": "user" in _field_names(model),
        }
    return out


def _unfinished(qs, model, done_field, status_field):
    """Exclude anything already completed, whichever way this schema records it."""
    if done_field:
        return qs.filter(**{done_field: False})
    if status_field:
        return qs.exclude(**{f"{status_field}__in": list(DONE_VALUES)})
    return qs


def upcoming_milestones(user, days=None):
    """
    Milestones falling due within the warning window, plus any already overdue.

    The window is deliberately wider than one day. A milestone you learn about
    on the morning it is due is not a warning, it is an obituary — this is
    Hofstadter's Law applied where it actually costs you something.
    """
    days = days or getattr(settings, "AGENT_GOAL_WARNING_DAYS", 3)

    model = _model("GoalMilestone") or _model("Goal")
    if model is None:
        return []

    title_field = _pick(model, TITLE_FIELDS, getattr(settings, "AGENT_GOAL_TITLE_FIELD", None))
    date_field = _pick(model, DATE_FIELDS, getattr(settings, "AGENT_GOAL_DATE_FIELD", None))
    done_field = _pick(model, DONE_FIELDS, getattr(settings, "AGENT_GOAL_DONE_FIELD", None))
    status_field = _pick(model, STATUS_FIELDS)

    if not date_field or not title_field:
        log.warning("goals: could not identify title/date fields — skipping")
        return []

    today = timezone.localtime().date()
    horizon = today + timedelta(days=days)

    try:
        qs = model.objects.all()
        if "user" in _field_names(model):
            qs = qs.filter(user=user)
        elif "goal" in _field_names(model):
            # milestone -> goal -> user
            try:
                qs = qs.filter(goal__user=user)
            except Exception:
                pass

        qs = _unfinished(qs, model, done_field, status_field)
        qs = qs.filter(**{f"{date_field}__lte": horizon}).order_by(date_field)[:10]
        rows = list(qs)
    except Exception as exc:
        log.warning("goals lookup failed (%s) — skipping section", exc)
        return []

    out = []
    for row in rows:
        due = getattr(row, date_field, None)
        if due is None:
            continue
        if hasattr(due, "date"):
            due = timezone.localtime(due).date() if timezone.is_aware(due) else due.date()
        out.append({
            "title": str(getattr(row, title_field, "") or "")[:80],
            "due": due,
            "days_left": (due - today).days,
        })
    return out


def brief_section(user):
    """Return the milestone lines for the morning brief, or [] to omit it."""
    if not getattr(settings, "AGENT_GOALS_ENABLED", False):
        return []

    items = upcoming_milestones(user)
    if not items:
        return []

    lines = ["🎯 *Malengo yanayokaribia*"]
    for item in items:
        left = item["days_left"]
        if left < 0:
            lines.append(f"  ⚠️ {item['title']} — imepita siku {abs(left)}")
        elif left == 0:
            lines.append(f"  🔴 {item['title']} — *leo*")
        else:
            lines.append(f"  ⏳ {item['title']} — siku {left}")
    lines.append("")
    return lines
