from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Schedule, Profile
from .models import Income,HealthRecord, Expense, Task

class IncomeForm(forms.ModelForm):
    class Meta:
        model = Income
        fields = ['amount', 'source', 'date', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter amount'}),
            'source': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Source of income'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional notes'}),
        }

        
class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['amount', 'category', 'date', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter amount'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional notes'}),
        }
class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'description', 'status', 'date', 'priority']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter task title'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Describe the task (optional)'
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
            'date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control'
            }),
            'priority': forms.Select(attrs={
                'class': 'form-select'
            }),
        }


class HealthRecordForm(forms.ModelForm):
    class Meta:
        model = HealthRecord
        fields = ['date', 'weight', 'exercise_minutes', 'sleep_hours']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'weight': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Weight in kg'}),
            'exercise_minutes': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Exercise minutes'}),
            'sleep_hours': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Sleep hours'}),
        }


from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm

class UserRegisterForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email',
            'autocomplete': 'email',
        })
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Choose a username',
                'autocomplete': 'username',
            }),
            'password1': forms.PasswordInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter password',
                'autocomplete': 'new-password',
            }),
            'password2': forms.PasswordInput(attrs={
                'class': 'form-control',
                'placeholder': 'Confirm password',
                'autocomplete': 'new-password',
            }),
        }

class ScheduleForm(forms.ModelForm):
    class Meta:
        model = Schedule
        fields = ['title', 'description', 'start_datetime', 'end_datetime', 'location', 'reminder_datetime']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter title'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional description'}),
            'start_datetime': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'end_datetime': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter location'}),
            'reminder_datetime': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
        }


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email']



from django import forms
from .models import Profile

class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['bio', 'location', 'birth_date', 'whatsapp_number']
        labels = {'whatsapp_number': 'WhatsApp number'}
        widgets = {
            'whatsapp_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 0712 345 678',
                'inputmode': 'tel',
            }),
            'bio': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Write something about yourself',
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Your location',
            }),
            'birth_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control',
            }),
        }





from .models import MenstrualCycleRecord

class MenstrualCycleForm(forms.ModelForm):
    class Meta:
        model = MenstrualCycleRecord
        fields = [
            'start_date', 'end_date', 'flow_level',
            'pain_level', 'mood', 'symptoms', 'notes'
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'symptoms': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Cramps, headache, fatigue…'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Anything else worth remembering'}),
        }

# ─── Goal Forms ─────────────────────────────────────────────────────────────

from .models import Goal, GoalMilestone, GoalUpdate

class GoalForm(forms.ModelForm):
    class Meta:
        model = Goal
        fields = ['title', 'description', 'kind', 'category', 'target_value', 'current_value',
                  'unit', 'start_date', 'target_date', 'status']
        widgets = {
            'kind': forms.RadioSelect,
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Save 1,000,000 Tsh'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional description'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'target_value': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 1000000'}),
            'current_value': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Tsh, kg, books'}),
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'target_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A monthly savings goal fills these in itself.
        self.fields['target_date'].required = False
        self.fields['current_value'].required = False

    def clean(self):
        data = super().clean()
        if data.get('kind') == Goal.KIND_MONTHLY_SAVINGS:
            from core.savings import month_end
            if not data.get('target_value'):
                self.add_error('target_value', 'How much do you want to save each month?')
            start = data.get('start_date') or timezone.localdate()
            data['start_date'] = start.replace(day=1)
            data['target_date'] = month_end(timezone.localdate())
            data['current_value'] = self.instance.current_value or 0
            data['category'] = 'finance'
            data['unit'] = data.get('unit') or 'Tsh'
        else:
            if not data.get('target_date'):
                self.add_error('target_date', 'Pick the date you want to reach this goal.')
            if data.get('current_value') is None:
                data['current_value'] = 0
        return data


class GoalUpdateForm(forms.ModelForm):
    class Meta:
        model = GoalUpdate
        fields = ['value', 'note', 'date']
        widgets = {
            'value': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'New progress value'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional note'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


class GoalMilestoneForm(forms.ModelForm):
    class Meta:
        model = GoalMilestone
        fields = ['title', 'target_value']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Milestone title'}),
            'target_value': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Value to reach'}),
        }