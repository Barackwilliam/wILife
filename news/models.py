"""
The public news side of wILife.

Every day the agent drafts six articles (1 Dunia, 2 Afrika, 3 Tanzania) and,
on Mondays, Wednesdays and Saturdays, one more about a JamiiTek service. The
drafts form one NewsBatch; nothing is public until the owner approves the batch
(WhatsApp/Telegram "OK 1234", or the staff review page).
"""

from datetime import timedelta

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
    # ── LIVE: habari inayosasishwa moja kwa moja (mechi, tukio) — news/live.py ──
    # Google: LiveBlogPosting -> alama ya "LIVE" kwenye Top stories (Google ndiyo inaamua kuionyesha)
    live_started_at = models.DateTimeField(null=True, blank=True,
                                           help_text="Imeanza LIVE lini. Tupu = makala ya kawaida.")
    live_ended_at = models.DateTimeField(null=True, blank=True, help_text="LIVE imeisha lini")
    live_score = models.CharField(max_length=80, blank=True, help_text="Mf. Simba 1 - 0 Yanga")
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
    def is_live(self):
        return bool(self.live_started_at and not self.live_ended_at)

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
        match = getattr(self, "match", None) if self.pk else None
        if match and match.fixture_id:   # mechi ya API-Football: nembo halisi za timu, si sanaa ya mfumo
            from news.football import match_cover
            self.store_cover(*match_cover(match))
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
        return reverse("news:cover_thumb", args=[self.pk]) + self._cover_version

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
        return reverse("news:cover", args=[self.pk]) + self._cover_version

    @property
    def _cover_version(self):
        """Picha za mechi zinatengenezwa upya (kuanza, matokeo) kwenye anwani ile ile — ?v= inavunja cache."""
        return f"?v={int(self.updated_at.timestamp())}" if self.origin == "football" and self.updated_at else ""

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
    # Hali ya AI (inajazwa na core.llm) — onyo kwenye dashboard AI kuu ikishindwa
    ai_fail_at = models.DateTimeField(null=True, blank=True, editable=False)
    ai_fail_reason = models.CharField(max_length=300, blank=True, default="", editable=False)
    ai_fail_used = models.CharField(max_length=20, blank=True, default="", editable=False,
                                    help_text="Mtoa huduma aliyeandika badala yake ('' = zote zilishindwa)")
    ai_fail_day = models.DateField(null=True, blank=True, editable=False)
    ai_fail_count = models.PositiveIntegerField(default=0, editable=False)
    ai_ok_at = models.DateTimeField(null=True, blank=True, editable=False)
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


class LiveUpdate(models.Model):
    """Sasisho moja la habari ya LIVE (goli, kadi, tamko…). Mpya juu."""
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="live_updates")
    label = models.CharField(max_length=20, blank=True, help_text="Mf. 45' au 14:30")
    kind = models.CharField(max_length=10, blank=True, default="",
                            help_text="goal / card / red / sub / var / whistle — ikoni kwenye ukurasa")
    text = models.TextField(max_length=1000)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.label} {self.text[:40]}".strip()


