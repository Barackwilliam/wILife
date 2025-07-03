from django.urls import path
from django.contrib.auth import views as auth_views
from .views import CustomPasswordResetView
from django.contrib.auth.views import LogoutView
from .views import logout_view
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('income/', views.income_list, name='income_list'),
    path('income/add/', views.income_create, name='income_create'),
    path('income/<int:pk>/edit/', views.income_update, name='income_update'),
    path('income/<int:pk>/delete/', views.income_delete, name='income_delete'),
   
    path('export/excel/', views.export_excel, name='export_excel'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),
    path('toggle-dark-mode/', views.toggle_dark_mode, name='toggle_dark_mode'),


    path('task/', views.task_list, name='task_list'),
    path('task/add/', views.task_create, name='task_create'),
    path('task/<int:pk>/edit/', views.task_update, name='task_update'),
    path('task/<int:pk>/delete/', views.task_delete, name='task_delete'),

    path('health/', views.health_list, name='health_list'),
    path('health/add/', views.health_create, name='health_create'),
    path('health/<int:pk>/edit/', views.health_update, name='health_update'),
    path('health/<int:pk>/delete/', views.health_delete, name='health_delete'),

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
    path('calendar/', views.calendar_view, name='calendar'),
    path('profile/', views.profile, name='profile'),
    # Ongeza URLs nyingine kama login, logout, dashboard n.k.


    path('password-reset/', CustomPasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='password_reset_confirm.html', success_url='/reset/done/'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='password_reset_complete.html'), name='password_reset_complete'),

]
