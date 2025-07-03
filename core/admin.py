from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import (
    Income,
    Expense,
    Task,
    HealthRecord,
    Schedule,
    Notification,
    Profile
)


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ('user', 'source', 'amount', 'date')
    list_filter = ('date', 'source')
    search_fields = ('source', 'notes')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('user', 'category', 'amount', 'date')
    list_filter = ('category', 'date')
    search_fields = ('notes',)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'status', 'priority', 'date')
    list_filter = ('status', 'priority', 'date')
    search_fields = ('title', 'description')


@admin.register(HealthRecord)
class HealthRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'weight', 'exercise_minutes', 'sleep_hours')
    list_filter = ('date',)
    search_fields = ('user__username',)


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'start_datetime', 'end_datetime', 'location', 'reminder_sent')
    list_filter = ('start_datetime', 'reminder_sent')
    search_fields = ('title', 'location')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'message', 'created_at', 'read')
    list_filter = ('read', 'created_at')
    search_fields = ('message',)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'location', 'birth_date')
    search_fields = ('user__username', 'location')
