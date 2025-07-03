
# Create your views here.
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
from .forms import UserRegisterForm, UserUpdateForm, ProfileUpdateForm
from .models import Notification, Schedule,HealthRecord
from django.http import JsonResponse


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


@login_required
def dashboard(request):
    user = request.user

    incomes = Income.objects.filter(user=user)
    expenses = Expense.objects.filter(user=user)
    tasks = Task.objects.filter(user=user)

    # Totals
    income_total = incomes.aggregate(total=Sum('amount'))['total'] or 0
    expense_total = expenses.aggregate(total=Sum('amount'))['total'] or 0
    tasks_pending = tasks.filter(status='pending').count()
    tasks_done = tasks.filter(status='done').count()

    # Health averages last 7 days
    week_ago = date.today() - timedelta(days=7)
    health_avg = HealthRecord.objects.filter(user=user, date__gte=week_ago).aggregate(
        avg_weight=Avg('weight'),
        avg_exercise=Avg('exercise_minutes'),
        avg_sleep=Avg('sleep_hours'),
    )

    # Recommendations
    recommendations = []
    if expense_total > income_total:
        recommendations.append("Your expenses exceed your income. Consider budgeting better.")
    if tasks_pending > 5:
        recommendations.append("You have many pending tasks. Try to complete or reschedule them.")
    if health_avg['avg_sleep'] and health_avg['avg_sleep'] < 6:
        recommendations.append("You are sleeping less than recommended. Try to get more rest.")

    today = date.today()
    week_ago = today - timedelta(days=7)

    current_week_food = expenses.filter(category='food', date__gte=week_ago).aggregate(total=Sum('amount'))['total'] or 0
    previous_week_food = expenses.filter(category='food', date__lt=week_ago, date__gte=week_ago - timedelta(days=7)).aggregate(total=Sum('amount'))['total'] or 0

    if current_week_food > previous_week_food * 1.2:
        recommendations.append(f"You spent more than usual this week on food: Tsh.{current_week_food:.2f}/=")

    if tasks_pending > 0:
        recommendations.append(f"You have {tasks_pending} pending tasks — consider rescheduling or completing them.")

    month_ago = today - timedelta(days=30)
    income_last_month = incomes.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or 0
    expense_last_month = expenses.filter(date__gte=month_ago).aggregate(total=Sum('amount'))['total'] or 0
    if expense_last_month > income_last_month:
        recommendations.append("Your expenses exceeded your income in the last month. Consider reviewing your budget.")

    # Calculate task percentages for progress bars
    tasks_total = tasks_pending + tasks_done
    if tasks_total > 0:
        tasks_pending_percent = (tasks_pending / tasks_total) * 100
        tasks_done_percent = (tasks_done / tasks_total) * 100
    else:
        tasks_pending_percent = 0
        tasks_done_percent = 0

    # Calculate expense vs income percentage for progress bar
    if income_total > 0:
        expense_vs_income_percent = (expense_total / income_total) * 100
    else:
        expense_vs_income_percent = 0


    category_totals = expenses.values('category').annotate(total=Sum('amount'))
    categories = []
    expense_data = []

    for key, label in Expense.CATEGORY_CHOICES:
        categories.append(label)
        total = next((item['total'] for item in category_totals if item['category'] == key), 0)
        expense_data.append(float(total))

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
            messages.success(request, 'Income updated successfully.')
            return redirect('income_list')
    else:
        form = IncomeForm(instance=income)
    return render(request, 'income_form.html', {'form': form})

@login_required
def income_delete(request, pk):
    income = get_object_or_404(Income, pk=pk, user=request.user)
    if request.method == 'POST':
        income.delete()
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
            messages.success(request, 'Expense updated successfully.')
            return redirect('expense_list')
    else:
        form = ExpenseForm(instance=expense)
    return render(request, 'expense_form.html', {'form': form})


@login_required
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user)
    if request.method == 'POST':
        expense.delete()
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
            messages.success(request, 'Task updated successfully.')
            return redirect('task_list')
    else:
        form = TaskForm(instance=task)
    return render(request, 'task_form.html', {'form': form})

@login_required
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user)
    if request.method == 'POST':
        task.delete()
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
            messages.success(request, 'Health record updated successfully.')
            return redirect('health_list')
    else:
        form = HealthRecordForm(instance=health)
    return render(request, 'health_form.html', {'form': form})

@login_required
def health_delete(request, pk):
    health = get_object_or_404(HealthRecord, pk=pk, user=request.user)
    if request.method == 'POST':
        health.delete()
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
            messages.success(request, 'Schedule updated successfully.')
            return redirect('schedule_list')
    else:
        form = ScheduleForm(instance=schedule)
    return render(request, 'schedule_form.html', {'form': form})

@login_required
def schedule_delete(request, pk):
    schedule = get_object_or_404(Schedule, pk=pk, user=request.user)
    if request.method == 'POST':
        schedule.delete()
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
    incomes = Income.objects.filter(user=user)
    expenses = Expense.objects.filter(user=user)
    tasks = Task.objects.filter(user=user)
    health = HealthRecord.objects.filter(user=user)
    schedules = Schedule.objects.filter(user=user)

    html = render_to_string('report_pdf.html', {
        'incomes': incomes,
        'expenses': expenses,
        'tasks': tasks,
        'health': health,
        'schedules': schedules,
        'user': user,
    })
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="report.pdf"'
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
