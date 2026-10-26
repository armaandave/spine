from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Prefetch, Q
from django.utils.text import slugify

from app.models import Item


class CustomListManager(models.Manager):
    """Manager for custom lists."""

    def get_user_lists(self, user):
        """Return the custom lists that the user owns or collaborates on."""
        item_order = [models.F("position").asc(nulls_last=True), "date_added"]
        return (
            self.filter(Q(owner=user) | Q(collaborators=user))
            .select_related("owner")
            .prefetch_related(
                "collaborators",
                Prefetch(
                    "items",
                    queryset=Item.objects.order_by(
                        models.F("customlistitem__position").asc(nulls_last=True),
                        "customlistitem__date_added",
                    ),
                ),
                Prefetch(
                    "customlistitem_set",
                    queryset=CustomListItem.objects.select_related("item").order_by(
                        *item_order,
                    ),
                ),
            )
            .distinct()
        )

    def get_user_lists_with_item(self, user, item):
        """Return user lists with item membership status."""
        return (
            self.filter(
                Q(owner=user) | Q(collaborators=user),
                list_type=CustomList.ListType.MEDIA,
            )
            .annotate(
                has_item=models.Exists(
                    CustomListItem.objects.filter(
                        custom_list_id=models.OuterRef("id"),
                        item=item,
                    ),
                ),
            )
            .prefetch_related("collaborators")
            .distinct()
            .order_by("name")
        )

    def get_user_lists_with_person(self, user, source, person_id):
        """Return user people lists with person membership status."""
        return (
            self.filter(
                Q(owner=user) | Q(collaborators=user),
                list_type=CustomList.ListType.PEOPLE,
            )
            .annotate(
                has_person=models.Exists(
                    PersonListItem.objects.filter(
                        custom_list_id=models.OuterRef("id"),
                        source=source,
                        person_id=person_id,
                    ),
                ),
                person_entry_id=models.Subquery(
                    PersonListItem.objects.filter(
                        custom_list_id=models.OuterRef("id"),
                        source=source,
                        person_id=person_id,
                    ).values("id")[:1],
                ),
            )
            .prefetch_related("collaborators")
            .distinct()
            .order_by("name")
        )


class CustomList(models.Model):
    """Model for custom lists."""

    class ListType(models.TextChoices):
        """Content types supported by custom lists."""

        MEDIA = "media", "Media"
        PEOPLE = "people", "People"

    class Visibility(models.TextChoices):
        """Visibility choices for custom lists."""

        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        PRIVATE = "private", "Private"

    name = models.CharField(max_length=255)
    slug = models.SlugField(
        max_length=255,
        blank=True,
        validators=[
            RegexValidator(
                r"^[a-z0-9-]*$",
                "Use lowercase letters, numbers, and hyphens only.",
            ),
        ],
    )
    description = models.TextField(blank=True, default="")
    tags = models.JSONField(blank=True, default=list)
    list_type = models.CharField(
        max_length=10,
        choices=ListType.choices,
        default=ListType.MEDIA,
        db_default=ListType.MEDIA,
    )
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    is_ranked = models.BooleanField(default=False)
    import_source = models.CharField(max_length=32, blank=True, default="")
    import_source_id = models.CharField(max_length=255, blank=True, default="")
    import_source_url = models.URLField(max_length=2048, blank=True, default="")
    is_featured = models.BooleanField(default=False, db_index=True)
    featured_position = models.PositiveIntegerField(default=0)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="collaborated_lists",
        blank=True,
    )
    items = models.ManyToManyField(
        Item,
        related_name="custom_lists",
        blank=True,
        through="CustomListItem",
    )

    objects = CustomListManager()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for the model."""

        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "slug"],
                condition=~Q(slug=""),
                name="%(app_label)s_customlist_unique_owner_slug",
            ),
            models.UniqueConstraint(
                fields=["owner", "import_source", "import_source_id"],
                condition=~Q(import_source_id=""),
                name="%(app_label)s_customlist_unique_import",
            ),
            models.CheckConstraint(
                condition=Q(list_type__in=["media", "people"]),
                name="%(app_label)s_customlist_valid_list_type",
            ),
        ]

    def __str__(self):
        """Return the name of the custom list."""
        return self.name

    def save(self, *args, **kwargs):
        """Save the list with a default slug for shareable URLs."""
        if not self.slug:
            self.slug = slugify(self.name)[:255]
        super().save(*args, **kwargs)

    def user_can_view(self, user):
        """Check if the user can view the list."""
        return self.owner == user or user in self.collaborators.all()

    def user_can_edit(self, user):
        """Check if the user can edit the list."""
        return self.owner == user or user in self.collaborators.all()

    def user_can_delete(self, user):
        """Check if the user can delete the list."""
        return self.owner == user

    @property
    def image(self):
        """Return the image of the first item in the list."""
        if self.list_type == self.ListType.PEOPLE:
            first_person = self.person_items.first()
            return (
                first_person.profile_url
                if first_person and first_person.profile_url
                else settings.IMG_NONE
            )
        first_item = self.customlistitem_set.select_related("item").first()
        return first_item.item.image if first_item else settings.IMG_NONE


class CustomListItemManager(models.Manager):
    """Manager for custom list items."""

    def get_last_added_date(self, custom_list):
        """Return the last time an item was added to a specific list."""
        try:
            return self.filter(custom_list=custom_list).latest("date_added").date_added
        except self.model.DoesNotExist:
            return None


class CustomListItem(models.Model):
    """Model for items in custom lists."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    custom_list = models.ForeignKey(CustomList, on_delete=models.CASCADE)
    position = models.PositiveIntegerField(null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)
    # Which repeat of the item this row is. People's lists hold an item once (always
    # 0); featured award histories list a work once per win (Succession: 0, 1, 2).
    occurrence = models.PositiveSmallIntegerField(default=0, db_default=0)
    # Featured lists only: what the row shows under the poster ("2023", "▲2 · $12.4M").
    note = models.CharField(max_length=200, blank=True, default="", db_default="")

    objects = CustomListItemManager()

    class Meta:
        """Meta options for the model."""

        ordering = [models.F("position").asc(nulls_last=True), "date_added"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "custom_list", "occurrence"],
                name="%(app_label)s_customlistitem_unique_item_list_occurrence",
            ),
        ]

    def __str__(self):
        """Return the name of the list item."""
        return self.item.title


