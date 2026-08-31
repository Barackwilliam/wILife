# Create your views here.
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
from .forms import UserRegisterForm, UserUpdateForm, ProfileUpdateForm
from .models import Notification, Schedule,HealthRecord
from django.http import JsonResponse
from django.db.models import Sum
from django.utils import timezone

from django.contrib.auth.decorators import login_required
from .models import Income, Expense, Task
from .forms import IncomeForm, ExpenseForm, TaskForm,ScheduleForm,HealthRecordForm
from django.db.models import Sum, Count, Avg

from datetime import date, timedelta
from django.http import HttpResponse
import pandas as pd
from xhtml2pdf import pisa
from django.template.loader import render_to_string
import json
from decimal import Decimal


def home(request):
    return render(request, 'index.html')

from django.db.models import Count, Avg
import json
from datetime import date, timedelta


@login_required
def dashboard(request):
    user = request.user

    # Data retrieval
    incomes = Income.objects.filter(user=user)
    expenses = Expense.objects.filter(user=user)
    tasks = Task.objects.filter(user=user)
    menstrual_records = MenstrualCycleRecord.objects.filter(user=user)

    # Totals
    income_total = incomes.aggregate(total=Sum('amount'))['total'] or Decimal("0")
    expense_total = expenses.aggregate(total=Sum('amount'))['total'] or Decimal("0")
    tasks_pending = tasks.filter(status='pending').count()
    tasks_done = tasks.filter(status='done').count()

    # Health Averages (last 7 days)
    week_ago = date.today() - timedelta(days=7)
    health_avg = HealthRecord.objects.filter(user=user, date__gte=week_ago).aggregate(
        avg_weight=Avg('weight'),
        avg_exercise=Avg('exercise_minutes'),
        avg_sleep=Avg('sleep_hours'),
    )

    # Smart Recommendations
    recommendations = []
    today = date.today()

    # Savings Rate
    if income_total > 0:
        savings_rate = Decimal("100") - ((expense_total / income_total) * Decimal("100"))
        if savings_rate < 10:
            recommendations.append("🚨 Your savings rate is critically low. Aim to keep at least 20% of your income untouched.")
        elif savings_rate >= 50:
            recommendations.append("🧠 Exceptional savings discipline! Consider exploring long-term investment opportunities.")
    else:
        recommendations.append("⚠️ You haven't recorded any income. The dashboard thrives on data—keep it updated!")

    # High Expense Category
    high_exp_cat = expenses.values('category').annotate(total=Sum('amount')).order_by('-total').first()
    if high_exp_cat and expense_total > 0:
        percentage = (Decimal(high_exp_cat['total']) / expense_total) * Decimal("100")
        if percentage >= 30:
            label = dict(Expense.CATEGORY_CHOICES).get(high_exp_cat['category'], high_exp_cat['category'])
            recommendations.append(f"💸 You’re spending over 30% of your total expenses on '{label}'. Review this area for optimization.")

    # Food Spending Spike
    current_week_food = expenses.filter(category='food', date__gte=today - timedelta(days=7)).aggregate(total=Sum('amount'))['total'] or Decimal("0")
    previous_week_food = expenses.filter(category='food', date__lt=today - timedelta(days=7), date__gte=today - timedelta(days=14)).aggregate(total=Sum('amount'))['total'] or Decimal("0")
    if previous_week_food > 0 and current_week_food > previous_week_food * Decimal("1.3"):
        diff = current_week_food - previous_week_food
        recommendations.append(f"🍔 Food spending increased by Tsh.{diff:.2f}/= this week. Is it delivery fatigue or celebrations?")

    # Task Progress
    if tasks_pending > 5:
        recommendations.append("📌 You're juggling many pending tasks. Consider breaking them into smaller sub-goals.")
    elif tasks_pending == 0 and tasks_done > 0:
        recommendations.append("✅ Excellent productivity streak. You’ve cleared all tasks — reward yourself!")

    # Health Monitoring
    if health_avg['avg_sleep'] and health_avg['avg_sleep'] < 6:
        recommendations.append("🌙 Sleep duration is below average. A consistent sleep schedule is crucial for mental clarity.")
    if health_avg['avg_exercise'] and health_avg['avg_exercise'] < 30:
        recommendations.append("💪 You exercised less than 30 minutes daily. Try micro workouts to stay active.")
    if health_avg['avg_weight'] and health_avg['avg_weight'] > 95:
        recommendations.append("⚖️ Your weight is trending high. Track your meals or seek nutritionist support.")

    # Monthly Financial Trend
    month_ago = today - timedelta(days=30)
    income_last_month = incomes.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or Decimal("0")
    expense_last_month = expenses.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or Decimal("0")
    if income_last_month and expense_last_month > income_last_month:
        recommendations.append("📉 Your expenses last month exceeded income. If repeated, this leads to debt — revise your strategy.")

    # Menstrual Health
    if menstrual_records.exists():
        heavy_days = menstrual_records.filter(flow_level='heavy').count()
        if heavy_days >= 3:
            recommendations.append("🩸 Multiple heavy flow days recorded. If this persists, consult a health specialist.")
        long_cycles = [r for r in menstrual_records if r.cycle_length() > 35]
        if long_cycles:
            recommendations.append("🔁 Some cycles are unusually long. Irregularity may signal hormonal imbalance.")

    # Task Chart Values
    tasks_total = tasks_pending + tasks_done
    tasks_pending_percent = (tasks_pending / tasks_total) * 100 if tasks_total else 0
    tasks_done_percent = (tasks_done / tasks_total) * 100 if tasks_total else 0

    # Expense vs Income %
    expense_vs_income_percent = float((expense_total / income_total) * Decimal("100")) if income_total > 0 else 0

    # Expense Chart Data
    category_totals = expenses.values('category').annotate(total=Sum('amount'))
    categories = []
    expense_data = []
    for key, label in Expense.CATEGORY_CHOICES:
        categories.append(label)
        total = next((item['total'] for item in category_totals if item['category'] == key), 0)
        expense_data.append(float(total))

    # Base Context
    context = {
        'income_total': income_total,
        'expense_total': expense_total,
        'tasks_pending': tasks_pending,
        'tasks_done': tasks_done,
        'health_avg': health_avg,
        'recommendations': recommendations,
        'tasks_pending_percent': tasks_pending_percent,
        'tasks_done_percent': tasks_done_percent,
        'expense_vs_income_percent': expense_vs_income_percent,
        'categories': json.dumps(categories),
        'expense_data': json.dumps(expense_data),
    }

    # Menstrual Charts (if any)
    if menstrual_records.exists():
        flow_counts = menstrual_records.values('flow_level').annotate(count=Count('flow_level'))
        flow_map = {'light': 'Light', 'medium': 'Medium', 'heavy': 'Heavy'}
        menstrual_flow_labels = [flow_map[k] for k in ['light', 'medium', 'heavy']]
        menstrual_flow_data = [next((c['count'] for c in flow_counts if c['flow_level'] == k), 0) for k in ['light', 'medium', 'heavy']]
        cycle_lengths = menstrual_records.order_by('start_date').values_list('start_date', 'end_date')
        menstrual_cycle_labels = [start.strftime('%b %d') for start, end in cycle_lengths if start and end]
        menstrual_cycle_data = [(end - start).days + 1 for start, end in cycle_lengths if start and end]

        context['menstrual_flow_levels'] = {
            'labels': json.dumps(menstrual_flow_labels),
            'data': json.dumps(menstrual_flow_data),
        }
        context['menstrual_cycle_lengths'] = {
            'labels': json.dumps(menstrual_cycle_labels),
            'data': json.dumps(menstrual_cycle_data),
        }

    return render(request, 'dashboard.html', context)


