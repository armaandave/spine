import json

from django.core.management.base import BaseCommand

from lists.featured import health


class Command(BaseCommand):
    help = "Print the featured-lists health report (the same data as /api/v1/lists/featured/health/)"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print the report as JSON")

    def handle(self, *args, **options):  # noqa: ARG002
        data = health.report()
        if options["json"]:
            self.stdout.write(json.dumps(data, indent=2))
        else:
            self.stdout.write(health.format_text(data))
