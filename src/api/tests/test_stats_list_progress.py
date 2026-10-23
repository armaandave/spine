from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from api.services import stats
from app.models import (
    Book,
    CustomPosterPreference,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem, PersonListItem

# The subject's completed set takes one query per user-owned media type plus
# episodes. On top of that come the lists, the per-list type counts, the
# completed members, the poster rows, and the two viewer and two curator
# artwork prefetches.
LIST_PROGRESS_QUERIES = 18


class ListProgressTests(TestCase):
    """Profile list progress: the same values, at a fixed query count."""

    def setUp(self):
        users = get_user_model().objects
        self.spine = users.create_user(username="spine")
        self.subject = users.create_user(username="subject")
        self.viewer = users.create_user(username="viewer")
        self.request = RequestFactory().get("/api/v1/stats/me/summary/")
        self.counter = 0

    def item(self, media_type=MediaTypes.MOVIE.value, source=Sources.TMDB.value):
        self.counter += 1
        return Item.objects.create(
            media_id=str(self.counter),
            media_type=media_type,
            source=source,
            title=f"{media_type} {self.counter}",
            image=f"https://example.com/{self.counter}.jpg",
        )

    def make_list(self, name, items, *, owner=None, featured=True, visibility=CustomList.Visibility.PUBLIC):
        custom_list = CustomList.objects.create(
            owner=owner or self.spine,
            name=name,
            is_featured=featured,
            visibility=visibility,
        )
        CustomListItem.objects.bulk_create([
            CustomListItem(custom_list=custom_list, item=item, position=index)
            for index, item in enumerate(items, start=1)
        ])
        return custom_list

    def complete(self, *items):
        Movie.objects.bulk_create([
            Movie(user=self.subject, item=item, status=Status.COMPLETED.value) for item in items
        ])

    def progress(self, viewer=None, subject=None):
        # A fresh user object, so the completed-set cache starts empty.
        subject = subject or get_user_model().objects.get(pk=self.subject.pk)
        return stats._list_progress(user=subject, viewer=viewer or subject, request=self.request)

    def test_values_match_the_list_contract(self):
        films = [self.item() for _ in range(4)]
        self.complete(films[3], films[1])
        Movie.objects.bulk_create([Movie(user=self.subject, item=films[0], status=Status.PLANNING.value)])
        CustomPosterPreference.objects.create(
            user=self.subject,
            item=films[1],
            custom_image_url="https://example.com/custom.jpg",
        )
        top = self.make_list("Top films", films)
        book = self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value)
        Book.objects.bulk_create([Book(user=self.subject, item=book, status=Status.COMPLETED.value)])
        mixed = self.make_list("across", [films[3], book])
        own = self.make_list("My private list", [films[3]], owner=self.subject, featured=False,
                             visibility=CustomList.Visibility.PRIVATE)
        self.make_list("Hidden featured", films, visibility=CustomList.Visibility.PRIVATE)
        self.make_list("Someone else's", films, owner=self.viewer, featured=False)
        self.make_list("Empty", [])
        people = CustomList.objects.create(
            owner=self.spine,
            name="Nobel",
            is_featured=True,
            visibility=CustomList.Visibility.PUBLIC,
            list_type=CustomList.ListType.PEOPLE,
        )
        PersonListItem.objects.create(custom_list=people, source="tmdb", person_id="1", name="A")

        progress = self.progress()

        self.assertEqual(
            progress,
            [
                {
                    "id": mixed.id,
                    "name": "across",
                    "media_type": None,
                    "poster_urls": ["https://example.com/4.jpg", "https://example.com/5.jpg"],
                    "completion": {"completed_count": 2, "total_count": 2},
                },
                {
                    "id": own.id,
                    "name": "My private list",
                    "media_type": MediaTypes.MOVIE.value,
                    "poster_urls": ["https://example.com/4.jpg"],
                    "completion": {"completed_count": 1, "total_count": 1},
                },
                {
                    "id": top.id,
                    "name": "Top films",
                    "media_type": MediaTypes.MOVIE.value,
                    "poster_urls": [
                        "https://example.com/1.jpg",
                        "https://example.com/custom.jpg",
                        "https://example.com/3.jpg",
                    ],
                    "completion": {"completed_count": 2, "total_count": 4},
                },
            ],
        )
        self.assertEqual(
            [row["id"] for row in self.progress(viewer=self.viewer)],
            [mixed.id, top.id],
        )

    def test_featured_lists_need_one_completed_item(self):
        films = [self.item() for _ in range(3)]
        self.complete(films[0])
        started = self.make_list("Started", films)
        self.make_list("Untouched", films[1:])
        own = self.make_list("My untouched list", films[1:], owner=self.subject, featured=False)
        shared = self.make_list("Shared featured", films[1:])
        shared.collaborators.add(self.subject)

        progress = self.progress()

        self.assertEqual(
            [(row["name"], row["completion"]["completed_count"]) for row in progress],
            [("Started", 1), ("My untouched list", 0), ("Shared featured", 0)],
        )
        self.assertNotIn("Untouched", [row["name"] for row in progress])
        self.assertEqual({row["id"] for row in progress}, {started.id, own.id, shared.id})

    @override_settings(MUSIC_ENABLED=False)
    def test_hidden_media_types_are_not_counted(self):
        film = self.item()
        song = self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value)
        self.complete(film)
        self.make_list("Songs", [song])
        self.make_list("Film and song", [song, film])

        progress = self.progress()

        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(progress[0]["completion"], {"completed_count": 1, "total_count": 1})
        self.assertEqual(progress[0]["poster_urls"], ["https://example.com/1.jpg"])

    def test_query_count_does_not_grow_with_lists(self):
        films = [self.item() for _ in range(12)]
        self.complete(*films[:5])

        def add_lists(start, count):
            for index in range(start, start + count):
                self.make_list(f"List {index:03}", films[index % 4:])

        add_lists(0, 5)
        subject = get_user_model().objects.get(pk=self.subject.pk)
        with CaptureQueriesContext(connection) as few:
            self.assertEqual(len(self.progress(subject=subject)), 5)
        add_lists(5, 60)
        subject = get_user_model().objects.get(pk=self.subject.pk)
        with self.assertNumQueries(LIST_PROGRESS_QUERIES):
            progress = self.progress(subject=subject)

        self.assertEqual(len(few.captured_queries), LIST_PROGRESS_QUERIES)
        self.assertEqual(len(progress), stats.PROGRESS_COLLECTION_LIMIT)
        self.assertEqual(progress[0]["completion"], {"completed_count": 5, "total_count": 12})
