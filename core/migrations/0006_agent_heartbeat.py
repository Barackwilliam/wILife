from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_dashboardpreference_goal_goalmilestone_goalupdate"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="whatsapp_number",
            field=models.CharField(
                blank=True,
                null=True,
                max_length=20,
                help_text="WhatsApp number the agent sends to, e.g. 255712345678",
            ),
        ),
        migrations.CreateModel(
            name="AgentRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("job", models.CharField(max_length=64)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("ok", models.BooleanField(default=False)),
                ("processed", models.PositiveIntegerField(default=0)),
                ("failed", models.PositiveIntegerField(default=0)),
                ("detail", models.TextField(blank=True, default="")),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.AddIndex(
            model_name="agentrun",
            index=models.Index(fields=["-started_at"], name="core_agentr_started_idx"),
        ),
        migrations.AddIndex(
            model_name="schedule",
            index=models.Index(
                fields=["reminder_sent", "reminder_datetime"],
                name="core_sched_reminder_idx",
            ),
        ),
    ]
