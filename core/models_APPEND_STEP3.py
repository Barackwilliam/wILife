# =============================================================================
# APPEND TO core/models.py  (Step 3 / 6)
# =============================================================================


class ApprovalRequest(models.Model):
    """
    A drafted message waiting for William to say yes.

    This table is the wall between TIER A and TIER B. A draft sits here until
    it is explicitly approved; approvals.execute_approved() is the only code
    path in the project that transmits to a non-William number.
    """
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