# CRUD Views for Income

@login_required
def income_list(request):
    incomes = Income.objects.filter(user=request.user).order_by('-date')
    return render(request, 'income_list.html', {'incomes': incomes})

@login_required
def income_create(request):
    if request.method == 'POST':
        form = IncomeForm(request.POST)
        if form.is_valid():
            income = form.save(commit=False)
            income.user = request.user
            income.save()
            Notification.objects.create(
                user=request.user,
                message=f"New income added: {income.source} - Tsh. {income.amount}"
            )
            messages.success(request, 'Income added successfully.')
            return redirect('income_list')
    else:
        form = IncomeForm()
    return render(request, 'income_form.html', {'form': form})

@login_required
def income_update(request, pk):
    income = get_object_or_404(Income, pk=pk, user=request.user)
    if request.method == 'POST':
        form = IncomeForm(request.POST, instance=income)
        if form.is_valid():
            form.save()
            Notification.objects.create(
                user=request.user,
                message=f"Income updated: {income.source} - Tsh. {income.amount}"
            )
            messages.success(request, 'Income updated successfully.')
            return redirect('income_list')
    else:
        form = IncomeForm(instance=income)
    return render(request, 'income_form.html', {'form': form})

@login_required
def income_delete(request, pk):
    income = get_object_or_404(Income, pk=pk, user=request.user)
    if request.method == 'POST':
        source = income.source
        amount = income.amount
        income.delete()
        Notification.objects.create(
            user=request.user,
            message=f"Income deleted: {source} - Tsh. {amount}"
        )
        messages.success(request, 'Income deleted successfully.')
        return redirect('income_list')
    return render(request, 'income_confirm_delete.html', {'income': income})



