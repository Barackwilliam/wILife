from datetime import datetime

def current_year(request):
    return {'year': datetime.now().year}


def unread_notifications(request):
    """Unread notification count for the bell and sidebar badge."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    from .models import Notification
    return {"unread_count": Notification.objects.filter(user=user, read=False).count()}


def ai_health(request):
    """Hali ya AI (Gemini/Groq) kwa onyo la wafanyakazi — inasomwa tu kiolezo kikiitumia."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_staff:
        return {}

    def load():
        from core import llm
        try:
            return llm.health()
        except Exception:
            return None
    return {"ai_health": load}
