"""AN-705: an anime goes by its English title wherever Spine names it."""

from unittest import mock

import requests

from app import anime_listing, anime_titles, anime_tracking
from app.models import Item
from app.providers import anilist, services
from app.tests.anime_fixtures import AnimeTestCase
from app.tests.tv_fixtures import ProviderDown


def enrichment(english):
    """Return AniList's page enrichment for a title with this English title, or none."""
    return {"titles": {"english": english, "romaji": "Shingeki no Kyojin", "native": "進撃の巨人"}}


class FillTitlesTests(AnimeTestCase):
    """AN-705: a job fills every anime Spine has no title for, from AniList in bulk."""

    def anime(self, *numbers):
        return [
            Item.objects.create(
                source="mal",
                media_type="anime",
                media_id=str(number),
                title=f"Original {number}",
                image="https://example.com/p.jpg",
            )
            for number in numbers
        ]

    def answer(self, *, missing=(), failing=()):
        def respond(provider, method, url, params=None, **kwargs):
            ids = params["variables"]["malIds"]
            if set(ids) & set(failing):
                raise ProviderDown
            media = [
                {"idMal": number, "title": {"english": f"English {number}" if number % 2 else None}}
                for number in ids
                if number not in missing
            ]
            return {"data": {"Page": {"media": media}}}

        return respond

    def run_fill(self, **answer):
        pauses = []
        with mock.patch.object(services, "api_request", side_effect=self.answer(**answer)) as request:
            result = anime_titles.fill_missing(sleep=pauses.append)
        return result, [call.kwargs["params"]["variables"]["malIds"] for call in request.call_args_list], pauses

    def shown(self, number):
        return Item.objects.get(media_type="anime", media_id=str(number)).shown_title

    def test_an_705_every_anime_with_none_fills_25_a_request_paced(self):
        self.anime(*range(1, 31))
        _, asked, pauses = self.run_fill(missing={30})
        self.assertEqual([len(ids) for ids in asked], [25, 5])
        self.assertEqual(pauses, [anime_titles.FILL_PACE])
        self.assertEqual(self.shown(1), "English 1")
        self.assertEqual(self.shown(2), "Original 2", "AniList has no English title for it")
        self.assertEqual(self.shown(30), "Original 30", "AniList doesn't have it")
        _, asked, _ = self.run_fill()
        self.assertEqual(asked, [], "Every one has been read")

    def test_an_705_a_failed_request_costs_only_its_own_titles(self):
        self.anime(*range(1, 31))
        _, asked, _ = self.run_fill(failing={1})
        self.assertEqual(len(asked), 2)
        self.assertEqual(self.shown(1), "Original 1")
        self.assertEqual(Item.objects.get(media_id="1", media_type="anime").display_title, "")
        self.assertEqual(self.shown(27), "English 27")
        _, asked, _ = self.run_fill()
        self.assertEqual(sorted(asked[0]), list(range(1, 26)), "The next run asks the failed ones again")

    def test_an_705_a_hanging_anilist_ends_the_run(self):
        self.anime(*range(1, 31))
        with mock.patch.object(services, "api_request", side_effect=requests.exceptions.Timeout) as request:
            anime_titles.fill_missing(sleep=lambda _: None)
        self.assertEqual(request.call_count, 1)

    def test_an_705_it_never_asks_for_other_types_manual_titles_or_ones_it_has(self):
        Item.objects.create(source="tmdb", media_type="movie", media_id="1", title="A film", image="https://example.com/p.jpg")
        Item.objects.create(source="manual", media_type="anime", media_id="m-1", title="Mine", image="https://example.com/p.jpg")
        (known,) = self.anime(7)
        known.display_title = "Seven"
        known.save(update_fields=["display_title"])
        _, asked, _ = self.run_fill()
        self.assertEqual(asked, [])

    def test_an_705_the_fill_runs_every_hour(self):
        from django.conf import settings

        from events import tasks

        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULE["fill_anime_titles"],
            {"task": "Fill anime titles", "schedule": 3600},
        )
        with mock.patch.object(anime_titles, "fill_missing", return_value={"asked": 3}) as job:
            result = tasks.fill_anime_titles.delay()
        self.assertEqual(result.get(), {"asked": 3})
        job.assert_called_once_with()