@login_required
def expense_list(request):
    expenses = Expense.objects.filter(user=request.user).order_by('-date')
    return render(request, 'expense_list.html', {'expenses': expenses})



@login_required
def expense_create(request):
    if request.method == 'POST':
        form = ExpenseForm(request.POST)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.user = request.user
            expense.save()
            Notification.objects.create(
                user=request.user,
                message=f"New expense added: {expense.category} - Tsh. {expense.amount}"
            )
            messages.success(request, 'Expense added successfully.')
            return redirect('expense_list')
    else:
        form = ExpenseForm()
    return render(request, 'expense_form.html', {'form': form})

@login_required
def expense_update(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user)
    if request.method == 'POST':
        form = ExpenseForm(request.POST, instance=expense)
        if form.is_valid():
            form.save()
            Notification.objects.create(
                user=request.user,
                message=f"Expense updated: {expense.category} - Tsh. {expense.amount}"
            )
            messages.success(request, 'Expense updated successfully.')
            return redirect('expense_list')
    else:
        form = ExpenseForm(instance=expense)
    return render(request, 'expense_form.html', {'form': form})

@login_required
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user)
    if request.method == 'POST':
        category = expense.category
        amount = expense.amount
        expense.delete()
        Notification.objects.create(
            user=request.user,
            message=f"Expense deleted: {category} - Tsh. {amount}"
        )
        messages.success(request, 'Expense deleted successfully.')
        return redirect('expense_list')
    return render(request, 'expense_confirm_delete.html', {'expense': expense})



@login_required
def task_list(request):
    tasks = Task.objects.filter(user=request.user).order_by('-date')
    return render(request, 'task_list.html', {'tasks': tasks})

@login_required
def task_create(request):
    if request.method == 'POST':
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            task.save()
            Notification.objects.create(
                user=request.user,
                message=f"New task added: {task.title}"
            )
            messages.success(request, 'Task added successfully.')
            return redirect('task_list')
    else:
        form = TaskForm()
    return render(request, 'task_form.html', {'form': form})

@login_required
def task_update(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user)
    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            Notification.objects.create(
                user=request.user,
                message=f"Task updated: {task.title}"
            )
            messages.success(request, 'Task updated successfully.')
            return redirect('task_list')
    else:
        form = TaskForm(instance=task)
    return render(request, 'task_form.html', {'form': form})

@login_required
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user)
    if request.method == 'POST':
        title = task.title
        task.delete()
        Notification.objects.create(
            user=request.user,
            message=f"Task deleted: {title}"
        )
        messages.success(request, 'Task deleted successfully.')
        return redirect('task_list')
    return render(request, 'task_confirm_delete.html', {'task': task})



# HealthRecord CRUD

@login_required
def health_list(request):
    health_records = HealthRecord.objects.filter(user=request.user).order_by('-date')
    return render(request, 'health_list.html', {'health_records': health_records})

@login_required
def health_create(request):
    if request.method == 'POST':
        form = HealthRecordForm(request.POST)
        if form.is_valid():
            health = form.save(commit=False)
            health.user = request.user
            health.save()
            Notification.objects.create(
                user=request.user,
                message=f"New health record added for date {health.date}"
            )
            messages.success(request, 'Health record added successfully.')
            return redirect('health_list')
    else:
        form = HealthRecordForm()
    return render(request, 'health_form.html', {'form': form})

@login_required
def health_update(request, pk):
    health = get_object_or_404(HealthRecord, pk=pk, user=request.user)
    if request.method == 'POST':
        form = HealthRecordForm(request.POST, instance=health)
        if form.is_valid():
            form.save()
            Notification.objects.create(
                user=request.user,
                message=f"Health record updated for date {health.date}"
            )
            messages.success(request, 'Health record updated successfully.')
            return redirect('health_list')
    else:
        form = HealthRecordForm(instance=health)
    return render(request, 'health_form.html', {'form': form})

@login_required
def health_delete(request, pk):
    health = get_object_or_404(HealthRecord, pk=pk, user=request.user)
    if request.method == 'POST':
        date_val = health.date
        health.delete()
        Notification.objects.create(
            user=request.user,
            message=f"Health record deleted for date {date_val}"
        )
        messages.success(request, 'Health record deleted successfully.')
        return redirect('health_list')
    return render(request, 'health_confirm_delete.html', {'health': health})


