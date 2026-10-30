from django import forms
from django.contrib import admin
from django.utils.html import format_html

from news.models import (Article, Author, ChannelPost, League, EditorSettings, Fixture, FootballState, Highlight, JamiiTekService, Team, VideoChannel, LiveMatch, LiveUpdate,
                         NewsBatch, NewsRequest, Subscriber, EmailSubscriber, DailyCount, HeroPhoto, MatchResult, ModelReport)


class LiveUpdateInline(admin.TabularInline):
    """Masasisho ya habari ya LIVE — hapa mhariri anarekebisha au kufuta lililokosewa."""
    model = LiveUpdate
    extra = 0
    fields = ("label", "kind", "text", "created_at")


class LiveMatchInline(admin.StackedInline):
    """Ubao wa matokeo wa mechi (timu, magoli, hali) — unajazwa wenyewe kwa mechi za API-Football."""
    model = LiveMatch
    extra = 0
    fields = (("home", "home_goals", "away_goals", "away"), ("status", "minute", "kickoff"),
              ("competition", "round"), ("venue", "city"), "fixture_id")


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "status", "published_at", "views", "photo_badge", "view_link")
    list_filter = ("status", "category")
    search_fields = ("title", "excerpt", "keywords")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("photo_info", "views", "created_at", "updated_at", "batch", "slot")
    actions = ["publish_selected", "reprocess_photos"]
    inlines = [LiveMatchInline, LiveUpdateInline]

    BADGE = {"hd": ("#15803d", "HD"), "sd": ("#2563eb", "Kawaida"), "low": ("#b45309", "Ndogo"),
             "generated": ("#6b7280", "Ya mfumo"), "error": ("#b91c1c", "Imeshindikana")}

    @admin.display(description="Picha")
    def photo_badge(self, obj):
        color, label = self.BADGE.get(obj.photo_quality, ("#9ca3af", "Haijakaguliwa"))
        return format_html('<span style="color:#fff;background:{};padding:2px 8px;border-radius:9px;'
                           'font-size:11px;font-weight:600">{}</span>', color, label)

    @admin.display(description="Ubora wa picha")
    def photo_info(self, obj):
        if not obj.pk:
            return "Pakia picha kwenye 'Cover art' — itachakatwa ukihifadhi."
        from news.photos import HD_MIN
        color, label = self.BADGE.get(obj.photo_quality, ("#9ca3af", "Haijakaguliwa"))
        note = {
            "hd": f"Picha asili {obj.photo_size} — toleo la HD ({obj.photo_hd_width}px) linatumika kwenye skrini kubwa.",
            "sd": f"Picha asili {obj.photo_size} — nzuri kwa simu, lakini si HD. Pakia ya angalau {HD_MIN}px upana kwa HD.",
            "low": f"Picha asili {obj.photo_size} ni ndogo — imekuzwa na kunolewa, lakini haiwezi kuwa HD. "
                   f"Pakia picha ya angalau {HD_MIN}px upana.",
            "generated": "Picha ya mfumo — pakia picha yako kwenye 'Cover art'.",
            "error": "Picha haikuweza kusomwa — pakia upya (JPG/PNG/WebP).",
        }.get(obj.photo_quality, "Itakaguliwa hivi karibuni.")
        preview = (format_html('<br><img src="{}" style="max-width:360px;border-radius:8px;margin-top:8px">',
                               obj.art_url) if obj.cover_art else "")
        return format_html('<span style="color:#fff;background:{};padding:2px 8px;border-radius:9px;'
                           'font-weight:600">{}</span> {}{}', color, label, note, preview)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        changed = [f for f in ("cover_art", "cover_image") if f in form.changed_data and getattr(obj, f)]
        if changed:
            from news import photos
            try:
                quality = photos.process(obj, field=changed[0])
                obj.save(update_fields=photos.FIELDS)
                if quality == "low":
                    self.message_user(request, f"Picha ni ndogo ({obj.photo_size}) — haitaonekana HD. "
                                               f"Pakia ya angalau {photos.HD_MIN}px upana.", level="warning")
                else:
                    self.message_user(request, f"Picha imechakatwa ({photos.QUALITY_LABELS[quality]}, {obj.photo_size}).")
            except Exception as exc:
                self.message_user(request, f"Picha haikuchakatwa: {exc}", level="error")

    @admin.action(description="Chakata picha upya (HD)")
    def reprocess_photos(self, request, queryset):
        from news import photos
        done = 0
        for a in queryset:
            try:
                photos.process(a)
                a.save(update_fields=photos.FIELDS)
                done += 1
            except Exception as exc:
                self.message_user(request, f"{a.title[:40]}: {exc}", level="error")
        self.message_user(request, f"Picha {done} zimechakatwa.")

    @admin.display(description="Open")
    def view_link(self, obj):
        return format_html('<a href="{}" target="_blank">↗</a>', obj.get_absolute_url())

    @admin.action(description="Publish selected")
    def publish_selected(self, request, queryset):
        from news import indexnow
        published = []
        for a in queryset.exclude(status="published"):
            a.publish()
            published.append(a.get_absolute_url())
        indexnow.submit(published)


