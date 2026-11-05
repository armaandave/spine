"""AN-705: an anime goes by its English title wherever Spine names it."""

from unittest import mock

from app.models import Item
from app.providers import anilist
from app.tests.anime_fixtures import AnimeTestCase


def enrichment(english):
    """Return AniList's page enrichment for a title with this English title, or none."""
    return {"titles": {"english": english, "romaji": "Shingeki no Kyojin", "native": "進撃の巨人"}}


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
