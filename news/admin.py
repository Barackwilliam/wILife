from django import forms
from django.contrib import admin
from django.utils.html import format_html

from news.models import (Article, ChannelPost, EditorSettings, Fixture, FootballState, Highlight, JamiiTekService, Team, VideoChannel, LiveMatch, LiveUpdate,
                         NewsBatch, NewsRequest, Subscriber, EmailSubscriber, DailyCount)


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


@admin.register(Fixture)
class FixtureAdmin(admin.ModelAdmin):
    """Ratiba ya siku (kurasa za Mechi za leo / Matokeo ya jana)."""
    list_display = ("day", "kickoff", "competition", "home", "away", "home_goals", "away_goals", "status")
    list_filter = ("day", "competition")
    search_fields = ("home", "away", "competition")


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


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    """Kurasa za timu (/timu/<slug>/). Utambulisho (intro) unaandikwa hapa; data nyingine ni automatic."""
    list_display = ("name", "slug", "active", "sort")
    list_editable = ("active", "sort")
    prepopulated_fields = {"slug": ("name",)}


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
