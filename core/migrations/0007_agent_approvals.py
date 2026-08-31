import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0006_agent_heartbeat"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApprovalRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tool", models.CharField(max_length=64)),
                ("code", models.CharField(db_index=True, max_length=8)),
                ("recipient_name", models.CharField(blank=True, default="", max_length=120)),
                ("recipient_number", models.CharField(blank=True, default="", max_length=32)),
                ("body", models.TextField()),
                ("context", models.CharField(blank=True, default="", max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("sent", "Sent"),
                            ("failed", "Failed"),
                            ("expired", "Expired"),
                        ],
                        default="pending",
                        max_length=12,
                    ),
                ),
                ("result", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="approval_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="approvalrequest",
            index=models.Index(fields=["status", "code"], name="core_appr_status_code_idx"),
        ),
    ]