class PersonListItem(models.Model):
    """Provider-backed person stored in a people list."""

    class Source(models.TextChoices):
        """Sources that currently expose person pages."""

        TMDB = "tmdb", "The Movie Database"
        HARDCOVER = "hardcover", "Hardcover"
        OPENLIBRARY = "openlibrary", "Open Library"
        MUSICBRAINZ = "musicbrainz", "MusicBrainz"
        APPLE_MUSIC = "apple_music", "Apple Music"
        MAL = "mal", "MyAnimeList"
        MANGAUPDATES = "mangaupdates", "MangaUpdates"
        ANILIST = "anilist", "AniList"

    custom_list = models.ForeignKey(
        CustomList,
        on_delete=models.CASCADE,
        related_name="person_items",
    )
    source = models.CharField(max_length=32, choices=Source.choices)
    person_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    profile_url = models.URLField(max_length=2048, blank=True, default="")
    known_for_department = models.CharField(max_length=255, blank=True, default="")
    position = models.PositiveIntegerField(null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta options for people in custom lists."""

        ordering = [
            models.F("position").asc(nulls_last=True),
            "date_added",
            "id",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["custom_list", "source", "person_id"],
                name="%(app_label)s_personlistitem_unique_person_list",
            ),
        ]

    def __str__(self):
        """Return the person's name."""
        return self.name


class FeaturedListSync(models.Model):
    """Sync state and health for one featured-list definition (``lists.featured``)."""

    class Status(models.TextChoices):
        """Outcome of the latest attempt."""

        OK = "ok", "OK"
        UNCHANGED = "unchanged", "Unchanged"
        GUARDED = "guarded", "Guarded"
        FAILED = "failed", "Failed"
        EMPTY = "empty", "Empty"
        # Some entries waited for a provider's daily budget or rate limit.
        DEFERRED = "deferred", "Deferred"

    # Identity
    key = models.SlugField(max_length=128, unique=True)
    custom_list = models.OneToOneField(
        CustomList,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="featured_sync",
    )
    definition_hash = models.CharField(max_length=64, blank=True, default="")
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    # Content
    edition = models.CharField(max_length=255, blank=True, default="")
    content_changed_at = models.DateTimeField(null=True, blank=True)
    live_label = models.CharField(max_length=255, blank=True, default="")
    # Run state
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(
        max_length=16,
        choices=Status.choices,
        blank=True,
        default="",
    )
    last_error = models.TextField(blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    # When a deferred run is retried: the provider's budget reset or 429 wait.
    deferred_until = models.DateTimeField(null=True, blank=True)
    # Counts
    entry_count = models.PositiveIntegerField(default=0)
    resolved_count = models.PositiveIntegerField(default=0)
    unresolved = models.JSONField(blank=True, default=list)
    # Charts only: {"<item_id>": "▲2" | "▼1" | "NEW"} against the previous content.
    item_notes = models.JSONField(blank=True, default=dict)
    # Every list: what each entry shows ("Winner · Best Picture", "▲2 · $12.4M"), keyed
    # by item ID, or by "<source>:person:<id>" in people lists. The API reads media
    # rows' notes from CustomListItem.note (one per row); this per-item copy remains
    # for people lists and for older code.
    entry_notes = models.JSONField(blank=True, default=dict)

    class Meta:
        """Meta options for featured-list sync state."""

        ordering = ["key"]

    def __str__(self):
        """Return the definition key."""
        return self.key


class FeaturedListSnapshot(models.Model):
    """One day's contents of a featured list, written when its content changes."""

    sync = models.ForeignKey(
        FeaturedListSync,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )
    taken_on = models.DateField()
    edition = models.CharField(max_length=255, blank=True, default="")
    fingerprint = models.CharField(max_length=64)
    # [[rank, entry_key, item_id | null], ...]
    entries = models.JSONField(default=list)

    class Meta:
        """Meta options for featured-list snapshots."""

        ordering = ["-taken_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["sync", "taken_on"],
                name="%(app_label)s_featuredlistsnapshot_unique_day",
            ),
        ]

    def __str__(self):
        """Return the list key and date."""
        return f"{self.sync.key} {self.taken_on}"