class LiveMatch(models.Model):
    """
    Mechi iliyo nyuma ya habari ya LIVE: timu, magoli, dakika na hali. Inajazwa na mhariri
    (live anza Simba vs Yanga) au yenyewe kutoka API-Football (news/football.py).
    Ukurasa unaitumia kuonyesha ubao wa matokeo, na Google inapewa `SportsEvent`.
    """
    STATUS = [("NS", "Bado haijaanza"), ("1H", "Kipindi cha kwanza"), ("HT", "Mapumziko"), ("2H", "Kipindi cha pili"),
              ("ET", "Muda wa nyongeza"), ("BT", "Mapumziko (muda wa nyongeza)"), ("P", "Mikwaju ya penalti"),
              ("FT", "Imekamilika"), ("AET", "Imekamilika (muda wa nyongeza)"), ("PEN", "Imekamilika (penalti)"),
              ("PST", "Imeahirishwa"),
              ("CANC", "Imefutwa"), ("ABD", "Imesimamishwa"), ("SUSP", "Imesimamishwa"), ("INT", "Imesimama"),
              ("LIVE", "Inaendelea")]
    PLAYING = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
    FINISHED = {"FT", "AET", "PEN"}
    OFF = {"PST", "CANC", "ABD", "AWD", "WO"}

    article = models.OneToOneField(Article, on_delete=models.CASCADE, related_name="match")
    fixture_id = models.BigIntegerField(null=True, blank=True, unique=True,
                                        help_text="Namba ya mechi kwa mtoaji wa data (tupu = mechi ya mhariri)")
    provider = models.CharField(max_length=12, blank=True, default="api-football",
                                help_text="Mtoaji wa data wa mechi hii: api-football / highlightly")
    competition = models.CharField(max_length=80, blank=True)
    competition_logo = models.URLField(max_length=300, blank=True)
    round = models.CharField(max_length=80, blank=True)
    home = models.CharField(max_length=80)
    away = models.CharField(max_length=80)
    home_logo = models.URLField(max_length=300, blank=True)
    away_logo = models.URLField(max_length=300, blank=True)
    home_goals = models.PositiveSmallIntegerField(null=True, blank=True)
    away_goals = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=5, default="NS", choices=STATUS)
    minute = models.PositiveSmallIntegerField(null=True, blank=True)
    kickoff = models.DateTimeField(null=True, blank=True, db_index=True)
    venue = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=80, blank=True)
    seen_events = models.JSONField(default=list, blank=True, editable=False)
    extra = models.JSONField(default=dict, blank=True,
                             help_text="Takwimu, vikosi, uso kwa uso, fomu, video, mwamuzi, hali ya hewa (news/enrich.py)")
    cover_ok = models.BooleanField(default=False, editable=False,
                                   help_text="Picha yenye nembo za timu imetengenezwa")
    synced_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["kickoff", "id"]

    def __str__(self):
        return f"{self.home} vs {self.away}"

    @property
    def has_score(self):
        return self.home_goals is not None and self.away_goals is not None

    STALE_AFTER = timedelta(hours=4)

    @staticmethod
    def stale(obj):
        """
        Mechi ya API iliyopaswa kuwa imeisha lakini hatuna matokeo ya mwisho (maombi ya siku yaliisha,
        API ilichelewa): isionyeshwe kama inaendelea ("73' LIVE") milele.
        """
        return bool(getattr(obj, "fixture_id", None) and obj.kickoff
                    and obj.status not in LiveMatch.FINISHED | LiveMatch.OFF
                    and timezone.now() - obj.kickoff > LiveMatch.STALE_AFTER)

    @staticmethod
    def not_started(obj):
        """Hali ya "inaendelea" kwa mechi iliyopangwa kuanza zaidi ya dakika 15 baadaye ni data iliyokosewa."""
        return bool(obj.kickoff and obj.kickoff > timezone.now() + timedelta(minutes=15))

    @staticmethod
    def overdue(obj):
        """
        Mechi ya API ambayo muda wake wa kuanza umepita lakini bado tunayo hali ya "haijaanza" (NS): hatuna
        data mpya (mechi zisizo na LIVE zinasasishwa baadaye). Isionyeshwe "14:30" kana kwamba bado haijaanza.
        """
        return bool(getattr(obj, "fixture_id", None) and obj.kickoff and obj.status == "NS"
                    and timezone.now() - obj.kickoff > timedelta(minutes=5) and not LiveMatch.stale(obj))

    @property
    def is_stale(self):
        return LiveMatch.stale(self)

    @property
    def is_overdue(self):
        return LiveMatch.overdue(self)

    @property
    def is_playing(self):
        return (self.status in self.PLAYING and not self.is_stale
                and not (self.kickoff and LiveMatch.not_started(self)))

    @property
    def is_finished(self):
        return self.status in self.FINISHED

    @property
    def clock(self):
        """Kinachoonyeshwa juu ya ubao: 67', HT, FT, au saa ya kuanza."""
        if LiveMatch.stale(self):
            return "Inasubiri matokeo"
        if self.status in LiveMatch.PLAYING and self.kickoff and LiveMatch.not_started(self):
            return timezone.localtime(self.kickoff).strftime("%H:%M")   # data ya API iliyokosewa: bado haijaanza
        if self.status in ("1H", "2H", "ET", "LIVE") and self.minute:
            return f"{self.minute}'"
        if self.status == "NS" and self.kickoff:
            hhmm = timezone.localtime(self.kickoff).strftime("%H:%M")
            return f"Ilipangwa {hhmm}" if LiveMatch.overdue(self) else hhmm
        return self.status if self.status in ("HT", "FT", "AET", "PEN") else self.get_status_display()

    @property
    def score_text(self):
        if not self.has_score:
            return f"{self.home} vs {self.away}"
        return f"{self.home} {self.home_goals} - {self.away_goals} {self.away}"

    @property
    def analysis(self):
        from news.enrich import analysis
        return analysis(self)

    @property
    def insights(self):
        from news.enrich import insight_items
        return insight_items(self, with_prediction=False)

    @property
    def x(self):
        """Taarifa za ziada (extra) kwa template: takwimu, vikosi, H2H, fomu, video."""
        return self.extra or {}

    def as_json(self):
        return {"id": self.article_id, "stats": (self.extra or {}).get("stats") or [], "home": self.home, "away": self.away,
                "hg": self.home_goals, "ag": self.away_goals, "status": self.status,
                "clock": self.clock, "playing": self.is_playing, "finished": self.is_finished}


