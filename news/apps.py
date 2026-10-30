from django.apps import AppConfig


class NewsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "news"
    verbose_name = "Habari"

    def ready(self):
        from django.db.models.signals import post_delete, post_save
        from news import crests
        from news.models import Team
        post_save.connect(crests.forget, sender=Team, dispatch_uid="crests_team_save")
        post_delete.connect(crests.forget, sender=Team, dispatch_uid="crests_team_delete")
        from django.core.cache import cache
        from news.models import HeroPhoto
        forget_photos = lambda *a, **k: cache.delete("hero:photos")
        post_save.connect(forget_photos, sender=HeroPhoto, dispatch_uid="hero_photo_save", weak=False)
        post_delete.connect(forget_photos, sender=HeroPhoto, dispatch_uid="hero_photo_delete", weak=False)
