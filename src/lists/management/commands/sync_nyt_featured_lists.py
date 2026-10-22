from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from app.providers import hardcover, nytbooks, openlibrary  # noqa: F401 - stable patch targets
from lists.featured import engine, registry, resolve
from lists.featured.adapters import nyt as nyt_adapter
from lists.featured.resolve import Resolver
from lists.featured.types import GuardError

LIST_TAGS = ["NYT Best Sellers", "Books"]


def _resolve_book(book):
    """Return the first Hardcover, then Open Library, match for a book's ISBNs."""
    return resolve.lookup_isbns(book["isbns"])


def _featured_definition(definition):
    featured = registry.by_identity("nyt", definition["slug"])
    if featured is None:
        raise CommandError(f"{definition['slug']} is not a registered featured list.")
    return featured


def _legacy_error(result, chart_name):
    if isinstance(result.error, GuardError) and result.error.reason in ("empty", "zero_resolved"):
        return f"{chart_name}: no books resolved. Existing contents were preserved."
    return result.message


def _sync_chart(owner, definition, chart):
    """Write one already-fetched chart, matching by ISBN only (the pre-engine behavior).

    Kept for callers of the original helper; ``handle`` runs the full engine instead.
    Returns ``(custom_list, stored_count, resolved_count)``.
    """
    featured = _featured_definition(definition)
    result = engine.run(
        featured,
        owner,
        trigger="command",
        fetch_result=nyt_adapter.fetch_result_from_chart(chart),
        resolver=Resolver(
            featured,
            isbn_lookup=lambda isbns: _resolve_book({"isbns": isbns}),
            max_title_searches=0,
        ),
    )
    if not result.ok:
        raise CommandError(_legacy_error(result, chart["name"]))
    return result.custom_list, result.stored_count, result.resolved_count


class Command(BaseCommand):
    help = "Synchronize the approved NYT Best Sellers charts into featured Spine lists"

    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True)

    def handle(self, *args, **options):  # noqa: ARG002
        try:
            owner = get_user_model().objects.get(username=options["owner"])
        except get_user_model().DoesNotExist as error:
            raise CommandError(f"Spine user {options['owner']!r} does not exist.") from error

        failures = []
        for definition in nytbooks.FEATURED_CHARTS:
            result = engine.run(_featured_definition(definition), owner, trigger="command")
            if not result.ok:
                chart_name = (result.fetch.name if result.fetch else None) or definition["fallback_name"]
                error = _legacy_error(result, chart_name)
                failures.append((definition["slug"], error))
                self.stderr.write(
                    self.style.ERROR(f"{definition['fallback_name']}: {error}"),
                )
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"Synced {result.stored_count} ranked items into {result.custom_list.name!r} "
                    f"({result.resolved_count}/{len(result.fetch.entries)} NYT entries resolved).",
                ),
            )

        if failures:
            failed = ", ".join(slug for slug, _ in failures)
            raise CommandError(
                f"{len(failures)} of {len(nytbooks.FEATURED_CHARTS)} NYT charts failed: {failed}.",
            )
