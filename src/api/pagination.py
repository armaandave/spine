from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    """Default page-number pagination for API collections."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class FeedCursorPagination(CursorPagination):
    """Reverse-chronological cursor pagination for activity feeds."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
    # Activity written in the same instant ties on created_at, and Postgres returns tied
    # rows in any order; id makes the order total, as Activity.Meta.ordering intends.
    ordering = ("-created_at", "-id")

    def get_paginated_response(self, data):
        """Return the cursor format expected by the iOS contract."""
        return Response(
            {
                "next_cursor": self.get_next_link(),
                "previous_cursor": self.get_previous_link(),
                "results": data,
            },
        )
