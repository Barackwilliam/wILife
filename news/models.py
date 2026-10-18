"""
The public news side of wILife.

Every day the agent drafts six articles (1 Dunia, 2 Afrika, 3 Tanzania) and,
on Mondays, Wednesdays and Saturdays, one more about a JamiiTek service. The
drafts form one NewsBatch; nothing is public until the owner approves the batch
(WhatsApp/Telegram "OK 1234", or the staff review page).
"""

from django.conf import settings
from django.core.files.base import ContentFile
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


def cover_path(instance, filename):
    when = timezone.now()
    return f"habari/{when:%Y/%m}/{filename}"


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
    origin = models.CharField(max_length=12, blank=True, default="", db_index=True,
                              help_text="Ilikotoka: '' (mfumo), 'instagram' / 'facebook' (chapisho lililowekwa na mhariri)")
    batch = models.ForeignKey(NewsBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="articles")
    slot = models.PositiveSmallIntegerField(default=0, help_text="Position within the day's batch")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    cover_image = models.FileField(upload_to=cover_path, blank=True, max_length=300,
                                   help_text="Picha ya kushirikisha (WhatsApp/Facebook/Google). Ukipakia picha yako hapa au "
                                             "kwenye 'Cover art', hii inatengenezwa yenyewe (1200×630).")
    cover_art = models.FileField(upload_to=cover_path, blank=True, max_length=300,
                                 help_text="PAKIA PICHA YA HABARI HAPA — angalau 1800px upana kwa HD. Inachakatwa yenyewe: "
                                           "toleo la simu, la HD na la kushirikisha.")
    # Picha iliyopakiwa na mhariri (news/photos.py): asili + toleo la HD kwa srcset
    cover_original = models.FileField(upload_to=cover_path, blank=True, max_length=300, editable=False)
    cover_hd = models.FileField(upload_to=cover_path, blank=True, null=True, max_length=300, editable=False)
    photo_hd_width = models.PositiveSmallIntegerField(default=0, editable=False)
    photo_quality = models.CharField(max_length=10, blank=True, default="", editable=False)
    photo_size = models.CharField(max_length=20, blank=True, default="", editable=False)
    photo_checked_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    # Database fallback, used only when no storage bucket is configured.
    cover_png = models.BinaryField(null=True, blank=True, editable=False)
    cover_thumb = models.BinaryField(null=True, blank=True, editable=False, help_text="Text-free 1200px WebP for on-site images")
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

    def tag_list(self):
        """Keywords as topic tags (deduplicated, max 6)."""
        seen, tags = set(), []
        for raw in self.keywords.split(","):
            tag = raw.strip()
            key = slugify(tag)
            if tag and key and key not in seen:
                seen.add(key)
                tags.append((tag, key))
        return tags[:6]

    def render_cover(self):
        """Generate both images; store them in the bucket if configured, else in the database."""
        from news import covers
        from news.pipeline import swahili_date
        when = self.published_at or self.created_at or timezone.now()
        day = swahili_date(timezone.localtime(when).date())
        seed = self.pk or 0
        if self.cover_original:   # mhariri amepakia picha yake — usiifunike
            return
        png = covers.render(self.title, self.category, day, seed=seed)
        art = covers.thumbnail(covers.render(self.title, self.category, day, seed=seed, with_title=False))
        self.store_cover(png, art)

    def store_cover(self, png, art):
        if getattr(settings, "USE_SUPABASE_STORAGE", False):
            base = f"{(self.slug or 'habari')[:80]}-{self.pk or 0}"
            self.cover_image.save(f"{base}.png", ContentFile(png), save=False)
            self.cover_art.save(f"{base}.webp", ContentFile(art), save=False)
            self.cover_png = self.cover_thumb = None
        else:
            self.cover_png, self.cover_thumb = png, art

    @property
    def art_url(self):
        """On-site image (text-free WebP)."""
        if self.cover_art:
            return self.cover_art.url
        return reverse("news:cover_thumb", args=[self.pk])

    @property
    def srcset(self):
        """'url 1200w, url-hd 2400w' — tupu kama hakuna toleo la HD."""
        if self.cover_hd and self.photo_hd_width:
            return f"{self.art_url} 1200w, {self.cover_hd.url} {self.photo_hd_width}w"
        return ""

    @property
    def og_url(self):
        """Share/Discover image (PNG with headline). May be relative — use |abs_url in templates."""
        if self.cover_image:
            return self.cover_image.url
        return reverse("news:cover", args=[self.pk])

    def publish(self, when=None):
        self.status = "published"
        self.published_at = when or timezone.now()
        self.save(update_fields=["status", "published_at", "updated_at"])


