from django.db import models

# Create your models here.
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver




class Income(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='incomes')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    source = models.CharField(max_length=255)
    date = models.DateField()
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.source} - {self.amount}"

class Expense(models.Model):
    CATEGORY_CHOICES = [
        ('food', 'Food'),
        ('transport', 'Transport'),
        ('entertainment', 'Entertainment'),
        ('bills', 'Bills'),
        ('health', 'Health'),
        ('education', 'Education'),
        ('business', 'Business'),
        ('love', 'Love'),
        ('debt', 'Debt'),
        ('beauty', 'Beauty'),
        ('savings', 'Savings / investment'),  # money put aside — not spending
        ('other', 'Other'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    date = models.DateField()
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.category} - {self.amount}"



class Task(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('done', 'Done'),
    ]
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    date = models.DateField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')

    def __str__(self):
        return f"{self.title} - {self.status}"



class HealthRecord(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='health_records')
    date = models.DateField()
    weight = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # in kg
    exercise_minutes = models.PositiveIntegerField(null=True, blank=True)
    sleep_hours = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"Health on {self.date}"


class Schedule(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True, null=True)
    reminder_datetime = models.DateTimeField(blank=True, null=True)
    reminder_sent = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["reminder_sent", "reminder_datetime"],
                         name="core_sched_reminder_idx"),
        ]

        def __str__(self):
            return self.title

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    def __str__(self):
        return f"Notification for {self.user.username}: {self.message}"

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bio = models.TextField(blank=True, null=True)
    location = models.CharField(max_length=100, blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    whatsapp_number = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="WhatsApp number the agent sends to, e.g. 255629712678",
    )

    @receiver(post_save, sender=User)
    def create_or_update_user_profile(sender, instance, created, **kwargs):
        if created:
            Profile.objects.create(user=instance)
        instance.profile.save()



# models.py

from django.conf import settings

class MenstrualCycleRecord(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    start_date = models.DateField()  # Tarehe ya kuanza hedhi
    end_date = models.DateField()    # Tarehe ya kumaliza
    flow_level = models.CharField(
        max_length=20,
        choices=[('light', 'Light'), ('medium', 'Medium'), ('heavy', 'Heavy')],
        default='medium'
    )
    pain_level = models.IntegerField(default=0)  # 0–10
    mood = models.CharField(max_length=100, blank=True, null=True)
    symptoms = models.TextField(blank=True, null=True)  # cramps, nausea, fatigue, etc.
    notes = models.TextField(blank=True, null=True)
    recorded_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.user.username} - {self.start_date} to {self.end_date}"

    def cycle_length(self):
        return (self.end_date - self.start_date).days + 1


# ─── Goal Setting & Progress Tracker ───────────────────────────────────────

class Goal(models.Model):
    CATEGORY_CHOICES = [
        ('finance', 'Finance'),
        ('health', 'Health'),
        ('education', 'Education'),
        ('career', 'Career'),
        ('personal', 'Personal'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('paused', 'Paused'),
        ('cancelled', 'Cancelled'),
    ]
    KIND_MANUAL = 'manual'
    KIND_MONTHLY_SAVINGS = 'monthly_savings'
    KIND_CHOICES = [
        (KIND_MANUAL, 'I update progress myself'),
        (KIND_MONTHLY_SAVINGS, 'Monthly savings (income − expenses, automatic)'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='goals')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_MANUAL,
                            help_text="Monthly savings goals track themselves from your income and expenses")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='personal')
    target_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                       help_text="Target number (e.g. 1000000 for savings goal)")
    current_value = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                        help_text="Current progress value")
    unit = models.CharField(max_length=50, blank=True, null=True,
                            help_text="Unit of measurement e.g. Tsh, kg, books")
    start_date = models.DateField(default=timezone.now)
    target_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.user.username})"

    def progress_percent(self):
        if self.target_value and self.target_value > 0:
            pct = (self.current_value / self.target_value) * 100
            return min(float(pct), 100)
        return 0

    def days_remaining(self):
        today = timezone.now().date()
        delta = self.target_date - today
        return delta.days

    def is_overdue(self):
        return self.days_remaining() < 0 and self.status == 'active' and not self.is_monthly_savings

    @property
    def is_monthly_savings(self):
        return self.kind == self.KIND_MONTHLY_SAVINGS


class GoalMilestone(models.Model):
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name='milestones')
    title = models.CharField(max_length=255)
    target_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.goal.title} — {self.title}"


class GoalUpdate(models.Model):
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name='updates')
    value = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.TextField(blank=True, null=True)
    date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.goal.title} update: {self.value}"


# ─── Dashboard Preferences ──────────────────────────────────────────────────

class DashboardPreference(models.Model):
    """Stores per-user widget order, visibility, and layout preferences."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='dashboard_prefs')

    # JSON list of widget keys in display order, e.g. ["finance","tasks","health",...]
    widget_order = models.TextField(default='[]')

    # JSON dict of hidden widgets, e.g. {"menstrual": true}
    hidden_widgets = models.TextField(default='{}')

    # Layout: 'default' | 'compact' | 'wide'
    layout = models.CharField(max_length=20, default='default')

    # Color theme accent: 'blue' | 'green' | 'purple' | 'orange'
    accent_color = models.CharField(max_length=20, default='blue')

    updated_at = models.DateTimeField(auto_now=True)

    def get_widget_order(self):
        import json as _json
        try:
            return _json.loads(self.widget_order) or []
        except Exception:
            return []

    def get_hidden_widgets(self):
        import json as _json
        try:
            return _json.loads(self.hidden_widgets) or {}
        except Exception:
            return {}

    def __str__(self):
        return f"Dashboard prefs for {self.user.username}"



class AgentRun(models.Model):
    job = models.CharField(max_length=64)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    processed = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    detail = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["-started_at"], name="core_agentr_started_idx")]

    def __str__(self):
        status = "ok" if self.ok else "failed"
        return f"{self.job} @ {self.started_at:%Y-%m-%d %H:%M} — {status}"



class ApprovalRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("expired", "Expired"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="approval_requests")
    tool = models.CharField(max_length=64)
    code = models.CharField(max_length=8, db_index=True)
    recipient_name = models.CharField(max_length=120, blank=True, default="")
    recipient_number = models.CharField(max_length=32, blank=True, default="")
    body = models.TextField()
    context = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="pending")
    result = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "code"], name="core_appr_status_code_idx"),
        ]

    def __str__(self):
        return f"[{self.code}] {self.tool} -> {self.recipient_name or self.recipient_number} ({self.status})"