class FootballState(models.Model):
    """Matumizi ya API-Football kwa siku (UTC — kikomo kinaanza upya saa 00:00 UTC). news/football.py"""
    day = models.DateField(unique=True)
    requests = models.PositiveIntegerField(default=0)
    remaining = models.IntegerField(null=True, blank=True, help_text="Kwa mujibu wa API yenyewe")
    schedule_at = models.DateTimeField(null=True, blank=True)
    live_at = models.DateTimeField(null=True, blank=True)
    live_mode = models.CharField(max_length=5, blank=True,
                                 help_text="Njia ya kuuliza mechi zinazochezwa ambayo mpango unaruhusu: ids / live / id")
    error = models.CharField(max_length=300, blank=True)
    sample = models.TextField(blank=True, help_text="Mfano wa jibu halisi la API (mechi moja) — kuhakiki majina ya sehemu")

    def __str__(self):
        return f"{self.day}: {self.requests}"


class Fixture(models.Model):
    """
    Mechi moja ya ratiba ya siku (ligi zetu zote, si zile 5 zenye LIVE tu) — kurasa za
    "Mechi za leo" na "Matokeo ya jana" (news/fixtures.py). Inajazwa na usomaji wa ratiba
    ambao tayari unafanyika (hakuna ombi la ziada), na matokeo yanasasishwa jioni.
    """
    provider = models.CharField(max_length=12, default="highlightly")
    fixture_id = models.BigIntegerField()
    day = models.DateField(db_index=True, help_text="Siku kwa saa za Afrika Mashariki")
    kickoff = models.DateTimeField(null=True, blank=True)
    rank = models.PositiveSmallIntegerField(default=99, help_text="Nafasi ya ligi kwenye HIGHLIGHTLY_LEAGUES")
    competition = models.CharField(max_length=80, blank=True)
    competition_id = models.BigIntegerField(null=True, blank=True)
    competition_logo = models.URLField(max_length=300, blank=True)
    country = models.CharField(max_length=60, blank=True)
    round = models.CharField(max_length=80, blank=True)
    home = models.CharField(max_length=80)
    away = models.CharField(max_length=80)
    home_logo = models.URLField(max_length=300, blank=True)
    away_logo = models.URLField(max_length=300, blank=True)
    home_goals = models.PositiveSmallIntegerField(null=True, blank=True)
    away_goals = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=5, default="NS")
    minute = models.PositiveSmallIntegerField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True,
                             help_text="Maelezo ya mechi yaliyoombwa msomaji alipofungua ukurasa wake: matukio, takwimu, uwanja")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank", "kickoff", "id"]
        constraints = [models.UniqueConstraint(fields=["provider", "fixture_id"], name="uniq_fixture")]

    def __str__(self):
        return f"{self.day}: {self.home} vs {self.away}"

    @property
    def has_score(self):
        return self.home_goals is not None and self.away_goals is not None

    @property
    def is_finished(self):
        return self.status in LiveMatch.FINISHED

    @property
    def is_playing(self):
        return (self.status in LiveMatch.PLAYING and not LiveMatch.stale(self)
                and not (self.kickoff and LiveMatch.not_started(self)))

    @property
    def clock(self):
        return LiveMatch.clock.fget(self)

    @property
    def is_overdue(self):
        return LiveMatch.overdue(self)

    def get_status_display(self):
        return dict(LiveMatch.STATUS).get(self.status, self.status)

    @property
    def x(self):
        return self.extra or {}

    def get_absolute_url(self):
        slug = slugify(f"{self.home} vs {self.away}")[:80] or "mechi"
        return reverse("news:fixture", args=[self.day.isoformat(), self.pk, slug])


class Subscriber(models.Model):
    """Msomaji aliyejiunga kupata matokeo na mechi za leo WhatsApp (alituma JIUNGE; ACHA kusitisha)."""
    phone = models.CharField(max_length=20, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    last_slot = models.CharField(max_length=16, blank=True, help_text="Ujumbe wa mwisho uliotumwa (siku-saa)")
    sent = models.PositiveIntegerField(default=0)
    failures = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.phone}{'' if self.active else ' (ameacha)'}"


