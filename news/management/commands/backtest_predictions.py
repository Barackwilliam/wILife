import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Pima utabiri kwa mechi zilizopita (walk-forward) na uhifadhi ripoti + uzito bora wa modeli."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=120, help="Kipindi cha majaribio (siku za mwisho)")
        parser.add_argument("--collect", action="store_true", help="Kusanya matokeo yaliyopo kwanza (bure)")

    def handle(self, *args, days, collect, **opts):
        from news import backtest, results
        if collect:
            self.stdout.write(f"matokeo mapya: {results.collect()}")
        data = backtest.run(days=days)
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
