from django.contrib import admin
from django.utils.html import format_html

from news.models import Article, JamiiTekService, NewsBatch


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "status", "published_at", "views", "view_link")
    list_filter = ("status", "category")
    search_fields = ("title", "excerpt", "keywords")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("views", "created_at", "updated_at", "batch", "slot")
    actions = ["publish_selected"]

    @admin.display(description="Open")
    def view_link(self, obj):
        return format_html('<a href="{}" target="_blank">↗</a>', obj.get_absolute_url())

    @admin.action(description="Publish selected")
    def publish_selected(self, request, queryset):
        for a in queryset.exclude(status="published"):
            a.publish()


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
