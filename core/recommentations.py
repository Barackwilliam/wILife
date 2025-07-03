from .models import Income, Expense, Task
from django.utils.timezone import now
from datetime import timedelta

def generate_recommendations(user):
    recommendations = []
    today = now().date()
    week_ago = today - timedelta(days=7)

    # Check if expenses on food category increased compared to previous week
    current_week_food = Expense.objects.filter(user=user, category='food', date__gte=week_ago).aggregate(total=Sum('amount'))['total'] or 0
    previous_week_food = Expense.objects.filter(user=user, category='food', date__lt=week_ago, date__gte=week_ago - timedelta(days=7)).aggregate(total=Sum('amount'))['total'] or 0

    if current_week_food > previous_week_food * 1.2:  # 20% increase
        recommendations.append(f"You spent more than usual this week on food: ${current_week_food:.2f}")

    # Check pending tasks
    pending_tasks = Task.objects.filter(user=user, status='pending', date__lte=today).count()
    if pending_tasks > 0:
        recommendations.append(f"You have {pending_tasks} pending tasks — consider rescheduling or completing them.")

    # Check if income is less than expenses in last month
    month_ago = today - timedelta(days=30)
    income_last_month = Income.objects.filter(user=user, date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or 0
    expense_last_month = Expense.objects.filter(user=user, date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or 0
    if expense_last_month > income_last_month:
        recommendations.append("Your expenses exceeded your income in the last month. Consider reviewing your budget.")

    return recommendations