class ArticleInline(admin.TabularInline):
    model = Article
    fields = ("slot", "category", "title", "status")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True


@admin.register(NewsBatch)
class NewsBatchAdmin(admin.ModelAdmin):
    list_display = ("date", "status", "approval")
    inlines = [ArticleInline]


@admin.register(JamiiTekService)
class JamiiTekServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "order", "active", "last_featured_at")
    list_editable = ("order", "active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(NewsRequest)
class NewsRequestAdmin(admin.ModelAdmin):
    list_display = ("topic", "category", "status", "via", "found", "created_at")
    list_filter = ("status", "via", "category")
    search_fields = ("topic",)
    raw_id_fields = ("article", "approval")


@admin.register(EditorSettings)
class EditorSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "ai_model", "sports_slot", "updated_at")
    list_editable = ("ai_model",)


@admin.register(FootballState)
class FootballStateAdmin(admin.ModelAdmin):
    """Maombi ya API-Football kwa siku — kikomo cha bure ni 100."""
    list_display = ("day", "requests", "remaining", "schedule_at", "live_at", "live_mode", "error")
    readonly_fields = ("day", "requests", "remaining", "schedule_at", "live_at", "live_mode", "error", "sample")


def _send_now(model_admin, request, queryset, kind):
    """Kitufe cha kujaribu channel: inatuma papo hapo na kuonyesha matokeo au kosa halisi."""
    from django.contrib import messages
    from news import soka360
    items = list(queryset[:4])
    if len(items) > 3:
        model_admin.message_user(request, "Chagua hadi 3 kwa mara moja — channel isijae.", messages.WARNING)
        return
    for obj in items:
        ok, text = soka360.send_now(obj, kind)
        model_admin.message_user(request, text, messages.SUCCESS if ok else messages.ERROR)


class FixtureOddsForm(forms.ModelForm):
    """Odds za kampuni ya hapa (mf. SportPesa, Betway) zinazowekwa na mhariri — zinatangulia odds za API."""
    odds_book = forms.CharField(label="Kampuni ya kubeti", max_length=40, required=False,
                                help_text="Mf. SportPesa. Weka odds za decimal kama zinavyoonekana kwenye kampuni hiyo.")
    odds_home = forms.DecimalField(label="1 (wenyeji)", required=False, min_value=1.01, max_value=100, decimal_places=2)
    odds_draw = forms.DecimalField(label="X (sare)", required=False, min_value=1.01, max_value=100, decimal_places=2)
    odds_away = forms.DecimalField(label="2 (wageni)", required=False, min_value=1.01, max_value=100, decimal_places=2)
    odds_over = forms.DecimalField(label="Magoli 3+ (Over 2.5)", required=False, min_value=1.01, max_value=100,
                                   decimal_places=2)
    odds_under = forms.DecimalField(label="Chini ya 3 (Under 2.5)", required=False, min_value=1.01, max_value=100,
                                    decimal_places=2)
    odds_yes = forms.DecimalField(label="Timu zote kufunga: Ndiyo", required=False, min_value=1.01, max_value=100,
                                  decimal_places=2)
    odds_no = forms.DecimalField(label="Timu zote kufunga: Hapana", required=False, min_value=1.01, max_value=100,
                                 decimal_places=2)
    odds_clear = forms.BooleanField(label="Ondoa odds za mhariri", required=False)

    PAIRS = (("odds_home", "odds_draw", "odds_away"), ("odds_over", "odds_under"), ("odds_yes", "odds_no"))

    class Meta:
        model = Fixture
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        m = ((self.instance.extra or {}).get("odds_manual") if self.instance.pk else None) or {}
        self.fields["odds_book"].initial = m.get("source", "")
        for kind, names in zip(("1x2", "ou25", "btts"), self.PAIRS):
            for name, v in zip(names, (m.get(kind) or {}).get("avg") or []):
                self.fields[name].initial = v

    def clean(self):
        data = super().clean()
        if data.get("odds_clear"):
            return data
        filled = False
        for names in self.PAIRS:
            got = [data.get(n) for n in names]
            if any(got) and not all(got):
                raise forms.ValidationError("Jaza odds zote za soko moja (1, X na 2 pamoja; Over na Under pamoja).")
            filled = filled or all(got)
        if filled and not (data.get("odds_book") or "").strip():
            raise forms.ValidationError("Andika jina la kampuni ya kubeti ya odds hizi.")
        return data


