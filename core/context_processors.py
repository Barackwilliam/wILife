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