@login_required
def schedule_list(request):
    schedules = Schedule.objects.filter(user=request.user).order_by('start_datetime')
    return render(request, 'schedule_list.html', {'schedules': schedules})

@login_required
def schedule_create(request):
    if request.method == 'POST':
        form = ScheduleForm(request.POST)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.user = request.user
            schedule.save()
            Notification.objects.create(
                user=request.user,
                message=f"New schedule added: {schedule.title} on {schedule.start_datetime.strftime('%Y-%m-%d')}"
            )
            messages.success(request, 'Schedule added successfully.')
            return redirect('schedule_list')
    else:
        form = ScheduleForm()
    return render(request, 'schedule_form.html', {'form': form})

@login_required
def schedule_update(request, pk):
    schedule = get_object_or_404(Schedule, pk=pk, user=request.user)
    if request.method == 'POST':
        form = ScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            form.save()
            Notification.objects.create(
                user=request.user,
                message=f"Schedule updated: {schedule.title} on {schedule.start_datetime.strftime('%Y-%m-%d')}"
            )
            messages.success(request, 'Schedule updated successfully.')
            return redirect('schedule_list')
    else:
        form = ScheduleForm(instance=schedule)
    return render(request, 'schedule_form.html', {'form': form})

@login_required
def schedule_delete(request, pk):
    schedule = get_object_or_404(Schedule, pk=pk, user=request.user)
    if request.method == 'POST':
        title = schedule.title
        schedule.delete()
        Notification.objects.create(
            user=request.user,
            message=f"Schedule deleted: {title}"
        )
        messages.success(request, 'Schedule deleted successfully.')
        return redirect('schedule_list')
    return render(request, 'schedule_confirm_delete.html', {'schedule': schedule})


# Repeat similar CRUD views for Expense and Task (omitted here for brevity)

# Export reports as Excel


@login_required
def export_excel(request):
    user = request.user
    incomes = Income.objects.filter(user=user).values('amount', 'source', 'date', 'notes')
    expenses = Expense.objects.filter(user=user).values('amount', 'category', 'date', 'notes')
    tasks = Task.objects.filter(user=user).values('title', 'description', 'status', 'date', 'priority')
    health = HealthRecord.objects.filter(user=user).values('date', 'weight', 'exercise_minutes', 'sleep_hours')
    schedules = Schedule.objects.filter(user=user).values('title', 'description', 'start_datetime', 'end_datetime', 'location')

    with pd.ExcelWriter('report.xlsx', engine='openpyxl') as writer:
        pd.DataFrame(list(incomes)).to_excel(writer, sheet_name='Incomes', index=False)
        pd.DataFrame(list(expenses)).to_excel(writer, sheet_name='Expenses', index=False)
        pd.DataFrame(list(tasks)).to_excel(writer, sheet_name='Tasks', index=False)
        pd.DataFrame(list(health)).to_excel(writer, sheet_name='Health', index=False)
        pd.DataFrame(list(schedules)).to_excel(writer, sheet_name='Schedule', index=False)

    with open('report.xlsx', 'rb') as f:
        response = HttpResponse(f.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename=report.xlsx'
        return response

# Export reports as PDF

@login_required
def export_pdf(request):
    user = request.user
    
    # Data querysets
    incomes = Income.objects.filter(user=user).order_by('-date')
    expenses = Expense.objects.filter(user=user).order_by('-date')
    tasks = Task.objects.filter(user=user).order_by('-date')
    health = HealthRecord.objects.filter(user=user).order_by('-date')
    schedules = Schedule.objects.filter(user=user).order_by('start_datetime')
    menstrual = MenstrualCycleRecord.objects.filter(user=user).order_by('-start_date')
    notifications = Notification.objects.filter(user=user).order_by('-created_at')
    profile = Profile.objects.filter(user=user).first()

    # Summary calculations
    income_total = incomes.aggregate(total=Sum('amount'))['total'] or 0
    expense_total = expenses.aggregate(total=Sum('amount'))['total'] or 0
    tasks_pending = tasks.filter(status='pending').count()
    tasks_done = tasks.filter(status='done').count()

    # Prepare context for template
    context = {
        'user': user,
        'profile': profile,
        'incomes': incomes,
        'expenses': expenses,
        'tasks': tasks,
        'health': health,
        'schedules': schedules,
        'menstrual': menstrual,
        'notifications': notifications,
        # summary
        'income_total': income_total,
        'expense_total': expense_total,
        'tasks_pending': tasks_pending,
        'tasks_done': tasks_done,
    }

    # Render html content using your beautiful template
    html = render_to_string('report_pdf.html', context)

    # Create PDF response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="user_report.pdf"'

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error generating PDF', status=500)
    return response


    
def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Registration successful.')
            return redirect('dashboard')
    else:
        form = UserRegisterForm()
    return render(request, 'register.html', {'form': form})

@login_required
def notifications_list(request):
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'notifications.html', {'notifications': notifications})