@admin.register(Fixture)
class FixtureAdmin(admin.ModelAdmin):
    """Ratiba ya siku (kurasa za Mechi za leo / Matokeo ya jana). Odds za kampuni za hapa zinawekwa hapa."""
    form = FixtureOddsForm
    fieldsets = (
        (None, {"fields": ("provider", "fixture_id", "day", "kickoff", "rank", "competition", "competition_id",
                           "competition_logo", "country", "round", "home", "away", "home_logo", "away_logo",
                           "home_goals", "away_goals", "status", "minute", "extra")}),
        ("Odds za kampuni ya kubeti (hiari)", {
            "description": "Odds za kampuni wanazotumia wasomaji (mf. SportPesa). Utabiri unalinganishwa nazo na "
                           "chaguo la thamani linaonyeshwa. Weka kabla mechi haijaanza.",
            "fields": ("odds_book", ("odds_home", "odds_draw", "odds_away"), ("odds_over", "odds_under"),
                       ("odds_yes", "odds_no"), "odds_clear")}),
    )
    list_display = ("day", "kickoff", "competition", "home", "away", "home_goals", "away_goals", "status", "has_odds")
    list_filter = ("day", "competition")
    search_fields = ("home", "away", "competition")
    actions = ["send_to_soka360"]

    @admin.action(description="Tuma video ya matokeo kwenye Soka360 sasa (mechi 1–3)")
    def send_to_soka360(self, request, queryset):
        _send_now(self, request, queryset, "result")

    @admin.display(description="Odds", boolean=True)
    def has_odds(self, obj):
        return bool((obj.extra or {}).get("odds_manual") or (obj.extra or {}).get("odds"))

    def save_model(self, request, obj, form, change):
        from news import odds
        d = form.cleaned_data
        extra = dict(obj.extra or {})
        if d.get("odds_clear"):
            extra.pop("odds_manual", None)
        else:
            m = odds.manual((d.get("odds_book") or "").strip(), d.get("odds_home"), d.get("odds_draw"),
                            d.get("odds_away"), d.get("odds_over"), d.get("odds_under"), d.get("odds_yes"),
                            d.get("odds_no"))
            old = extra.get("odds_manual") or {}
            if m and any(m.get(k) != old.get(k) for k in ("1x2", "ou25", "btts", "source")):
                extra["odds_manual"] = m
        obj.extra = extra
        super().save_model(request, obj, form, change)
        live = LiveMatch.objects.filter(fixture_id=obj.fixture_id).first()   # LIVE ya mechi hiyo ionyeshe zile zile
        if live:
            lx = dict(live.extra or {})
            if "odds_manual" in extra:
                lx["odds_manual"] = extra["odds_manual"]
            else:
                lx.pop("odds_manual", None)
            LiveMatch.objects.filter(pk=live.pk).update(extra=lx)


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    """Wasomaji waliotuma JIUNGE kupata michezo WhatsApp."""
    list_display = ("phone", "active", "created_at", "sent", "last_slot", "failures")
    list_filter = ("active",)
    search_fields = ("phone",)


@admin.register(EmailSubscriber)
class EmailSubscriberAdmin(admin.ModelAdmin):
    """Wasomaji wa michezo kwa email (wamethibitisha kwa link)."""
    list_display = ("email", "confirmed", "active", "created_at", "sent", "last_slot", "failures")
    list_filter = ("confirmed", "active")
    search_fields = ("email",)
    readonly_fields = ("token",)


@admin.register(DailyCount)
class DailyCountAdmin(admin.ModelAdmin):
    list_display = ("day", "key", "n")
    list_filter = ("key",)


@admin.register(Highlight)
class HighlightAdmin(admin.ModelAdmin):
    """Video za magoli (ScoreBat). Video isiyofaa inafutwa hapa — ukurasa wake unatoweka."""
    list_display = ("title", "competition", "date", "created_at")
    list_filter = ("competition",)
    search_fields = ("title", "home", "away")
    readonly_fields = ("key", "videos", "created_at", "updated_at")
    actions = ["send_to_soka360"]

    @admin.action(description="Tuma kwenye Soka360 sasa (video 1–3)")
    def send_to_soka360(self, request, queryset):
        _send_now(self, request, queryset, "highlight")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    """
    Kurasa za timu (/timu/<slug>/) na nembo za klabu. Utambulisho (intro) unaandikwa hapa; data nyingine ni automatic.
    Klabu ambayo data ya mechi haina nembo (mf. Geita Gold): iongeze hapa ikiwa na URL ya nembo, nchi, na
    "Active" ikiwa imezimwa kama haihitaji ukurasa — nembo itaonekana kwenye kadi zote za mechi zake.
    """
    list_display = ("crest", "name", "country", "slug", "active", "sort")
    list_display_links = ("name",)
    list_editable = ("active", "sort")
    search_fields = ("name", "match_names")
    list_filter = ("active", "country")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Nembo")
    def crest(self, obj):
        if not obj.logo:
            return "—"
        return format_html('<img src="{}" alt="" width="28" height="28" style="object-fit:contain" referrerpolicy="no-referrer">', obj.logo)


