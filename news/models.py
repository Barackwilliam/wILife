"""
wILife Habari — the public news side of wILife.

Every day the agent drafts six articles (1 Dunia, 2 Afrika, 3 Tanzania) and,
on Mondays, Wednesdays and Saturdays, one more about a JamiiTek service. The
drafts form one NewsBatch; nothing is public until the owner approves the batch
(WhatsApp/Telegram "OK 1234", or the staff review page).
"""

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class Category(models.TextChoices):
    DUNIA = "dunia", "Dunia"
    AFRIKA = "afrika", "Afrika"
    TANZANIA = "tanzania", "Tanzania"
    JAMIITEK = "jamiitek", "JamiiTek"


CATEGORY_BLURBS = {
    "dunia": "Habari kuu za dunia zilizochujwa kwa msomaji wa Kitanzania.",
    "afrika": "Yanayojiri barani Afrika — siasa, uchumi, teknolojia na jamii.",
    "tanzania": "Habari za Tanzania leo: serikali, uchumi, jamii na michezo.",
    "jamiitek": "Huduma za JamiiTek, faida zake na fursa kwa biashara yako.",
}


class JamiiTekService(models.Model):
    """A JamiiTek service. Featured in rotation on JamiiTek days and has its own landing page."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    icon = models.CharField(max_length=40, default="bi-stars", help_text="Bootstrap icon class, e.g. bi-globe2")
    tagline = models.CharField(max_length=200)
    description = models.TextField()
    benefits = models.TextField(help_text="One benefit per line")
    opportunities = models.TextField(blank=True, help_text="Opportunities / who it is for, one per line")
    cta_label = models.CharField(max_length=60, default="Wasiliana nasi")
    cta_url = models.URLField(blank=True, help_text="Empty = WhatsApp link from settings")
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    last_featured_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "JamiiTek service"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("news:service", args=[self.slug])

    def benefit_list(self):
        return [b.strip("-• ").strip() for b in self.benefits.splitlines() if b.strip()]

    def opportunity_list(self):
        return [o.strip("-• ").strip() for o in self.opportunities.splitlines() if o.strip()]


class NewsBatch(models.Model):
    STATUS = [
        ("drafting", "Drafting"),
        ("pending", "Awaiting approval"),
        ("published", "Published"),
        ("rejected", "Rejected"),
    ]
    date = models.DateField(unique=True)
    status = models.CharField(max_length=12, choices=STATUS, default="drafting")
    approval = models.ForeignKey("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        verbose_name_plural = "News batches"

    def __str__(self):
        return f"Habari {self.date:%Y-%m-%d} ({self.get_status_display()})"


class PublishedManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(status="published", published_at__lte=timezone.now())


class Article(models.Model):
    STATUS = [("draft", "Draft"), ("published", "Published"), ("rejected", "Rejected")]

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    category = models.CharField(max_length=12, choices=Category.choices, db_index=True)
    excerpt = models.CharField(max_length=300, help_text="Meta description and card summary (≤160 chars ideal)")
    body = models.TextField(help_text="Paragraphs separated by a blank line; '## ' starts a subheading")
    keywords = models.CharField(max_length=300, blank=True)
    sources = models.JSONField(default=list, blank=True, help_text='[{"title": "", "url": "", "publisher": ""}]')
    source_url = models.URLField(max_length=500, null=True, blank=True, unique=True,
                                 help_text="Main source item — prevents writing the same story twice")
    service = models.ForeignKey(JamiiTekService, null=True, blank=True, on_delete=models.SET_NULL)
    batch = models.ForeignKey(NewsBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="articles")
    slot = models.PositiveSmallIntegerField(default=0, help_text="Position within the day's batch")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    cover_png = models.BinaryField(null=True, blank=True, editable=False)
    views = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = models.Manager()
    published = PublishedManager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [models.Index(fields=["status", "category", "-published_at"])]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or "habari"
            slug, n = base, 2
            while Article.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("news:article", args=[self.category, self.slug])

    @property
    def reading_minutes(self):
        return max(1, round(len(self.body.split()) / 200))

    def publish(self, when=None):
        self.status = "published"
        self.published_at = when or timezone.now()
        self.save(update_fields=["status", "published_at", "updated_at"])
