from django.urls import path
from django.contrib.auth import views as auth_views
from .views import CustomPasswordResetView
from django.contrib.auth.views import LogoutView
from .views import logout_view
from . import views_whatsapp
from . import views_telegram
from . import views_agent

from . import views

urlpatterns = [
    path('app/', views.home, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('income/', views.income_list, name='income_list'),
    path('income/add/', views.income_create, name='income_create'),
    path('income/<int:pk>/edit/', views.income_update, name='income_update'),
    path('income/<int:pk>/delete/', views.income_delete, name='income_delete'),
   
    path('export/excel/', views.export_excel, name='export_excel'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),
    path('toggle-dark-mode/', views.toggle_dark_mode, name='toggle_dark_mode'),

    path('menstrual/', views.menstrual_list, name='menstrual_list'),
    path('menstrual/add/', views.menstrual_create, name='menstrual_add'),
    path('period', views.menstrual_calendar, name='menstrual_calendar'),

    path('period/update/<int:pk>/', views.menstrual_update, name='menstrual_update'),
    path('period/delete/<int:pk>/', views.menstrual_delete, name='menstrual_delete'),


    path('task/', views.task_list, name='task_list'),
    path('task/add/', views.task_create, name='task_create'),
    path('task/<int:pk>/edit/', views.task_update, name='task_update'),
    path('task/<int:pk>/delete/', views.task_delete, name='task_delete'),
    path('task/<int:pk>/toggle/', views.task_toggle, name='task_toggle'),

    path('health/', views.health_list, name='health_list'),
    path('health/add/', views.health_create, name='health_create'),
    path('health/<int:pk>/edit/', views.health_update, name='health_update'),
    path('health/<int:pk>/delete/', views.health_delete, name='health_delete'),
    path('healthz/', views_agent.healthz, name='healthz'),
    path('agent/tick/', views_agent.agent_tick, name='agent_tick'),

    path('schedule/', views.schedule_list, name='schedule_list'),
    path('schedule/add/', views.schedule_create, name='schedule_create'),
    path('schedule/<int:pk>/edit/', views.schedule_update, name='schedule_update'),
    path('schedule/<int:pk>/delete/', views.schedule_delete, name='schedule_delete'),

    path('expense/', views.expense_list, name='expense_list'),
    path('expense/add/', views.expense_create, name='expense_create'),
    path('expense/<int:pk>/edit/', views.expense_update, name='expense_update'),
    path('expense/<int:pk>/delete/', views.expense_delete, name='expense_delete'),

    path('register/', views.register, name='register'),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', logout_view, name='logout'),

    path('notifications/', views.notifications_list, name='notifications_list'),
    path('notifications/<int:pk>/read/', views.mark_notification_read, name='mark_notification_read'),
    path('notifications/delete/<int:pk>/', views.notification_delete, name='notification_delete'),

    path('calendar/', views.calendar_view, name='calendar'),
    path('profile/', views.profile, name='profile'),
    # Ongeza URLs nyingine kama login, logout, dashboard n.k.


    path('password-reset/', CustomPasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='password_reset_confirm.html', success_url='/reset/done/'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='password_reset_complete.html'), name='password_reset_complete'),


    # Goal URLs
    path('goals/', views.goal_list, name='goal_list'),
    path('goals/add/', views.goal_create, name='goal_create'),
    path('goals/<int:pk>/', views.goal_detail, name='goal_detail'),
    path('goals/<int:pk>/edit/', views.goal_update, name='goal_update'),
    path('goals/<int:pk>/delete/', views.goal_delete, name='goal_delete'),
    path('goals/<int:pk>/update/', views.goal_add_update, name='goal_add_update'),
    path('goals/<int:pk>/milestone/', views.goal_add_milestone, name='goal_add_milestone'),
    path('goals/<int:pk>/milestone/<int:milestone_pk>/toggle/', views.goal_toggle_milestone, name='goal_toggle_milestone'),
    path('agent/whatsapp/', views_whatsapp.whatsapp_webhook, name='whatsapp_webhook'),
    path('agent/whatsapp/baileys/', views_whatsapp.baileys_incoming, name='whatsapp_baileys_incoming'),
    path('agent/whatsapp/qr/', views_whatsapp.whatsapp_qr, name='whatsapp_qr'),
    # Dashboard preferences
    path('dashboard/save-prefs/', views.dashboard_save_prefs, name='dashboard_save_prefs'),
    path('agent/telegram/', views_telegram.telegram_webhook, name='telegram_webhook'),
]