class EditorSettings(models.Model):
    """
    Mwongozo wa mhariri kwa AI — mmoja tu (singleton). Unahaririwa kwenye
    /habari-admin/agiza/ bila kugusa code.
    """

    focus_topics = models.TextField(
        blank=True, default="Simba, Yanga, Taifa Stars, Azam FC, Bunge, Bajeti, TANESCO, Dar es Salaam",
        help_text="Mada za kipaumbele — habari zinazozigusa zinapewa uzito zaidi (moja kwa mstari au kwa koma)")
    avoid_topics = models.TextField(
        blank=True, default="", help_text="Mada za kuepuka kabisa (moja kwa mstari au kwa koma)")
    editor_notes = models.TextField(
        blank=True, default="",
        help_text="Maelekezo ya ziada kwa AI anapoandika (mf. 'kwenye michezo taja matokeo na wafungaji')")
    sports_slot = models.BooleanField(
        default=True, help_text="Habari moja ya michezo ya Tanzania kila siku (slot 6)")
    ai_model = models.CharField(
        max_length=80, default="gemini:gemini-2.5-flash",
        choices=[("gemini:gemini-2.5-flash", "Gemini 2.5 Flash"), ("gemini:gemini-2.5-pro", "Gemini 2.5 Pro"),
                 ("groq:openai/gpt-oss-120b", "Groq · GPT-OSS 120B"),
                 ("groq:llama-3.3-70b-versatile", "Groq · Llama 3.3 70B")],
        help_text="AI inayoandika habari. Ikishindwa (kikomo/key), mfumo unatumia mtoa huduma mwingine.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Editor settings"
        verbose_name_plural = "Editor settings"

    def __str__(self):
        return "Mwongozo wa mhariri"

    @classmethod
    def load(cls):
        obj = cls.objects.order_by("pk").first()
        return obj or cls.objects.create()


class NewsRequest(models.Model):
    """Habari iliyoagizwa na mhariri: 'Andika habari kuhusu Simba na Yanga leo'."""

    STATUS = [
        ("scheduled", "Imepangwa"),
        ("watching", "Inafuatilia"),
        ("queued", "Inasubiri"),
        ("working", "Inaandikwa"),
        ("drafted", "Rasimu tayari"),
        ("published", "Imechapishwa"),
        ("failed", "Imeshindikana"),
    ]
    SOURCES = [("web", "Dashboard"), ("whatsapp", "WhatsApp"), ("telegram", "Telegram")]

    topic = models.CharField(max_length=300)
    instructions = models.TextField(blank=True, default="")
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.TANZANIA)
    publish_now = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUS, default="queued", db_index=True)
    # Habari iliyopangwa: AI inaanza saa `start_at`; vyombo visipokuwa vimeripoti,
    # inajaribu tena kila RETRY_MINUTES hadi saa `watch_hours` ziishe.
    start_at = models.DateTimeField(null=True, blank=True, help_text="AI ianze saa hii (tukio linapotokea)")
    next_try_at = models.DateTimeField(null=True, blank=True, db_index=True)
    watch_hours = models.PositiveSmallIntegerField(default=6)
    attempts = models.PositiveSmallIntegerField(default=0)
    article = models.ForeignKey(Article, null=True, blank=True, on_delete=models.SET_NULL, related_name="requests")
    approval = models.ForeignKey("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)
    error = models.TextField(blank=True, default="")
    found = models.PositiveSmallIntegerField(default=0, help_text="Vyanzo vilivyopatikana")
    via = models.CharField(max_length=10, choices=SOURCES, default="web")
    requested_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.topic[:60]} ({self.get_status_display()})"
