# =============================================================================
# APPEND THE BLOCK BELOW TO THE END OF core/models.py
# (do not replace the file — this is an addition)
# =============================================================================


class AgentRun(models.Model):
    """
    A record of every heartbeat.

    The agent works while nobody is watching, so this table is the only way to
    tell the difference between "nothing needed doing" and "it has been dead
    for three days". Check it before trusting the agent with anything bigger.
    """
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


# =============================================================================
# ALSO: add this ONE field to the existing Profile class in core/models.py
# =============================================================================
#
#     whatsapp_number = models.CharField(
#         max_length=20, blank=True, null=True,
#         help_text="WhatsApp number the agent sends to, e.g. 255712345678",
#     )
#
# =============================================================================
# ALSO: add this Meta class inside the existing Schedule class
# =============================================================================
#
#     class Meta:
#         indexes = [
#             models.Index(fields=["reminder_sent", "reminder_datetime"],
#                          name="core_sched_reminder_idx"),
#         ]
#
# The index matters: the tick runs this query every few minutes forever. Without
# it you are doing a full table scan on Schedule for the life of the project.
# =============================================================================
