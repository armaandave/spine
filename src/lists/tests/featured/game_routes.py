"""IGDB routes for the game adapter tests (``api_routes`` values)."""

import requests

from app.models import Sources

TOKEN_URL = "id.twitch.tv/oauth2/token"
TOKEN = {"access_token": "test-token", "expires_in": 3600}
TOKEN_KEY = f"{Sources.IGDB.value}_access_token"  # igdb.get_access_token's cache key


def http_error(status):
    """Return the error ``services.api_request`` raises for HTTP ``status``."""
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


def rejected_once(payload):
    """A route that answers HTTP 401 (Twitch revoked the token) once, then ``payload``."""
    calls = []

    def respond(url, params):  # noqa: ARG001
        calls.append(url)
        if len(calls) == 1:
            raise http_error(401)
        return payload

    return respond


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
