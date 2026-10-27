"""Shared API view mixins."""

from app import exposure, music_catalog


class MediaExposureMixin:
    """Reject client media types hidden by the rollout policy.

    Music albums in the path are served under their canonical ref, so an old
    MusicBrainz ref of an album that moved reaches the Item that holds it now.
    """

    def dispatch(self, request, *args, **kwargs):
        # Here, not in initial(): DRF hands initial() a copy the handler never sees.
        if {"source", "media_type", "media_id"} <= kwargs.keys():
            kwargs = music_catalog.canonical_media_ref(kwargs)
        return super().dispatch(request, *args, **kwargs)

    def initial(self, request, *args, **kwargs):
        media_type = kwargs.get("media_type") or request.query_params.get("media_type")
        if media_type is None and request.method != "GET":
            media_type = request.data.get("media_type")
        exposure.require_media_type(media_type)
        return super().initial(request, *args, **kwargs)