class VideoChannelForm(forms.ModelForm):
    class Meta:
        model = VideoChannel
        fields = ("link", "team", "highlights_only", "active")

    def clean_link(self):
        """Kiungo kinathibitishwa na YouTube (ombi 1) — chaneli isiyopo au kiungo kibaya kinakataliwa."""
        from news import youtube
        link = self.cleaned_data["link"].strip()
        if not youtube.key():
            raise forms.ValidationError("Weka YOUTUBE_API_KEY kwenye Render kwanza.")
        try:
            self._resolved = youtube.resolve(link)
        except youtube.YouTubeError as exc:
            raise forms.ValidationError(str(exc))
        dup = VideoChannel.objects.filter(channel_id=self._resolved[0]).exclude(pk=self.instance.pk)
        if dup.exists():
            raise forms.ValidationError(f"Chaneli hii tayari imeongezwa: {dup.first()}")
        return link

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.channel_id, obj.name = self._resolved
        obj.checked_at = None                    # isomwe kwenye mzunguko unaofuata
        if commit:
            obj.save()
        return obj


@admin.register(VideoChannel)
class VideoChannelAdmin(admin.ModelAdmin):
    """Chaneli RASMI za YouTube (klabu, ligi, shirikisho). Bandika kiungo — jina na namba vinajazwa vyenyewe."""
    form = VideoChannelForm
    list_display = ("__str__", "team", "highlights_only", "active", "checked_at", "error")
    list_editable = ("active",)
    readonly_fields = ("name", "channel_id", "checked_at", "error")


@admin.register(ChannelPost)
class ChannelPostAdmin(admin.ModelAdmin):
    """Posti za WhatsApp Channel ya Soka360 (zinatumwa zenyewe — news/soka360.py)."""
    list_display = ("title", "kind", "posted_at", "tries", "error")
    list_filter = ("kind",)
    search_fields = ("title", "key")
    readonly_fields = ("key", "kind", "title", "posted_at", "tries", "error", "created_at")


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    """Kurasa za ligi (/ligi/<slug>/) — zinaundwa zenyewe kutoka ratiba; badilisha jina/utangulizi hapa."""
    list_display = ("name", "country", "season", "rank", "standings_at", "active")
    list_editable = ("rank", "active")
    search_fields = ("name", "competition", "country")
    readonly_fields = ("competition_id", "competition", "country", "season", "standings_at", "standings_note")


@admin.register(MatchResult)
class MatchResultAdmin(admin.ModelAdmin):
    """Hifadhi ya matokeo inayofundisha utabiri (news/results.py) — inajazwa yenyewe."""
    list_display = ("played_at", "competition", "home", "home_goals", "away_goals", "away", "season")
    list_filter = ("competition", "season")
    search_fields = ("home", "away", "competition")
    date_hierarchy = "played_at"


@admin.register(ModelReport)
class ModelReportAdmin(admin.ModelAdmin):
    """Ripoti za utabiri: backtest (uaminifu na uzito wa modeli) na maendeleo ya kupakua historia."""
    list_display = ("key", "updated_at")
    readonly_fields = ("key", "data", "updated_at")


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    """Wahariri halisi (/mwandishi/<slug>/). Ukimuunganisha na akaunti yake, habari anazochapisha zinapata jina lake."""
    list_display = ("name", "role", "user", "active")
    list_editable = ("active",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(HeroPhoto)
class HeroPhotoAdmin(admin.ModelAdmin):
    """Picha zinazobadilishana nyuma ya vichwa vya kurasa za michezo. Picha unazomiliki au zenye leseni tu."""
    list_display = ("thumb", "caption", "credit", "active", "sort")
    list_display_links = ("caption",)
    list_editable = ("active", "sort")

    @admin.display(description="Picha")
    def thumb(self, obj):
        return format_html('<img src="{}" alt="" width="96" height="54" style="object-fit:cover;border-radius:6px" '
                           'referrerpolicy="no-referrer">', obj.image)
