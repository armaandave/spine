"""Export, then import into an empty account (AN-802, N-30)."""

import io

from django.contrib.auth import get_user_model

from integrations import exports
from integrations.imports.yamtrack import YamtrackImporter


def round_trip(user):
    """Export the user's data and import it into a new account; return (header, account)."""
    lines = list(exports.generate_rows(user))
    other = get_user_model().objects.create_user(username=f"{user.username}-copy", password="!")
    file = io.BytesIO("".join(lines).encode())
    YamtrackImporter(file, other, "new").import_data()
    return lines[0], other
