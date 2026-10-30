import json

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Jaribu odds za Highlightly mara moja (ombi 1): onyesha jibu, idadi ya mechi na zilizoeleweka, na uzihifadhi."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (chaguo-msingi: leo, saa za Afrika Mashariki)")

    def handle(self, *args, date=None, **opts):
        from django.conf import settings
        from news import football, highlightly, odds
        now = timezone.now()
        day = date or timezone.localtime(now).date().isoformat()
        state = football._state(now)
        try:
            data = highlightly._get("/odds", {"date": day, "timezone": settings.TIME_ZONE, "oddsType": "prematch",
                                              "limit": odds.PAGE, "offset": 0}, state)
        except Exception as exc:
            state.save(update_fields=["requests", "remaining"])
            self.stdout.write(self.style.ERROR(f"Kosa: {exc}"))
            return
        state.save(update_fields=["requests", "remaining"])
        rows = (data.get("data") if isinstance(data, dict) else data) or []
        found = odds.parse(rows)
        odds.save_sample(data, rows, now, day)
        stored = odds._store(found, now) if found else 0
        self.stdout.write(f"Siku {day}: mechi {len(rows)} kwenye jibu, zilizoeleweka {len(found)}, "
                          f"zilizohifadhiwa kwenye ratiba yetu {stored}.")
        if isinstance(data, dict) and data.get("pagination"):
            self.stdout.write(f"pagination: {json.dumps(data['pagination'])}")
        self.stdout.write("Mfano wa jibu (pia Admin -> Model reports -> odds-sample):")
        self.stdout.write(json.dumps(rows[0] if rows else data, ensure_ascii=False, indent=1, default=str)[:4000])