class EmailSubscriber(models.Model):
    """Msomaji wa ujumbe wa michezo kwa email: anajiunga kwenye tovuti, anathibitisha kwa link (double opt-in)."""
    email = models.EmailField(unique=True)
    token = models.CharField(max_length=40, unique=True, editable=False)
    confirmed = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirm_sent_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    last_slot = models.CharField(max_length=16, blank=True)
    sent = models.PositiveIntegerField(default=0)
    failures = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        state = "" if self.active and self.confirmed else (" (bado kuthibitisha)" if self.active else " (ameacha)")
        return f"{self.email}{state}"

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets
            self.token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)


class DailyCount(models.Model):
    """Kihesabu cha siku (UTC): email zilizotumwa (kikomo cha Resend), posti za WhatsApp Channel, n.k."""
    day = models.DateField()
    key = models.CharField(max_length=60)
    n = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "key"], name="uniq_daily_count")]

    def __str__(self):
        return f"{self.day} {self.key}: {self.n}"

    @classmethod
    def get(cls, key, day=None):
        from datetime import timezone as dt_tz
        day = day or timezone.now().astimezone(dt_tz.utc).date()
        return cls.objects.filter(day=day, key=key).values_list("n", flat=True).first() or 0

    @classmethod
    def bump(cls, key, by=1, day=None):
        from datetime import timezone as dt_tz
        day = day or timezone.now().astimezone(dt_tz.utc).date()
        obj, _ = cls.objects.get_or_create(day=day, key=key)
        cls.objects.filter(pk=obj.pk).update(n=models.F("n") + by)
        return obj.n + by


