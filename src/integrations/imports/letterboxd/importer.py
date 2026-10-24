import logging
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from app import single_weight
from app.models import DiaryEntry, Item, MediaLike, MediaTypes, Movie, Sources, Status
from app.providers import tmdb
from integrations.imports.letterboxd.parser import parse_export
from integrations.imports.letterboxd.resolver import LetterboxdResolver
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from a Letterboxd ZIP export."""
    return LetterboxdImporter(file, user, mode).import_data()


class LetterboxdImporter:
    """Import Letterboxd export data into Spine movies."""

    def __init__(self, file, user, mode, resolver=None):
        self.file = file
        self.user = user
        self.mode = mode
        self.resolver = resolver or LetterboxdResolver()
        self.warnings = []
        self.counts = defaultdict(int)
        self.items_by_tmdb_id = {}
        self.resolved_by_film = {}

    def import_data(self):
        """Import the export."""
        export = parse_export(self.file)
        rows = list(export.rows_with_uris())
        resolved = self.resolver.resolve_rows(rows)
        self.resolved_by_film = self._film_key_index(rows, resolved)

        with transaction.atomic():
            if self.mode == "overwrite":
                self._cleanup()
            liked_ids = self._liked_item_ids(export.likes, resolved)
            already_liked = set(
                MediaLike.objects.filter(user=self.user, item_id__in=liked_ids).values_list("item_id", flat=True),
            )
            self._import_history(export.diary, export.reviews, liked_ids, resolved)
            self._import_watched(export.watched, resolved)
            self._import_ratings(export.ratings, resolved)
            self._import_watchlist(export.watchlist, resolved)
            self._import_lists(export.lists, resolved)
            self._import_likes(export.likes, resolved, already_liked)

        warnings = "\n".join(dict.fromkeys(self.warnings))
        return dict(self.counts), warnings if warnings else None

    def _cleanup(self):
        imported_entries = list(DiaryEntry.objects.filter(
            user=self.user,
            item__media_type=MediaTypes.MOVIE.value,
            import_source="letterboxd",
        ))
        for entry in imported_entries:
            single_weight.delete_log(self.user, entry)
        CustomList.objects.filter(
            owner=self.user,
            import_source="letterboxd",
        ).delete()

    def _liked_item_ids(self, rows, resolved):
        liked_ids = set()
        for row in rows:
            item = self._item_for(row, resolved)
            if item:
                liked_ids.add(item.id)
        return liked_ids

    def _import_history(self, diary_rows, review_rows, liked_ids, resolved):
        rows_by_id = {}
        for row in diary_rows:
            rows_by_id[row["source_id"]] = {**row, "from_diary": True, "from_review": False}
        for row in review_rows:
            combined = rows_by_id.setdefault(
                row["source_id"],
                {**row, "from_diary": False, "from_review": True},
            )
            combined["from_review"] = True
            for field in ("rating", "review", "rewatch", "tags"):
                if row.get(field) not in (None, "", []):
                    combined[field] = row[field]

        rows_by_item = defaultdict(list)
        item_by_id = {}
        for row in rows_by_id.values():
            item = self._item_for(row, resolved)
            if not item or row.get("date") is None:
                continue
            item_by_id[item.id] = item
            rows_by_item[item.id].append(
                {
                    "source_id": row["source_id"],
                    "source_order": row["source_order"],
                    "consumed_at": row["date"],
                    "rating": row.get("rating"),
                    "review": row.get("review", ""),
                    # The export says which films are liked, not which viewings.
                    "liked": item.id in liked_ids,
                    "is_rewatch": row.get("rewatch"),
                    "tags": row.get("tags", []),
                    "from_diary": row["from_diary"],
                    "from_review": row["from_review"],
                },
            )

        for item_id, rows in rows_by_item.items():
            created = single_weight.import_logs(
                self.user,
                item_by_id[item_id],
                rows,
                source="letterboxd",
            )
            created_ids = {entry.import_source_id for entry in created}
            for row in rows:
                if row["source_id"] not in created_ids:
                    continue
                self.counts["diary"] += int(row["from_diary"])
                self.counts["reviews"] += int(row["from_review"])

    def _import_watched(self, rows, resolved):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            existed = Movie.objects.filter(user=self.user, item=item, direct_consumption=True).exists()
            single_weight.import_title_state(self.user, item)
            if not existed:
                self.counts[MediaTypes.MOVIE.value] += 1

    def _import_ratings(self, rows, resolved):
        for row in rows:
            if row.get("rating") is None:
                continue
            item = self._item_for(row, resolved)
            if not item:
                continue
            single_weight.import_title_state(
                self.user,
                item,
                rating=row["rating"],
            )
            self.counts["ratings"] += 1

    def _import_watchlist(self, rows, resolved):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            movie = Movie.objects.filter(user=self.user, item=item).first()
            if movie and movie.status == Status.COMPLETED.value:
                continue
            _, changed = self._ensure_movie(item, Status.PLANNING.value, row.get("date"))
            if changed:
                self.counts["watchlist"] += 1

    def _import_lists(self, lists, resolved):
        for letterboxd_list in lists:
            custom_list, created = CustomList.objects.get_or_create(
                owner=self.user,
                name=letterboxd_list.name,
                import_source="letterboxd",
                defaults={
                    "slug": f"letterboxd-{slugify(letterboxd_list.name)}"[:255],
                    "description": letterboxd_list.description,
                    "tags": letterboxd_list.tags,
                    "visibility": CustomList.Visibility.PRIVATE,
                },
            )
            fields = []
            if not created and letterboxd_list.description:
                custom_list.description = letterboxd_list.description
                fields.append("description")
            if not created and letterboxd_list.tags:
                custom_list.tags = letterboxd_list.tags
                fields.append("tags")
            if fields:
                custom_list.save(update_fields=fields)
            for row in letterboxd_list.rows:
                item = self._item_for(row, resolved)
                if not item:
                    continue
                _, item_created = CustomListItem.objects.get_or_create(
                    custom_list=custom_list,
                    item=item,
                    defaults={"position": row.get("position")},
                )
                if item_created:
                    self.counts["list_items"] += 1
            self.counts["lists"] += int(created)

    def _import_likes(self, rows, resolved, already_liked):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            single_weight.import_title_state(self.user, item, liked=True)
            if item.id not in already_liked:
                already_liked.add(item.id)
                self.counts["likes"] += 1

    def _film_key(self, row):
        name = (row.get("name") or "").strip().casefold()
        if not name:
            return None
        return (name, row.get("year"))

    def _film_key_index(self, rows, resolved):
        index = {}
        for row in rows:
            result = resolved.get(row.get("uri"))
            if not result:
                continue
            key = self._film_key(row)
            if key:
                index[key] = result
        return index

    def _item_for(self, row, resolved):
        result = resolved.get(row.get("uri"))
        if not result:
            result = self.resolved_by_film.get(self._film_key(row))
        if not result:
            self._warn_unresolved(row)
            return None
        tmdb_id = str(result["tmdb_id"])
        if tmdb_id not in self.items_by_tmdb_id:
            self.items_by_tmdb_id[tmdb_id] = self._get_or_create_item(tmdb_id, row)
        return self.items_by_tmdb_id[tmdb_id]

    def _get_or_create_item(self, tmdb_id, row):
        try:
            metadata = tmdb.movie(tmdb_id)
        except Exception as error:
            logger.warning("Could not fetch TMDB movie %s: %s", tmdb_id, error)
            metadata = {}
        return Item.objects.update_or_create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": metadata.get("title") or row.get("name") or tmdb_id,
                "image": metadata.get("image") or settings.IMG_NONE,
            },
        )[0]

    def _ensure_movie(self, item, status, when=None, rating=None):
        movie, created = Movie.objects.get_or_create(
            user=self.user,
            item=item,
            defaults={
                "status": status,
                "progress": 1 if status == Status.COMPLETED.value else 0,
                "end_date": when if status == Status.COMPLETED.value else None,
                "score": rating,
            },
        )
        if created:
            return movie, True
        fields = []
        if status == Status.COMPLETED.value and movie.status != Status.COMPLETED.value:
            movie.status = status
            movie.progress = 1
            fields.extend(["status", "progress"])
        if status == Status.COMPLETED.value and when and not movie.end_date:
            movie.end_date = when
            fields.append("end_date")
        if rating is not None and movie.score is None:
            movie.score = rating
            fields.append("score")
        if fields:
            movie.save(update_fields=fields)
        return movie, bool(fields)

    def _warn_unresolved(self, row):
        label = row.get("name") or row.get("uri") or "Unknown film"
        self.warnings.append(f"{label}: Couldn't resolve Letterboxd film to TMDB")
