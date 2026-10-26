import unicodedata

from django.db import migrations, models


def _search_text(title, artist):
    # A copy of search_rank.normalize_search_text, so later changes there
    # don't change this migration.
    chars = []
    for char in unicodedata.normalize("NFKD", f"{title} {artist}"):
        if unicodedata.combining(char):
            continue
        category = unicodedata.category(char)
        chars.append(char.casefold() if category[0] in {"L", "N"} else " ")
    return f" {' '.join(''.join(chars).split())} "


def fill_search_text(apps, _):
    """Fill the new column for albums saved before it existed."""
    MusicGenreAlbum = apps.get_model("app", "MusicGenreAlbum")
    albums = list(MusicGenreAlbum.objects.only("id", "title", "artist"))
    for album in albums:
        album.search_text = _search_text(album.title, album.artist)
    MusicGenreAlbum.objects.bulk_update(albums, ["search_text"], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0084_fill_empty_ratings_from_rated_logs"),
    ]

    operations = [
        migrations.AddField(
            model_name="musicgenrealbum",
            name="search_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(fill_search_text, reverse_code=migrations.RunPython.noop),
    ]