class Highlight(models.Model):
    """
    Video za magoli / highlights za mechi moja kutoka ScoreBat (news/scorebat.py). Video zenyewe zinabaki
    kwa mtoaji (iframe ya scorebat.com) — tunahifadhi kichwa, picha, tarehe na viungo vya kuzicheza tu.
    """
    provider = models.CharField(max_length=12, default="scorebat")
    key = models.CharField(max_length=200, unique=True, help_text="Kitambulisho cha mtoaji (matchviewUrl)")
    slug = models.SlugField(max_length=160, unique=True)
    title = models.CharField(max_length=200)
    competition = models.CharField(max_length=120, blank=True)
    home = models.CharField(max_length=80, blank=True)
    away = models.CharField(max_length=80, blank=True)
    home_key = models.CharField(max_length=80, blank=True, db_index=True)
    away_key = models.CharField(max_length=80, blank=True, db_index=True)
    date = models.DateTimeField(db_index=True)
    thumbnail = models.URLField(max_length=500, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    videos = models.JSONField(default=list, help_text="[{title, src}] — src ni iframe ya scorebat.com au youtube-nocookie.com tu")
    channel = models.CharField(max_length=120, blank=True, help_text="Chaneli rasmi ya YouTube (kwa video za YouTube)")
    team = models.ForeignKey("Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="videos",
                             help_text="Timu ya chaneli rasmi (video zake zinaonekana kwenye ukurasa wa timu)")
    duration = models.PositiveIntegerField(null=True, blank=True, help_text="Sekunde")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("news:video", args=[self.slug])

    @property
    def main(self):
        """Video ya kwanza (kwa kawaida highlights kamili)."""
        return self.videos[0] if self.videos else None

    @property
    def name(self):
        return f"{self.home} vs {self.away}" if self.home and self.away else self.title

    @property
    def source_label(self):
        return f"YouTube — {self.channel}" if self.provider == "youtube" else "ScoreBat"

    @property
    def duration_iso(self):
        if not self.duration:
            return ""
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"PT{h}H{m}M{s}S" if h else f"PT{m}M{s}S"

    @property
    def keywords(self):
        """Maneno ambayo watu wanatafuta: Kiswahili na Kiingereza."""
        n = self.name
        return [f"{n} highlights", f"{n} video za magoli", f"magoli ya {n}", f"{self.home} highlights",
                f"{self.away} highlights", self.league or "", "video za magoli", "highlights za leo"]

    @property
    def league(self):
        """'ENGLAND: Premier League' -> 'Premier League · England' (kwa kuonyesha)."""
        country, sep, name = (self.competition or "").partition(": ")
        return f"{name} · {country.title()}" if sep and name else self.competition


class Team(models.Model):
    """
    Timu yenye ukurasa wake (/timu/<slug>/): habari, mechi ijayo/LIVE, matokeo, fomu na video — vyote
    vinajazwa vyenyewe (news/teams.py). Mhariri anaandika utambulisho (intro) mara moja tu; ukiwa tupu haonyeshwi.
    """
    name = models.CharField(max_length=80, help_text="Jina la kuonyesha, mf. Simba SC")
    slug = models.SlugField(max_length=80, unique=True)
    match_names = models.CharField(
        max_length=300, help_text="Majina ya timu kwenye data ya mechi, kwa koma (mf. Simba, Simba SC). SC/FC hazijalishi.")
    news_terms = models.CharField(
        max_length=400, blank=True,
        help_text="Maneno ya kutambua habari zake, kwa koma (mf. Simba SC, klabu ya Simba). Epuka neno lenye maana "
                  "nyingine (simba = mnyama).")
    intro = models.TextField(blank=True, help_text="Utambulisho mfupi wa timu (mhariri). Tupu = hauonyeshwi.")
    logo = models.URLField(max_length=300, blank=True, help_text="Tupu = nembo kutoka data ya mechi")
    active = models.BooleanField(default=True)
    sort = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("news:team", args=[self.slug])

    @property
    def keys(self):
        from news.highlightly import _team_key
        return {_team_key(n) for n in self.match_names.split(",") if n.strip()}

    @property
    def terms(self):
        return [t.strip() for t in self.news_terms.split(",") if t.strip()]


class VideoChannel(models.Model):
    """
    Chaneli RASMI ya YouTube (klabu, ligi, shirikisho) ambayo video zake zinaingia wILife (news/youtube.py).
    Kwenye Admin unabandika kiungo (https://www.youtube.com/@jina au /channel/UC...) — mfumo unathibitisha
    chaneli kupitia YouTube Data API na kujaza jina na namba yake. Chaneli zisizo rasmi zisiongezwe.
    """
    link = models.CharField(max_length=200, help_text="Kiungo cha chaneli, mf. https://www.youtube.com/@jina")
    channel_id = models.CharField(max_length=40, blank=True, unique=True, null=True, editable=False)
    name = models.CharField(max_length=120, blank=True, editable=False)
    team = models.ForeignKey("Team", null=True, blank=True, on_delete=models.SET_NULL,
                             help_text="Kama ni chaneli ya timu moja (mf. Simba SC)")
    highlights_only = models.BooleanField(
        default=True, help_text="Chukua video zenye 'highlights', 'magoli', 'mabao', 'goals' n.k. kwenye kichwa tu")
    active = models.BooleanField(default=True)
    checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    error = models.CharField(max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or self.link

    @property
    def uploads(self):
        """Orodha ya video zote za chaneli: UC... -> UU..."""
        return "UU" + self.channel_id[2:] if self.channel_id and self.channel_id.startswith("UC") else ""


class ChannelPost(models.Model):
    """Posti moja kwenye WhatsApp Channel ya Soka360 (news/soka360.py): highlight rasmi au video ya matokeo."""
    KINDS = [("highlight", "Highlights (YouTube rasmi)"), ("result", "Video ya matokeo (yetu)")]
    key = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    title = models.CharField(max_length=200, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    tries = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"


class League(models.Model):
    """
    Ligi/mashindano yenye ukurasa wake (/ligi/<slug>/ — news/leagues.py): msimamo, ratiba, matokeo, habari
    na video. Zinaundwa zenyewe kutoka ratiba (Fixture); jina na utangulizi vinaweza kuhaririwa kwenye Admin.
    """
    competition_id = models.BigIntegerField(unique=True, help_text="Namba ya ligi kwa mtoaji data (Highlightly)")
    competition = models.CharField(max_length=80, help_text="Jina kama API inavyoliandika")
    country = models.CharField(max_length=60, blank=True)
    name = models.CharField(max_length=120, help_text="Jina la kuonyesha, mf. 'Ligi Kuu Tanzania (NBC Premier League)'")
    slug = models.SlugField(max_length=120, unique=True)
    logo = models.URLField(max_length=300, blank=True)
    season = models.PositiveIntegerField(null=True, blank=True)
    rank = models.PositiveSmallIntegerField(default=99, help_text="Kipaumbele (HIGHLIGHTLY_LEAGUES)")
    intro = models.TextField(blank=True, help_text="Utangulizi wa ukurasa — ukweli tu, kwa Kiswahili")
    standings = models.JSONField(default=list, blank=True, editable=False,
                                 help_text="[{group, rows: [{pos, team, logo, p, w, d, l, gf, ga, pts}]}]")
    standings_at = models.DateTimeField(null=True, blank=True, editable=False)
    standings_note = models.TextField(blank=True, editable=False, help_text="Kosa au mfano wa jibu la API")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["rank", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("news:league", args=[self.slug])
