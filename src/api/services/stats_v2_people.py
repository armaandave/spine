"""Stats v2 people: the directors, cast, creators, artists and authors you logged most.

docs/stats-contract.md, STAT-219.

Every function takes an api.services.stats_scope.StatsScope. build_people returns the
section on the movie, TV, music and book tabs and None everywhere else (STAT-302):
each group's first 10 people by titles (``rows``) and by rating (``rated_rows``);
people_page serves the show-all endpoint (STAT-310): page is 1-based and it returns
``{"count": int, "results": [...]}``.

A person's titles are the scope's titles (STAT-105, TV folded to the show) that
credit them in the role (app.models.ItemCredit, stored by store_item_metadata). Each
title's rating is its title rating (STAT-109, STAT-205): ``rated`` counts the titles
that have one and ``average_stars`` is their average.

One SQL query per response, for every role and both orders: a materialized CTE
holds the scope's titles with their rating, worked out once per title (per credit row
it would run 15 times per film for the cast), and drives the join into the credits'
(role, item) index; the credits are grouped by person and ranked within each role by
window functions, by titles and by rating. Python only formats the rows sent.
"""

from django.db import connection
from django.db.models import OuterRef, Subquery

from api.services.stats_scope import stars
from api.services.stats_v2_ratings import STORED_RATING, highest_rated_queryset
from app.models import ItemCredit, MediaTypes

# Each tab's groups, in the order the app shows them (STAT-219).
GROUPS = {
    MediaTypes.MOVIE.value: (
        (ItemCredit.Role.DIRECTOR.value, "Directors"),
        (ItemCredit.Role.CAST.value, "Cast"),
    ),
    MediaTypes.TV.value: (
        (ItemCredit.Role.CAST.value, "Cast"),
        (ItemCredit.Role.CREATOR.value, "Creators"),
    ),
    MediaTypes.MUSIC.value: ((ItemCredit.Role.ARTIST.value, "Artists"),),
    MediaTypes.BOOK.value: ((ItemCredit.Role.AUTHOR.value, "Authors"),),
}
PREVIEW_LIMIT = 10
# "Highest rated" ranks only people with this many rated titles.
MIN_RATED = 3
SORT_TITLES = "titles"
SORT_RATING = "rating"
SORTS = (SORT_TITLES, SORT_RATING)


def roles_for(media_type):
    """Return the roles a tab shows, in order; empty off the people tabs."""
    return [role for role, _label in GROUPS.get(media_type, ())]


def build_people(scope):
    """Build each group's top 10 by titles and by rating, and its total, or None."""
    groups = GROUPS.get(scope.media_type)
    if groups is None:
        return None
    window = (0, PREVIEW_LIMIT)
    by_role = _people_rows(scope, [role for role, _label in groups], titles_window=window, rating_window=window)
    return {
        "groups": [
            {
                "role": role,
                "label": label,
                "rows": by_role[role][SORT_TITLES][0],
                "rated_rows": by_role[role][SORT_RATING][0],
                "total": by_role[role][SORT_TITLES][1],
            }
            for role, label in groups
        ],
    }


def people_page(scope, role, sort, page, page_size):
    """Return one page of a role's people, by titles or by rating (STAT-219, 310)."""
    window = ((page - 1) * page_size, page * page_size)
    windows = {"titles_window": window} if sort == SORT_TITLES else {"rating_window": window}
    rows, total = _people_rows(scope, [role], **windows)[role][sort]
    return {"count": total, "results": rows}


