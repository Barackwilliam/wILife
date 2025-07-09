
# Create your views here.
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
from .forms import UserRegisterForm, UserUpdateForm, ProfileUpdateForm
from .models import Notification, Schedule,HealthRecord
from django.http import JsonResponse
from django.db.models import Sum

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


def home(request):
    return render(request, 'index.html')

from django.db.models import Count, Avg
import json
from datetime import date, timedelta

@login_required
def dashboard(request):
    user = request.user

    # User's data
    incomes = Income.objects.filter(user=user)
    expenses = Expense.objects.filter(user=user)
    tasks = Task.objects.filter(user=user)
    menstrual_records = MenstrualCycleRecord.objects.filter(user=user)

    # Totals
    income_total = incomes.aggregate(total=Sum('amount'))['total'] or 0
    expense_total = expenses.aggregate(total=Sum('amount'))['total'] or 0
    tasks_pending = tasks.filter(status='pending').count()
    tasks_done = tasks.filter(status='done').count()

    # Health averages (last 7 days)
    week_ago = date.today() - timedelta(days=7)
    health_avg = HealthRecord.objects.filter(user=user, date__gte=week_ago).aggregate(
        avg_weight=Avg('weight'),
        avg_exercise=Avg('exercise_minutes'),
        avg_sleep=Avg('sleep_hours'),
    )

    # Smart Recommendations (Deep, Logical, Dynamic)
    recommendations = []
    today = date.today()

    # Financial Insight
    if income_total > 0:
        savings_rate = 100 - ((expense_total / income_total) * 100)
        if savings_rate < 10:
            recommendations.append("🚨 Your savings rate is critically low. Aim to keep at least 20% of your income untouched.")
        elif savings_rate >= 50:
            recommendations.append("🧠 Exceptional savings discipline! Consider exploring long-term investment opportunities.")
    else:
        recommendations.append("⚠️ You haven't recorded any income. The dashboard thrives on data—keep it updated!")

    # Expense category check
    high_exp_cat = expenses.values('category').annotate(total=Sum('amount')).order_by('-total').first()
    if high_exp_cat and expense_total > 0:
        percentage = (high_exp_cat['total'] / expense_total) * 100
        if percentage >= 30:
            label = dict(Expense.CATEGORY_CHOICES).get(high_exp_cat['category'], high_exp_cat['category'])
            recommendations.append(f"💸 You’re spending over 30% of your total expenses on '{label}'. Review this area for optimization.")

    # Food spike
    current_week_food = expenses.filter(category='food', date__gte=today - timedelta(days=7)).aggregate(total=Sum('amount'))['total'] or 0
    previous_week_food = expenses.filter(category='food', date__lt=today - timedelta(days=7), date__gte=today - timedelta(days=14)).aggregate(total=Sum('amount'))['total'] or 0
    if previous_week_food > 0 and current_week_food > previous_week_food * 1.3:
        diff = current_week_food - previous_week_food
        recommendations.append(f"🍔 Food spending increased by Tsh.{diff:.2f}/= this week. Is it delivery fatigue or celebrations?")

    # Tasks Insight
    if tasks_pending > 5:
        recommendations.append("📌 You're juggling many pending tasks. Consider breaking them into smaller sub-goals.")
    elif tasks_pending == 0 and tasks_done > 0:
        recommendations.append("✅ Excellent productivity streak. You’ve cleared all tasks — reward yourself!")

    # Health Patterns
    if health_avg['avg_sleep'] and health_avg['avg_sleep'] < 6:
        recommendations.append("🌙 Sleep duration is below average. A consistent sleep schedule is crucial for mental clarity.")
    if health_avg['avg_exercise'] and health_avg['avg_exercise'] < 30:
        recommendations.append("💪 You exercised less than 30 minutes daily. Try micro workouts to stay active.")
    if health_avg['avg_weight'] and health_avg['avg_weight'] > 95:
        recommendations.append("⚖️ Your weight is trending high. Track your meals or seek nutritionist support.")

    # Monthly trend
    month_ago = today - timedelta(days=30)
    income_last_month = incomes.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or 0
    expense_last_month = expenses.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or 0
    if income_last_month and expense_last_month > income_last_month:
        recommendations.append("📉 Your expenses last month exceeded income. If repeated, this leads to debt — revise your strategy.")

    # Menstrual data
    if menstrual_records.exists():
        heavy_days = menstrual_records.filter(flow_level='heavy').count()
        if heavy_days >= 3:
            recommendations.append("🩸 Multiple heavy flow days recorded. If this persists, consult a health specialist.")
        long_cycles = [r for r in menstrual_records if r.cycle_length() > 35]
        if long_cycles:
            recommendations.append("🔁 Some cycles are unusually long. Irregularity may signal hormonal imbalance.")

    # Progress visuals
    tasks_total = tasks_pending + tasks_done
    tasks_pending_percent = (tasks_pending / tasks_total) * 100 if tasks_total else 0
    tasks_done_percent = (tasks_done / tasks_total) * 100 if tasks_total else 0
    expense_vs_income_percent = (expense_total / income_total) * 100 if income_total else 0

    # Expense visualization
    category_totals = expenses.values('category').annotate(total=Sum('amount'))
    categories = []
    expense_data = []
    for key, label in Expense.CATEGORY_CHOICES:
        categories.append(label)
        total = next((item['total'] for item in category_totals if item['category'] == key), 0)
        expense_data.append(float(total))

    # Base context
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

    # Menstrual chart data
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