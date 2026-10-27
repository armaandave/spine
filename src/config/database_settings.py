def sqlite_database(name):
    """Return the SQLite DATABASES entry, used when DB_HOST isn't set.

    Local and test backends share one SQLite file between the server and its Celery
    worker. Every transaction takes the write lock when it begins (IMMEDIATE), and a
    busy file is waited on for up to 20 seconds: a transaction that reads before it
    writes (an atomic TV write, a ratings save) then waits for the other writer instead
    of failing at once with "database is locked". Production runs PostgreSQL.
    """
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": name,
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
    }
