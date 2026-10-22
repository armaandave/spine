from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from lists.featured import engine, registry
from lists.featured.resolve import Resolver
from lists.featured.types import AdapterError


class Command(BaseCommand):
    help = (
        "Sync one featured list by registry key and print its entries, resolved counts "
        "and unresolved rows. --dry-run fetches and resolves without writing."
    )

    def add_arguments(self, parser):
        parser.add_argument("key", help="Registry key, for example letterboxd-top-500")
        parser.add_argument("--dry-run", action="store_true", help="Fetch and resolve only")
        parser.add_argument("--owner", default=engine.OWNER_USERNAME)
        parser.add_argument(
            "--show",
            type=int,
            default=0,
            help="Print only the first N entries (default: all)",
        )

    def handle(self, *args, **options):  # noqa: ARG002
        definition = registry.get(options["key"])
        if definition is None:
            known = ", ".join(item.key for item in registry.all_definitions()[:20])
            raise CommandError(f"Unknown featured list {options['key']!r}. Known keys include: {known}")
        owner = get_user_model().objects.filter(username=options["owner"]).first()
        if owner is None and not options["dry_run"]:
            raise CommandError(f"Spine user {options['owner']!r} does not exist.")

        resolver = Resolver(definition)
        if options["dry_run"]:
            try:
                result = engine.dry_run(definition, owner, resolver=resolver)
            except AdapterError as error:
                raise CommandError(f"{definition.key}: {error}") from error
        else:
            result = engine.run(definition, owner, trigger="command", resolver=resolver)

        self._print(definition, result, resolver, options["show"])
        if result.status not in ("dry-run", "deferred", *engine.SUCCESS_STATUSES):
            raise CommandError(f"{definition.key}: {result.status}: {result.message}")

    def _print(self, definition, result, resolver, show):
        fetch = result.fetch
        if fetch is None:
            return
        self.stdout.write(
            f"{definition.name} ({definition.key}) via {definition.adapter}: "
            f"{len(fetch.entries)} entries"
            + (f", edition {fetch.edition!r}" if fetch.edition else "")
            + (f", source reports {fetch.expected_count}" if fetch.expected_count else ""),
        )
        rows = result.resolutions[:show] if show else result.resolutions
        for resolution in rows:
            entry = resolution.entry
            label = f"#{entry.rank}" if entry.rank is not None else "-"
            year = f" ({entry.year})" if entry.year else ""
            creator = f" / {entry.creator}" if entry.creator else ""
            if resolution.skipped:
                outcome = "SKIPPED by override"
            elif resolution.match is not None:
                outcome = f"{resolution.match.ref} [{resolution.how}]"
            else:
                outcome = f"UNRESOLVED ({resolution.reason or 'no match'})"
            self.stdout.write(f"  {label:>5} {entry.title}{year}{creator} -> {outcome}  {{{entry.key}}}")
        counted = [resolution for resolution in result.resolutions if not resolution.skipped]
        resolved = sum(1 for resolution in counted if resolution.match is not None)
        rate = f"{resolved / len(counted):.1%}" if counted else "n/a"
        self.stdout.write(
            f"Resolved {resolved}/{len(counted)} ({rate}); {result.stored_count} unique items; "
            f"{len(counted) - resolved} unresolved; {resolver.title_searches} title searches.",
        )
        unresolved = result.unresolved
        if unresolved:
            self.stdout.write("Unresolved:")
            for row in unresolved:
                self.stdout.write(
                    f"  #{row['rank']} {row['title']} ({row['year'] or '?'}) "
                    f"{row['creator']} {{{row['key']}}} {row['reason']}".rstrip(),
                )
        if result.status == "dry-run":
            verdict = result.message or "guards pass"
            self.stdout.write(self.style.WARNING(f"Dry run, nothing written. {verdict}"))
        elif result.ok:
            list_id = result.custom_list.pk if result.custom_list is not None else None
            self.stdout.write(self.style.SUCCESS(f"Status {result.status}; list {list_id}."))
        elif result.status == "deferred":
            message = result.message or "some entries wait for a provider's quota"
            self.stdout.write(self.style.WARNING(f"Deferred: {message}"))