class RefreshTitleTests(AnimeTestCase):
    """AN-705: the copy's reads store it too, at no extra request."""

    def test_an_705_the_job_stores_mals_english_title(self):
        item = self.title(title=self.fake.add(title="Shingeki no Kyojin", english="Attack on Titan", anilist_english="AniList's"))
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(Item.objects.get(pk=item.pk).display_title, "Attack on Titan")

    def test_an_705_with_none_on_mal_a_first_open_stores_anilists(self):
        item = self.title(title=self.fake.add(title="Shingeki no Kyojin", anilist_english="Attack on Titan"))
        anime_listing.get_listing(item)
        self.assertEqual(Item.objects.get(pk=item.pk).display_title, "Attack on Titan")

    def test_an_705_with_mal_down_anilists_only_fills_what_isnt_known(self):
        title = self.fake.add(title="Shingeki no Kyojin", english="Attack on Titan", anilist_english="AniList's")
        item = self.title(title=title)
        anime_listing.get_listing(item)
        self.clock.advance(days=8)
        self.fake.fail("mal")
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(Item.objects.get(pk=item.pk).display_title, "Attack on Titan")

    def test_an_705_the_query_asks_for_the_english_title(self):
        self.assertIn("english", anilist.ANIME_EPISODES_QUERY)


class StoreTitleTests(AnimeTestCase):
    """AN-705: Spine stores the English title whenever it reads MAL or AniList anyway."""

    def open_page(self, item, *, anilist_answer=None):
        with mock.patch.object(anilist, "anime", return_value=anilist_answer or {}):
            response = self.api.get(f"/api/v1/media/mal/anime/{item.media_id}/")
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        return response

    def test_an_705_a_page_open_stores_mals_english_title(self):
        item = self.title(title=self.fake.add(title="Shingeki no Kyojin", english="Attack on Titan"))
        self.open_page(item, anilist_answer=enrichment("AniList's Attack on Titan"))
        self.assertEqual(item.display_title, "Attack on Titan")

    def test_an_705_with_none_on_mal_the_page_stores_anilists(self):
        item = self.title(title=self.fake.add(title="Shingeki no Kyojin"))
        self.open_page(item, anilist_answer=enrichment("Attack on Titan"))
        self.assertEqual(item.display_title, "Attack on Titan")

    def test_an_705_with_none_on_either_the_page_stores_the_original(self):
        item = self.title(title=self.fake.add(title="Shingeki no Kyojin"))
        self.open_page(item, anilist_answer=enrichment(None))
        self.assertEqual((item.display_title, item.shown_title), ("Shingeki no Kyojin", "Shingeki no Kyojin"))

    def test_an_705_with_anilist_down_and_none_on_mal_nothing_is_stored_yet(self):
        item = self.title(title=self.fake.add(title="Shingeki no Kyojin"))
        self.fake.fail("anilist")
        self.open_page(item)
        self.assertEqual(item.display_title, "")

    def test_an_705_the_page_uses_anilists_english_title_not_its_romaji(self):
        item = self.title(title=self.fake.add(title="Shingeki no Kyojin"))
        response = self.open_page(item, anilist_answer={**enrichment(None), "display_title": "Shingeki no Kyojin (romaji)"})
        self.assertNotIn("(romaji)", response.data.get("display_title") or "")

    def test_an_705_a_mal_read_elsewhere_stores_mals_english_title(self):
        from api.services.item_metadata import store_item_metadata
        from app.providers import mal

        item = self.title(title=self.fake.add(title="Shingeki no Kyojin", english="Attack on Titan"))
        store_item_metadata(item, mal.anime(item.media_id))
        item.refresh_from_db()
        self.assertEqual(item.display_title, "Attack on Titan")

    def test_an_705_mals_english_title_replaces_anilists_and_anilists_never_replaces_mals(self):
        from app import anime_titles

        item = self.title(title=self.fake.add(title="Shingeki no Kyojin"))
        anime_titles.store_shown_title(item, anilist="AniList's")
        self.assertEqual(item.display_title, "AniList's")
        anime_titles.store_shown_title(item, mal="Attack on Titan")
        self.assertEqual(item.display_title, "Attack on Titan")
        anime_titles.store_shown_title(item, anilist="AniList's")
        self.assertEqual(Item.objects.get(pk=item.pk).display_title, "Attack on Titan")

    def test_an_705_other_types_and_manual_titles_store_nothing(self):
        from app import anime_titles

        movie = Item.objects.create(source="tmdb", media_type="movie", media_id="1", title="Le Samourai", image="https://example.com/m.jpg")
        manual = Item.objects.create(source="manual", media_type="anime", media_id="m-1", title="Mine", image="https://example.com/m.jpg")
        for item in (movie, manual):
            anime_titles.store_shown_title(item, mal="English", anilist="English")
            self.assertEqual(Item.objects.get(pk=item.pk).display_title, "")
