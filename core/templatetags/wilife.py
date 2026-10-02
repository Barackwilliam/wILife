"""Template helpers for the wILife UI."""

from django import template
from django.forms.widgets import CheckboxInput, RadioSelect, Select, SelectMultiple

register = template.Library()


@register.filter
def styled(field, extra=""):
    """Render a bound field with the design-system class for its widget type."""
    widget = field.field.widget
    if isinstance(widget, CheckboxInput):
        css = "form-check-input"
    elif isinstance(widget, (Select, SelectMultiple)) and not isinstance(widget, RadioSelect):
        css = "form-select"
    else:
        css = "form-control"
    existing = widget.attrs.get("class", "")
    classes = {c for c in existing.split() if c not in ("form-control", "form-select")}
    classes.add(css)
    if extra:
        classes.update(extra.split())
    if field.errors:
        classes.add("is-invalid")
    return field.as_widget(attrs={"class": " ".join(sorted(classes))})


@register.filter
def initials(user):
    name = (user.get_full_name() or user.get_username() or "?").strip()
    parts = name.split()
    return (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()


@register.filter
def pct(value, total):
    try:
        value, total = float(value), float(total)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, round(value / total * 100))) if total else 0


@register.filter
def money(value, decimals=0):
    """1234567.8 -> '1,234,568' (thousand separators, no currency)."""
    try:
        return f"{float(value):,.{int(decimals)}f}"
    except (TypeError, ValueError):
        return value
