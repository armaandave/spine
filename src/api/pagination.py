from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    """Default page-number pagination for API collections."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