@login_required
def mark_notification_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.read = True
    notification.save()
    return redirect('notifications_list')

@login_required
def notification_delete(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.delete()
    return redirect('notifications_list')

from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Schedule

@login_required
def calendar_view(request):
    schedules = Schedule.objects.filter(user=request.user)
    events = []

    for s in schedules:
        events.append({
            'title': s.title,
            'start': s.start_datetime.replace(microsecond=0).isoformat(),
            'end': s.end_datetime.replace(microsecond=0).isoformat(),
            'url': '',  # Optional: You can add link to update page
        })

    return render(request, 'calendar.html', {'events': events})

 
 
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import UserUpdateForm, ProfileUpdateForm

@login_required
def profile(request):
    if request.method == 'POST':
        user_form = UserUpdateForm(request.POST, instance=request.user)
        profile_form = ProfileUpdateForm(request.POST, instance=request.user.profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, 'Your profile has been updated successfully.')
            return redirect('profile')
    else:
        user_form = UserUpdateForm(instance=request.user)
        profile_form = ProfileUpdateForm(instance=request.user.profile)

    context = {
        'user_form': user_form,
        'profile_form': profile_form,
    }
    return render(request, 'profile.html', context)


from django.views.decorators.http import require_POST



def toggle_dark_mode(request):
    current = request.COOKIES.get('theme', 'light')
    new_theme = 'dark' if current == 'light' else 'light'
    response = JsonResponse({'theme': new_theme})
    response.set_cookie('theme', new_theme, max_age=365*24*60*60)
    return response


from django.contrib.auth import logout
from django.contrib import messages
from django.views.decorators.http import require_POST

@login_required
@require_POST
def logout_view(request):
    """
    Log out the current user and redirect to login page with a success message.
    Only accepts POST requests for security.
    """
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect('login')  # Replace 'login' with your login URL name

from django.contrib.auth.views import PasswordResetView
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.models import User
from django import forms
from django.core.exceptions import ValidationError


class CustomPasswordResetForm(PasswordResetForm):

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not User.objects.filter(email__iexact=email, is_active=True).exists():
            raise ValidationError("No user is registered with this email address.")
        return email

class CustomPasswordResetView(PasswordResetView):
    form_class = CustomPasswordResetForm
    template_name = 'password_reset.html'
    email_template_name = 'password_reset_email.html'
    subject_template_name = 'password_reset_subject.txt'
    success_url = '/password-reset/done/'


from .models import MenstrualCycleRecord
from .forms import MenstrualCycleForm
from django.contrib.auth.decorators import login_required

@login_required
def menstrual_list(request):
    records = MenstrualCycleRecord.objects.filter(user=request.user)
    return render(request, 'menstrual_list.html', {'records': records})

@login_required
def menstrual_create(request):
    if request.method == 'POST':
        form = MenstrualCycleForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.user = request.user
            record.save()
            return redirect('menstrual_list')
    else:
        form = MenstrualCycleForm()
    return render(request, 'menstrual_form.html', {'form': form})



from datetime import timedelta

def menstrual_calendar(request):
    records = MenstrualCycleRecord.objects.filter(user=request.user).order_by('start_date')
    events = []

    for record in records:
        # Period days
        events.append({
            'title': '🩸 Period',
            'start': record.start_date.isoformat(),
            'end': (record.end_date + timedelta(days=1)).isoformat(),
            'type': 'period',
            'flow_level': record.flow_level,
            'pain_level': record.pain_level,
            'mood': record.mood,
            'symptoms': record.symptoms,
            'notes': record.notes
        })

        # Predicted Ovulation (day 14 from start)
        ovulation_day = record.start_date + timedelta(days=14)
        events.append({
            'title': '💛 Ovulation',
            'start': ovulation_day.isoformat(),
            'type': 'ovulation'
        })

        # Safe Days: days before ovulation (e.g. days 5–9)
        for i in range(5, 10):
            safe_day = record.start_date + timedelta(days=i)
            events.append({
                'title': '✅ Safe Day',
                'start': safe_day.isoformat(),
                'type': 'safe'
            })

        # Danger Days: around ovulation (e.g. days 12–16)
        for i in range(12, 17):
            danger_day = record.start_date + timedelta(days=i)
            events.append({
                'title': '🚨 Danger Day',
                'start': danger_day.isoformat(),
                'type': 'danger'
            })

    context = {
        'events': events
    }
    return render(request, 'menstrual_calendar.html', context)


@login_required
def menstrual_update(request, pk):
    record = get_object_or_404(MenstrualCycleRecord, pk=pk, user=request.user)
    
    if request.method == 'POST':
        form = MenstrualCycleForm(request.POST, instance=record)
        if form.is_valid():
            form.save()
            messages.success(request, 'Record updated successfully!')
            return redirect('menstrual_list')
    else:
        form = MenstrualCycleForm(instance=record)

    return render(request, 'menstrual_form.html', {'form': form})


@login_required
def menstrual_delete(request, pk):
    record = get_object_or_404(MenstrualCycleRecord, pk=pk, user=request.user)
    if request.method == 'POST':
        record.delete()
        return redirect('menstrual_list')
    return render(request, 'menstrual_confirm_delete.html', {'record': record})

# ─── Goal Views ──────────────────────────────────────────────────────────────

from .models import Goal, GoalMilestone, GoalUpdate
from .forms import GoalForm, GoalUpdateForm, GoalMilestoneForm
from django.utils import timezone as tz


@login_required
def goal_list(request):
    goals = Goal.objects.filter(user=request.user)

    # Stats
    active = goals.filter(status='active').count()
    completed = goals.filter(status='completed').count()
    overdue = [g for g in goals.filter(status='active') if g.is_overdue()]

    # Category filter
    category = request.GET.get('category', '')
    status_filter = request.GET.get('status', '')
    if category:
        goals = goals.filter(category=category)
    if status_filter:
        goals = goals.filter(status=status_filter)

    context = {
        'goals': goals,
        'active_count': active,
        'completed_count': completed,
        'overdue_count': len(overdue),
        'categories': Goal.CATEGORY_CHOICES,
        'selected_category': category,
        'selected_status': status_filter,
    }
    return render(request, 'goal_list.html', context)


@login_required
def goal_create(request):
    if request.method == 'POST':
        form = GoalForm(request.POST)
        if form.is_valid():
            goal = form.save(commit=False)
            goal.user = request.user
            goal.save()
            messages.success(request, 'Goal created successfully!')
            return redirect('goal_list')
    else:
        form = GoalForm()
    return render(request, 'goal_form.html', {'form': form, 'action': 'Create'})


@login_required
def goal_update(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    if request.method == 'POST':
        form = GoalForm(request.POST, instance=goal)
        if form.is_valid():
            form.save()
            messages.success(request, 'Goal updated!')
            return redirect('goal_detail', pk=pk)
    else:
        form = GoalForm(instance=goal)
    return render(request, 'goal_form.html', {'form': form, 'action': 'Edit', 'goal': goal})


@login_required
def goal_delete(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    if request.method == 'POST':
        goal.delete()
        messages.success(request, 'Goal deleted.')
        return redirect('goal_list')
    return render(request, 'goal_confirm_delete.html', {'goal': goal})


@login_required
def goal_detail(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    updates = goal.updates.all()
    milestones = goal.milestones.all()

    # Chart data — last 10 updates
    chart_dates = [str(u.date) for u in updates[:10]][::-1]
    chart_values = [float(u.value) for u in updates[:10]][::-1]

    # Forms
    update_form = GoalUpdateForm(initial={'date': timezone.now().date()})
    milestone_form = GoalMilestoneForm()

    context = {
        'goal': goal,
        'updates': updates,
        'milestones': milestones,
        'update_form': update_form,
        'milestone_form': milestone_form,
        'chart_dates': json.dumps(chart_dates),
        'chart_values': json.dumps(chart_values),
    }
    return render(request, 'goal_detail.html', context)


@login_required
def goal_add_update(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    if request.method == 'POST':
        form = GoalUpdateForm(request.POST)
        if form.is_valid():
            update = form.save(commit=False)
            update.goal = goal
            update.save()
            # Update goal's current_value to latest entry
            goal.current_value = update.value
            # Auto-complete if reached target
            if goal.target_value and goal.current_value >= goal.target_value:
                goal.status = 'completed'
                messages.success(request, '🎉 Goal completed! Congratulations!')
            else:
                messages.success(request, 'Progress updated!')
            goal.save()
    return redirect('goal_detail', pk=pk)


@login_required
def goal_add_milestone(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    if request.method == 'POST':
        form = GoalMilestoneForm(request.POST)
        if form.is_valid():
            milestone = form.save(commit=False)
            milestone.goal = goal
            milestone.save()
            messages.success(request, 'Milestone added!')
    return redirect('goal_detail', pk=pk)


@login_required
def goal_toggle_milestone(request, pk, milestone_pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    milestone = get_object_or_404(GoalMilestone, pk=milestone_pk, goal=goal)
    milestone.is_completed = not milestone.is_completed
    milestone.completed_at = tz.now() if milestone.is_completed else None
    milestone.save()
    return redirect('goal_detail', pk=pk)


# ─── Dashboard Preferences Views ────────────────────────────────────────────

from .models import DashboardPreference
import json as _json

ALL_WIDGETS = [
    {'key': 'finance',        'label': 'Finance Summary',      'icon': 'bi-cash-stack',    'color': 'success'},
    {'key': 'tasks',          'label': 'Tasks Overview',        'icon': 'bi-check2-square', 'color': 'primary'},
    {'key': 'health',         'label': 'Health Insights',       'icon': 'bi-heart-pulse',   'color': 'danger'},
    {'key': 'goals',          'label': 'Goals Progress',        'icon': 'bi-trophy',        'color': 'warning'},
    {'key': 'expense_chart',  'label': 'Expense Chart',         'icon': 'bi-bar-chart',     'color': 'info'},
    {'key': 'schedule',       'label': 'Upcoming Schedule',     'icon': 'bi-calendar-event','color': 'secondary'},
    {'key': 'recommendations','label': 'Smart Recommendations', 'icon': 'bi-lightbulb',     'color': 'warning'},
    {'key': 'menstrual',      'label': 'Menstrual Tracker',     'icon': 'bi-calendar-heart','color': 'pink'},
    {'key': 'net_worth',      'label': 'Net Worth',             'icon': 'bi-bank',          'color': 'success'},
]

DEFAULT_ORDER = [w['key'] for w in ALL_WIDGETS]


def _get_or_create_prefs(user):
    prefs, _ = DashboardPreference.objects.get_or_create(user=user)
    return prefs


@login_required
def dashboard(request):
    user = request.user

    # ── Preferences ──────────────────────────────────────────────────────────
    prefs = _get_or_create_prefs(user)
    widget_order = prefs.get_widget_order() or DEFAULT_ORDER
    hidden = prefs.get_hidden_widgets()

    # Ensure new widgets not in saved order are appended
    for w in DEFAULT_ORDER:
        if w not in widget_order:
            widget_order.append(w)

    # ── Data ─────────────────────────────────────────────────────────────────
    from .models import MenstrualCycleRecord, Goal
    incomes  = Income.objects.filter(user=user)
    expenses = Expense.objects.filter(user=user)
    tasks    = Task.objects.filter(user=user)
    menstrual_records = MenstrualCycleRecord.objects.filter(user=user)
    goals    = Goal.objects.filter(user=user, status='active') if 'goals' not in hidden else Goal.objects.none()

    income_total  = incomes.aggregate(total=Sum('amount'))['total']  or Decimal('0')
    expense_total = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    net_worth     = income_total - expense_total

    tasks_pending = tasks.filter(status='pending').count()
    tasks_done    = tasks.filter(status='done').count()
    tasks_total   = tasks_pending + tasks_done
    tasks_pending_percent = (tasks_pending / tasks_total * 100) if tasks_total else 0
    tasks_done_percent    = (tasks_done    / tasks_total * 100) if tasks_total else 0

    week_ago   = date.today() - timedelta(days=7)
    health_avg = HealthRecord.objects.filter(user=user, date__gte=week_ago).aggregate(
        avg_weight=Avg('weight'),
        avg_exercise=Avg('exercise_minutes'),
        avg_sleep=Avg('sleep_hours'),
    )

    expense_vs_income_percent = float(
        (expense_total / income_total) * Decimal('100')
    ) if income_total > 0 else 0

    # Expense chart data
    category_totals = expenses.values('category').annotate(total=Sum('amount'))
    categories, expense_data = [], []
    for key, label in Expense.CATEGORY_CHOICES:
        categories.append(label)
        expense_data.append(float(
            next((i['total'] for i in category_totals if i['category'] == key), 0)
        ))

    # Upcoming schedule (next 7 days)
    from django.utils import timezone as _tz
    now = _tz.now()
    upcoming_schedule = Schedule.objects.filter(
        user=user,
        start_datetime__gte=now,
        start_datetime__lte=now + timedelta(days=7)
    ).order_by('start_datetime')[:5]

    # Goals summary
    goals_list = list(goals[:4])

    # Recommendations
    recommendations = []
    today = date.today()
    if income_total > 0:
        savings_rate = Decimal('100') - ((expense_total / income_total) * Decimal('100'))
        if savings_rate < 10:
            recommendations.append('🚨 Savings rate critically low. Aim for at least 20%.')
        elif savings_rate >= 50:
            recommendations.append('🧠 Exceptional savings! Consider long-term investments.')
    else:
        recommendations.append('⚠️ No income recorded yet. Keep your data updated!')

    high_exp_cat = expenses.values('category').annotate(total=Sum('amount')).order_by('-total').first()
    if high_exp_cat and expense_total > 0:
        pct = (Decimal(high_exp_cat['total']) / expense_total) * Decimal('100')
        if pct >= 30:
            lbl = dict(Expense.CATEGORY_CHOICES).get(high_exp_cat['category'], '')
            recommendations.append(f"💸 Over 30% of expenses on '{lbl}'. Review this area.")

    if tasks_pending > 5:
        recommendations.append('📌 Many pending tasks. Break them into smaller goals.')
    elif tasks_pending == 0 and tasks_done > 0:
        recommendations.append('✅ All tasks cleared — reward yourself!')

    if health_avg['avg_sleep'] and health_avg['avg_sleep'] < 6:
        recommendations.append('🌙 Sleep below 6hrs/night. Prioritise rest.')
    if health_avg['avg_exercise'] and health_avg['avg_exercise'] < 30:
        recommendations.append('💪 Less than 30 min exercise/day. Try micro-workouts.')

    month_ago = today - timedelta(days=30)
    inc_m = incomes.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    exp_m = expenses.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    if inc_m and exp_m > inc_m:
        recommendations.append('📉 Last month expenses exceeded income. Revise your budget.')

    if menstrual_records.exists():
        if menstrual_records.filter(flow_level='heavy').count() >= 3:
            recommendations.append('🩸 Multiple heavy flow days. Consider consulting a specialist.')

    # Menstrual chart data
    menstrual_flow_levels = menstrual_cycle_lengths = None
    if menstrual_records.exists():
        flow_counts = menstrual_records.values('flow_level').annotate(count=Count('flow_level'))
        flow_map = {'light': 'Light', 'medium': 'Medium', 'heavy': 'Heavy'}
        menstrual_flow_levels = {
            'labels': _json.dumps([flow_map[k] for k in ['light', 'medium', 'heavy']]),
            'data':   _json.dumps([next((c['count'] for c in flow_counts if c['flow_level'] == k), 0) for k in ['light', 'medium', 'heavy']]),
        }
        cl = menstrual_records.order_by('start_date').values_list('start_date', 'end_date')
        menstrual_cycle_lengths = {
            'labels': _json.dumps([s.strftime('%b %d') for s, e in cl if s and e]),
            'data':   _json.dumps([(e - s).days + 1 for s, e in cl if s and e]),
        }

    context = {
        # widget layout
        'widget_order':  widget_order,
        'hidden_widgets': hidden,
        'all_widgets':   ALL_WIDGETS,
        'prefs':         prefs,
        # finance
        'income_total':  income_total,
        'expense_total': expense_total,
        'net_worth':     net_worth,
        'expense_vs_income_percent': expense_vs_income_percent,
        'categories':    _json.dumps(categories),
        'expense_data':  _json.dumps(expense_data),
        # tasks
        'tasks_pending':         tasks_pending,
        'tasks_done':            tasks_done,
        'tasks_pending_percent': tasks_pending_percent,
        'tasks_done_percent':    tasks_done_percent,
        # health
        'health_avg': health_avg,
        # goals
        'goals_list': goals_list,
        # schedule
        'upcoming_schedule': upcoming_schedule,
        # recommendations
        'recommendations': recommendations,
        # menstrual
        'menstrual_flow_levels':  menstrual_flow_levels,
        'menstrual_cycle_lengths': menstrual_cycle_lengths,
    }
    return render(request, 'dashboard.html', context)


@login_required
def dashboard_save_prefs(request):
    """AJAX endpoint — saves widget order, visibility, layout, accent."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data  = _json.loads(request.body)
        prefs = _get_or_create_prefs(request.user)
        if 'order' in data:
            prefs.widget_order   = _json.dumps(data['order'])
        if 'hidden' in data:
            prefs.hidden_widgets = _json.dumps(data['hidden'])
        if 'layout' in data and data['layout'] in ('default', 'compact', 'wide'):
            prefs.layout = data['layout']
        if 'accent' in data and data['accent'] in ('blue', 'green', 'purple', 'orange'):
            prefs.accent_color = data['accent']
        prefs.save()
        return JsonResponse({'status': 'ok'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)