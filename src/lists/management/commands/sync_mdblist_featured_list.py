from dataclasses import replace

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from lists.featured import engine, registry
from lists.featured.adapters import mdblist as mdblist_adapter
from lists.featured.registry import FeaturedList, Guards
from lists.featured.types import GuardError

poster_url = mdblist_adapter.poster_url
UNCHANGED = "the existing list was not changed."


def external_list_id(value):
    """Return an MDBList external-list ID from an ID or public URL."""
    candidate = mdblist_adapter.external_list_id(value)
    if candidate is None:
        raise CommandError("External list must be an MDBList list ID or URL.")
    return candidate


def definition_for(list_id, source_url, featured_position):
    """Return the registry definition for this external list, or a one-off one."""
    definition = registry.by_identity("mdblist", list_id)
    if definition is None:
        definition = FeaturedList(
            key=f"mdblist-external-{list_id}",
            name="",
            media_types=("movie", "tv"),
            kind="community",
            position=featured_position,
            adapter="mdblist",
            params={"external": list_id},
            cadence=None,
            source_url=source_url,
            identity=("mdblist", list_id),
            guards=Guards(require_exact_count=True, min_resolve_rate=0.95),
        )
    return replace(definition, position=max(featured_position, 0))


def legacy_message(result):
    """Return the command's original wording for guard failures."""
    error = result.error
    if isinstance(error, GuardError) and error.reason in ("empty", "zero_resolved"):
        return f"MDBList returned no importable TMDB items; {UNCHANGED}"
    if isinstance(error, GuardError) and error.reason == "exact_count":
        return (
            f"MDBList reported {error.details['expected']} items but returned "
            f"{error.details['actual']} unique TMDB items; {UNCHANGED}"
        )
    return result.message


class Command(BaseCommand):
    help = "Synchronize one MDBList external list into a featured native Spine list"

    def add_arguments(self, parser):
        parser.add_argument("external_list")
        parser.add_argument("--owner", required=True)
        parser.add_argument("--featured-position", type=int, default=1)

    def handle(self, *args, **options):  # noqa: ARG002
        list_id = external_list_id(options["external_list"])
        source_url = options["external_list"] if "://" in options["external_list"] else ""
        try:
            owner = get_user_model().objects.get(username=options["owner"])
        except get_user_model().DoesNotExist as error:
            raise CommandError(f"Spine user {options['owner']!r} does not exist.") from error

        definition = definition_for(list_id, source_url, options["featured_position"])
        result = engine.run(definition, owner, trigger="command")
        if not result.ok:
            raise CommandError(legacy_message(result))

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {result.stored_count} items into featured list {result.custom_list.name!r} "
                f"owned by {owner.username}."
            )
        )
