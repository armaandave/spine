"""IGDB routes for the game adapter tests (``api_routes`` values)."""

TOKEN_URL = "id.twitch.tv/oauth2/token"
TOKEN = {"access_token": "test-token", "expires_in": 3600}


def igdb_game(igdb_id, name, released=None, cover=None):
    """Return an IGDB ``games`` row."""
    row = {"id": igdb_id, "name": name}
    if released is not None:
        row["first_release_date"] = released
    if cover:
        row["cover"] = {"id": 1, "image_id": cover}
    return row


def external(uid, game, source=1):
    """Return an IGDB ``external_games`` row."""
    return {"id": 1, "uid": str(uid), "game": game, "external_game_source": source}


def igdb_routes(*, games=(), externals=(), extra=None):
    """Routes for an IGDB token, ``games`` and ``external_games`` (first key found wins)."""
    return {
        **(extra or {}),
        TOKEN_URL: TOKEN,
        "v4/external_games": list(externals),
        "v4/games": list(games),
    }


def bodies(api, fragment):
    """Return the raw request bodies sent to URLs containing ``fragment``."""
    return [call.kwargs.get("data") for call in api.call_args_list if fragment in call.args[2]]