def _people_rows(scope, roles, *, titles_window=(0, 0), rating_window=(0, 0)):
    """Return ``{role: {sort: (formatted rows, how many people the order lists)}}``.

    Each window is a (start, end) slice of one order, 0-based, end excluded:

    - ``titles``: everyone, by titles, then average stars (none last), then name;
    - ``rating``: people with at least MIN_RATED rated titles, by average stars, then
      titles, then name.

    Person source and ID break any tie. Every role and both orders come from one
    query, so the scope's titles are worked out once.
    """
    titles_sql, titles_params = _rated_titles(scope).query.sql_with_params()
    credit = connection.ops.quote_name(ItemCredit._meta.db_table)
    params = [*titles_params, *roles, *titles_window, *rating_window]
    rated_enough = f"rated >= {MIN_RATED:d}"
    # The titles drive the join: one (role, item) index probe per title. Credits
    # are unique per (item, person, role), so COUNT(*) counts distinct titles.
    sql = f"""
        WITH t (id, title_rating) AS MATERIALIZED ({titles_sql}),
        people AS (
            SELECT
                c.role AS person_role,
                c.person_source AS person_source,
                c.person_id AS person_id,
                MAX(c.name) AS person_name,
                MAX(c.image_url) AS image_url,
                COUNT(*) AS titles,
                COUNT(t.title_rating) AS rated,
                AVG(t.title_rating) AS average
            FROM t
            JOIN {credit} c ON c.item_id = t.id AND c.role IN ({", ".join(["%s"] * len(roles))})
            GROUP BY c.role, c.person_source, c.person_id
        ),
        ranked AS (
            SELECT
                people.*,
                ROW_NUMBER() OVER (
                    PARTITION BY person_role
                    ORDER BY titles DESC, (average IS NULL), average DESC, person_name, person_source, person_id
                ) AS by_titles,
                COUNT(*) OVER (PARTITION BY person_role) AS titles_total,
                CASE WHEN {rated_enough} THEN ROW_NUMBER() OVER (
                    PARTITION BY person_role, ({rated_enough})
                    ORDER BY average DESC, titles DESC, person_name, person_source, person_id
                ) END AS by_rating,
                SUM(CASE WHEN {rated_enough} THEN 1 ELSE 0 END) OVER (PARTITION BY person_role) AS rating_total
            FROM people
        )
        SELECT
            person_role, person_source, person_id, person_name, image_url, titles, rated, average,
            by_titles, titles_total, by_rating, rating_total
        FROM ranked
        WHERE (by_titles > %s AND by_titles <= %s) OR (by_rating > %s AND by_rating <= %s)
    """  # noqa: S608 - only quoted names, placeholders and Django's own compiled SQL are inlined
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        fetched = cursor.fetchall()
    ranked = {role: {SORT_TITLES: [], SORT_RATING: []} for role in roles}
    totals = {role: {SORT_TITLES: 0, SORT_RATING: 0} for role in roles}
    for (
        role, person_source, person_id, name, image_url, titles, rated, average,
        by_titles, titles_total, by_rating, rating_total,
    ) in fetched:
        row = {
            "person": {"source": person_source, "id": person_id, "name": name, "image_url": image_url or None},
            "titles": titles,
            "rated": rated,
            "average_stars": stars(average),
        }
        if titles_window[0] < by_titles <= titles_window[1]:
            ranked[role][SORT_TITLES].append((by_titles, row))
        if by_rating is not None and rating_window[0] < by_rating <= rating_window[1]:
            ranked[role][SORT_RATING].append((by_rating, row))
        totals[role] = {SORT_TITLES: titles_total, SORT_RATING: int(rating_total)}
    # Past an order's last person there is no row to read its total from: a 404 page.
    return {
        role: {
            sort: ([row for _rank, row in sorted(rows, key=lambda pair: pair[0])], totals[role][sort] if rows else 0)
            for sort, rows in orders.items()
        }
        for role, orders in ranked.items()
    }


def _rated_titles(scope):
    """Return the scope's titles (STAT-105) as (id, STAT-205 title rating or None)."""
    rated = highest_rated_queryset(scope).order_by().filter(id=OuterRef("id")).values("title_rating")[:1]
    return scope.titles.annotate(title_rating=Subquery(rated, output_field=STORED_RATING)).values("id", "title_rating")
